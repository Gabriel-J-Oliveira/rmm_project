param(
    [string]$KeyId = "nightowl-release-2026-02",
    [string]$PrivateKeyPath = (Join-Path $env:USERPROFILE ".nightowl\release-signing\nightowl-release-2026-02-private.xml"),
    [string]$PublicKeysPath = (Join-Path $env:USERPROFILE ".nightowl\release-public-keys.json"),
    [switch]$Force,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-release-keygen] {0}" -f $Message)
}

function Write-Utf8NoBomText([string]$Path, [string]$Content) {
    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-Utf8NoBomJson([string]$Path, $Value, [int]$Depth = 8) {
    Write-Utf8NoBomText -Path $Path -Content ($Value | ConvertTo-Json -Depth $Depth)
}

function Assert-RsaPssProviderAvailable {
    if ($null -eq ("System.Security.Cryptography.RSACng" -as [type])) {
        throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: Provedor CNG RSA indisponivel neste Windows/PowerShell. RSA-PSS e obrigatorio."
    }
}

function New-RsaCngInstance {
    Assert-RsaPssProviderAvailable
    try {
        return New-Object System.Security.Cryptography.RSACng
    }
    catch {
        throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: Falha ao criar provedor CNG RSA para RSA-PSS. Detalhe: $($_.Exception.Message)"
    }
}

function Import-RsaParametersFromXml([string]$Xml, [bool]$IncludePrivateParameters) {
    $legacyProvider = $null
    try {
        $legacyProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider
        $legacyProvider.PersistKeyInCsp = $false
        $legacyProvider.FromXmlString($Xml)
        return $legacyProvider.ExportParameters($IncludePrivateParameters)
    }
    catch {
        $purpose = if ($IncludePrivateParameters) { "privada" } else { "publica" }
        throw "RELEASE_SIGNING_KEY_INVALID: Nao foi possivel importar a chave RSA XML $purpose. Detalhe: $($_.Exception.Message)"
    }
    finally {
        if ($null -ne $legacyProvider) {
            $legacyProvider.PersistKeyInCsp = $false
            $legacyProvider.Clear()
            $legacyProvider.Dispose()
        }
    }
}

function New-RsaPssPrivateKeyFromXmlText([string]$Xml) {
    $parameters = Import-RsaParametersFromXml -Xml $Xml -IncludePrivateParameters $true
    $rsa = New-RsaCngInstance
    try {
        $rsa.ImportParameters($parameters)
        return $rsa
    }
    catch {
        $rsa.Dispose()
        throw "RELEASE_SIGNING_KEY_INVALID: Chave privada RSA XML nao pode ser usada pelo provedor CNG/RSA-PSS. Detalhe: $($_.Exception.Message)"
    }
}

function New-RsaPssPublicKeyFromXmlText([string]$Xml) {
    $parameters = Import-RsaParametersFromXml -Xml $Xml -IncludePrivateParameters $false
    $rsa = New-RsaCngInstance
    try {
        $rsa.ImportParameters($parameters)
        return $rsa
    }
    catch {
        $rsa.Dispose()
        throw "RELEASE_SIGNING_KEY_INVALID: Chave publica RSA XML nao pode ser usada pelo provedor CNG/RSA-PSS. Detalhe: $($_.Exception.Message)"
    }
}

function Assert-PublicXmlHasNoPrivateParameters([string]$PublicXml) {
    foreach ($privateElement in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
        if ($PublicXml -match ("<{0}>" -f [regex]::Escape($privateElement))) {
            throw "RELEASE_PUBLIC_KEY_EXPORT_UNSAFE: XML publico contem parametro privado $privateElement."
        }
    }
}

function Assert-RsaPssKeyPairMatches([string]$PrivateXml, [string]$PublicXml) {
    $privateRsa = New-RsaPssPrivateKeyFromXmlText -Xml $PrivateXml
    $publicRsa = New-RsaPssPublicKeyFromXmlText -Xml $PublicXml
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes("nightowl-release-keygen-selftest")
        $signature = $privateRsa.SignData(
            $bytes,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pss
        )
        $valid = $publicRsa.VerifyData(
            $bytes,
            $signature,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pss
        )
        if (-not $valid) {
            throw "RELEASE_PUBLIC_KEY_MISMATCH: A chave publica gerada nao valida assinatura da chave privada."
        }
        $tampered = [byte[]]$bytes.Clone()
        $tampered[0] = $tampered[0] -bxor 0x01
        $tamperedValid = $publicRsa.VerifyData(
            $tampered,
            $signature,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pss
        )
        if ($tamperedValid) {
            throw "RELEASE_KEYGEN_SELFTEST_FAILED: Assinatura aceitou dados alterados."
        }
    }
    finally {
        $publicRsa.Dispose()
        $privateRsa.Dispose()
    }
}

function Protect-PrivateKeyFile([string]$Path) {
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier "S-1-5-18"
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetOwner($currentUser)
    $acl.SetAccessRuleProtection($true, $false)
    $rights = [System.Security.AccessControl.FileSystemRights]::FullControl
    $inheritance = [System.Security.AccessControl.InheritanceFlags]::None
    $propagation = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($currentUser, $rights, $inheritance, $propagation, $allow)))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, $rights, $inheritance, $propagation, $allow)))
    [System.IO.File]::SetAccessControl($Path, $acl)
}

function Assert-PrivateKeyAcl([string]$Path) {
    $acl = [System.IO.File]::GetAccessControl($Path)
    if (-not $acl.AreAccessRulesProtected) {
        throw "RELEASE_PRIVATE_KEY_ACL_UNSAFE: heranca de ACL ainda esta habilitada."
    }
    $allowed = @(
        [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
        "S-1-5-18"
    )
    foreach ($rule in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
            throw "RELEASE_PRIVATE_KEY_ACL_UNSAFE: regra nao-Allow encontrada em $($rule.IdentityReference.Value)."
        }
        if ($allowed -notcontains $rule.IdentityReference.Value) {
            throw "RELEASE_PRIVATE_KEY_ACL_UNSAFE: identidade nao autorizada na chave privada: $($rule.IdentityReference.Value)."
        }
    }
}

function New-NightOwlReleaseSigningKey([string]$RequestedKeyId, [string]$PrivatePath, [string]$PublicPath) {
    if ([string]::IsNullOrWhiteSpace($RequestedKeyId)) {
        throw "KeyId obrigatorio."
    }
    foreach ($path in @($PrivatePath, $PublicPath)) {
        if ((Test-Path $path) -and -not $Force) {
            throw "Arquivo ja existe: $path. Use -Force para substituir conscientemente."
        }
    }

    $provider = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
    try {
        $provider.PersistKeyInCsp = $false
        $privateXml = $provider.ToXmlString($true)
        $publicXml = $provider.ToXmlString($false)
    }
    finally {
        $provider.PersistKeyInCsp = $false
        $provider.Clear()
        $provider.Dispose()
    }

    Assert-PublicXmlHasNoPrivateParameters -PublicXml $publicXml
    Assert-RsaPssKeyPairMatches -PrivateXml $privateXml -PublicXml $publicXml

    Write-Utf8NoBomText -Path $PrivatePath -Content $privateXml
    Protect-PrivateKeyFile -Path $PrivatePath
    Assert-PrivateKeyAcl -Path $PrivatePath

    $payload = [ordered]@{
        keys = @(
            [ordered]@{
                key_id = $RequestedKeyId
                algorithm = "RSA-PSS-SHA256"
                public_key_xml = $publicXml
                status = "active"
                valid_from = (Get-Date).ToUniversalTime().ToString("O")
                valid_until = ""
                revoked_at = ""
            }
        )
    }
    Write-Utf8NoBomJson -Path $PublicPath -Value $payload -Depth 8
    $content = Get-Content -Raw -Path $PublicPath
    foreach ($privateElement in @("<P>", "<Q>", "<DP>", "<DQ>", "<InverseQ>", "<D>")) {
        if ($content.Contains($privateElement)) {
            throw "RELEASE_PUBLIC_KEYS_JSON_INVALID: release-public-keys.json contem parametro privado $privateElement."
        }
    }
    $json = $content | ConvertFrom-Json
    if (@($json.keys).Count -ne 1 -or $json.keys[0].key_id -ne $RequestedKeyId -or $json.keys[0].algorithm -ne "RSA-PSS-SHA256") {
        throw "RELEASE_PUBLIC_KEYS_JSON_INVALID: formato inesperado no release-public-keys.json."
    }
}

function Invoke-SelfTest {
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-release-keygen-selftest-{0}" -f ([guid]::NewGuid().ToString("N")))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $privatePath = Join-Path $temp "nightowl-release-2026-02-private.xml"
        $publicPath = Join-Path $temp "release-public-keys.json"
        New-NightOwlReleaseSigningKey -RequestedKeyId "nightowl-release-2026-02" -PrivatePath $privatePath -PublicPath $publicPath
        $privateContent = Get-Content -Raw -Path $privatePath
        if (-not $privateContent.Contains("<D>")) {
            throw "SelfTest falhou: chave privada gerada nao contem parametro privado esperado."
        }
        $publicContent = Get-Content -Raw -Path $publicPath
        if ($publicContent.Contains("<D>") -or $publicContent.Contains("<P>") -or $publicContent.Contains("<Q>")) {
            throw "SelfTest falhou: arquivo publico contem parametros privados."
        }
        Assert-PrivateKeyAcl -Path $privatePath
        Write-Step "SelfTest OK: RSA 3072 gerado, JSON publico seguro e ACL restritiva validados."
    }
    finally {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($SelfTest) {
    Invoke-SelfTest
    return
}

New-NightOwlReleaseSigningKey -RequestedKeyId $KeyId -PrivatePath $PrivateKeyPath -PublicPath $PublicKeysPath
Write-Step "Chave privada criada em: $PrivateKeyPath"
Write-Step "release-public-keys.json criado em: $PublicKeysPath"
Write-Step "Key ID: $KeyId"
