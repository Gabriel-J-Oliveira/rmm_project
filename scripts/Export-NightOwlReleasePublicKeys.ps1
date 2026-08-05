param(
    [string]$SigningKeyPath = $env:NIGHTOWL_RELEASE_SIGNING_KEY,
    [string]$SigningKeyId = $env:NIGHTOWL_RELEASE_SIGNING_KEY_ID,
    [string]$OutputPath = (Join-Path $env:USERPROFILE ".nightowl\release-public-keys.json"),
    [switch]$Force,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-release-keys] {0}" -f $Message)
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

function New-RsaPssPrivateKeyFromXml([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) {
        throw "Chave privada de assinatura nao encontrada. Defina NIGHTOWL_RELEASE_SIGNING_KEY ou informe -SigningKeyPath."
    }
    $parameters = Import-RsaParametersFromXml -Xml (Get-Content -Raw -Path $Path) -IncludePrivateParameters $true
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

function Export-RsaPublicXmlFromPrivateXmlFile([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) {
        throw "Chave privada de assinatura nao encontrada. Defina NIGHTOWL_RELEASE_SIGNING_KEY ou informe -SigningKeyPath."
    }
    $legacyProvider = $null
    try {
        $legacyProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider
        $legacyProvider.PersistKeyInCsp = $false
        $legacyProvider.FromXmlString((Get-Content -Raw -Path $Path))
        return $legacyProvider.ToXmlString($false)
    }
    catch {
        throw "RELEASE_SIGNING_KEY_INVALID: Nao foi possivel extrair chave publica da chave privada RSA XML. Detalhe: $($_.Exception.Message)"
    }
    finally {
        if ($null -ne $legacyProvider) {
            $legacyProvider.PersistKeyInCsp = $false
            $legacyProvider.Clear()
            $legacyProvider.Dispose()
        }
    }
}

function Assert-PublicXmlHasNoPrivateParameters([string]$PublicXml) {
    foreach ($privateElement in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
        if ($PublicXml -match ("<{0}>" -f [regex]::Escape($privateElement))) {
            throw "RELEASE_PUBLIC_KEY_EXPORT_UNSAFE: XML publico contem parametro privado $privateElement."
        }
    }
}

function Assert-RsaPssKeyPairMatches([string]$PrivateKeyPath, [string]$PublicXml) {
    $privateRsa = New-RsaPssPrivateKeyFromXml -Path $PrivateKeyPath
    $publicRsa = New-RsaPssPublicKeyFromXmlText -Xml $PublicXml
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes("nightowl-release-public-key-selftest")
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
            throw "RELEASE_PUBLIC_KEY_MISMATCH: A chave publica exportada nao valida assinatura da chave privada."
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
            throw "RELEASE_PUBLIC_KEY_SELFTEST_FAILED: Assinatura aceitou dados alterados."
        }
    }
    finally {
        $publicRsa.Dispose()
        $privateRsa.Dispose()
    }
}

function Export-NightOwlReleasePublicKeys([string]$PrivateKeyPath, [string]$KeyId, [string]$DestinationPath) {
    if ([string]::IsNullOrWhiteSpace($KeyId)) {
        throw "SigningKeyId obrigatorio. Defina NIGHTOWL_RELEASE_SIGNING_KEY_ID ou informe -SigningKeyId."
    }
    if ((Test-Path $DestinationPath) -and -not $Force) {
        throw "Arquivo ja existe: $DestinationPath. Use -Force para substituir conscientemente."
    }
    $publicXml = Export-RsaPublicXmlFromPrivateXmlFile -Path $PrivateKeyPath
    Assert-PublicXmlHasNoPrivateParameters -PublicXml $publicXml
    Assert-RsaPssKeyPairMatches -PrivateKeyPath $PrivateKeyPath -PublicXml $publicXml
    $payload = [ordered]@{
        keys = @(
            [ordered]@{
                key_id = $KeyId
                algorithm = "RSA-PSS-SHA256"
                public_key_xml = $publicXml
                status = "active"
                valid_from = (Get-Date).ToUniversalTime().ToString("O")
                valid_until = ""
                revoked_at = ""
            }
        )
    }
    Write-Utf8NoBomJson -Path $DestinationPath -Value $payload -Depth 8
    $json = Get-Content -Raw -Path $DestinationPath | ConvertFrom-Json
    if (@($json.keys).Count -ne 1 -or $json.keys[0].key_id -ne $KeyId) {
        throw "RELEASE_PUBLIC_KEYS_JSON_INVALID: Arquivo gerado nao possui o formato esperado."
    }
}

function Invoke-SelfTest {
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-release-keys-selftest-{0}" -f ([guid]::NewGuid().ToString("N")))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $privatePath = Join-Path $temp "private.xml"
        $outputPath = Join-Path $temp "release-public-keys.json"
        $legacyProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider 2048
        try {
            $legacyProvider.PersistKeyInCsp = $false
            Write-Utf8NoBomText -Path $privatePath -Content $legacyProvider.ToXmlString($true)
        }
        finally {
            $legacyProvider.PersistKeyInCsp = $false
            $legacyProvider.Clear()
            $legacyProvider.Dispose()
        }
        Export-NightOwlReleasePublicKeys -PrivateKeyPath $privatePath -KeyId "nightowl-selftest-key" -DestinationPath $outputPath
        $content = Get-Content -Raw -Path $outputPath
        foreach ($privateElement in @("<P>", "<Q>", "<DP>", "<DQ>", "<InverseQ>", "<D>")) {
            if ($content.Contains($privateElement)) {
                throw "SelfTest falhou: arquivo publico contem $privateElement."
            }
        }
        Write-Step "SelfTest OK: release-public-keys.json gerado com chave publica RSA-PSS valida e sem parametros privados."
    }
    finally {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($SelfTest) {
    Invoke-SelfTest
    return
}

Export-NightOwlReleasePublicKeys -PrivateKeyPath $SigningKeyPath -KeyId $SigningKeyId -DestinationPath $OutputPath
Write-Step "Arquivo publico gerado em: $OutputPath"
Write-Step "Key ID: $SigningKeyId"
