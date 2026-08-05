param(
    [string]$ExistingPublicKeysPath,
    [string]$NewPublicKeysPath = (Join-Path $env:USERPROFILE ".nightowl\release-public-keys.json"),
    [string]$OutputPath = (Join-Path $env:USERPROFILE ".nightowl\release-public-keys-2026-01-2026-02.json"),
    [string]$SignaturePath = (Join-Path $env:USERPROFILE ".nightowl\release-public-keys-2026-01-2026-02.sig"),
    [string]$SigningKeyPath = $env:NIGHTOWL_RELEASE_ROTATION_SIGNING_KEY,
    [string]$SigningKeyId = "nightowl-release-2026-01",
    [switch]$AllowUnsignedTestBundle,
    [switch]$Force,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) { Write-Host ("[nightowl-key-bundle] {0}" -f $Message) }

function Write-Utf8NoBomText([string]$Path, [string]$Content) {
    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory)) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-Utf8NoBomJson([string]$Path, $Value, [int]$Depth = 8) {
    Write-Utf8NoBomText -Path $Path -Content ($Value | ConvertTo-Json -Depth $Depth)
}

function Assert-RsaPssProviderAvailable {
    if ($null -eq ("System.Security.Cryptography.RSACng" -as [type])) {
        throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: Provedor CNG RSA indisponivel. RSA-PSS e obrigatorio."
    }
}

function New-RsaCngInstance {
    Assert-RsaPssProviderAvailable
    try { return New-Object System.Security.Cryptography.RSACng }
    catch { throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: Falha ao criar RSACng. Detalhe: $($_.Exception.Message)" }
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
        throw "RELEASE_SIGNING_KEY_INVALID: Nao foi possivel importar chave RSA XML $purpose. Detalhe: $($_.Exception.Message)"
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
        throw "ROTATION_SIGNING_KEY_MISSING: chave privada antiga nao encontrada. Sem ela nao ha bootstrap criptografico de producao."
    }
    $rsa = New-RsaCngInstance
    try {
        $rsa.ImportParameters((Import-RsaParametersFromXml -Xml (Get-Content -Raw -Path $Path) -IncludePrivateParameters $true))
        return $rsa
    }
    catch {
        $rsa.Dispose()
        throw
    }
}

function Assert-PublicXmlHasNoPrivateParameters([string]$PublicXml, [string]$KeyId) {
    foreach ($privateElement in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
        if ($PublicXml -match ("<{0}>" -f [regex]::Escape($privateElement))) {
            throw "RELEASE_PUBLIC_KEY_EXPORT_UNSAFE: key_id $KeyId contem parametro privado $privateElement."
        }
    }
}

function Read-KeyBundle([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) { throw "Arquivo de chaves nao encontrado: $Path" }
    try { return Get-Content -Raw -Path $Path | ConvertFrom-Json }
    catch { throw "RELEASE_PUBLIC_KEYS_JSON_INVALID: $Path invalido. Detalhe: $($_.Exception.Message)" }
}

function New-NormalizedKeyEntry($Item) {
    $keyId = [string]$Item.key_id
    $algorithm = if ([string]::IsNullOrWhiteSpace($Item.algorithm)) { "RSA-PSS-SHA256" } else { [string]$Item.algorithm }
    $publicXml = [string]$Item.public_key_xml
    if ([string]::IsNullOrWhiteSpace($keyId)) { throw "RELEASE_PUBLIC_KEYS_JSON_INVALID: key_id vazio." }
    if ($algorithm -ne "RSA-PSS-SHA256") { throw "RELEASE_PUBLIC_KEYS_JSON_INVALID: algoritmo nao permitido para $keyId." }
    if ([string]::IsNullOrWhiteSpace($publicXml)) { throw "RELEASE_PUBLIC_KEYS_JSON_INVALID: public_key_xml vazio para $keyId." }
    Assert-PublicXmlHasNoPrivateParameters -PublicXml $publicXml -KeyId $keyId
    return [ordered]@{
        key_id = $keyId
        algorithm = $algorithm
        public_key_xml = $publicXml
        status = if ([string]::IsNullOrWhiteSpace($Item.status)) { "active" } else { [string]$Item.status }
        valid_from = if ($null -eq $Item.valid_from) { "" } else { [string]$Item.valid_from }
        valid_until = if ($null -eq $Item.valid_until) { "" } else { [string]$Item.valid_until }
        revoked_at = if ($null -eq $Item.revoked_at) { "" } else { [string]$Item.revoked_at }
    }
}

function Merge-KeyBundles([string]$ExistingPath, [string]$NewPath) {
    $map = @{}
    foreach ($bundle in @((Read-KeyBundle $ExistingPath), (Read-KeyBundle $NewPath))) {
        foreach ($item in @($bundle.keys)) {
            $entry = New-NormalizedKeyEntry $item
            if ($map.ContainsKey($entry.key_id) -and $map[$entry.key_id].public_key_xml -ne $entry.public_key_xml) {
                throw "RELEASE_PUBLIC_KEYS_JSON_INVALID: key_id duplicado com material diferente: $($entry.key_id)."
            }
            $map[$entry.key_id] = $entry
        }
    }
    if (-not $map.ContainsKey("nightowl-release-2026-01")) { throw "Bundle precisa conter nightowl-release-2026-01." }
    if (-not $map.ContainsKey("nightowl-release-2026-02")) { throw "Bundle precisa conter nightowl-release-2026-02." }
    return [ordered]@{ keys = @($map.Values | Sort-Object { $_.key_id }) }
}

function Sign-Bundle([string]$BundlePath, [string]$DestinationSignaturePath, [string]$PrivateKeyPath) {
    $rsa = New-RsaPssPrivateKeyFromXml $PrivateKeyPath
    try {
        $bytes = [System.IO.File]::ReadAllBytes($BundlePath)
        $signature = $rsa.SignData($bytes, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
        Write-Utf8NoBomText -Path $DestinationSignaturePath -Content ([Convert]::ToBase64String($signature))
    }
    finally { $rsa.Dispose() }
}

function Invoke-SelfTest {
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-key-bundle-selftest-{0}" -f ([guid]::NewGuid().ToString("N")))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $oldProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
        $newProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
        try {
            $oldPrivate = Join-Path $temp "old-private.xml"
            $oldPublic = Join-Path $temp "old.json"
            $newPublic = Join-Path $temp "new.json"
            Write-Utf8NoBomText -Path $oldPrivate -Content $oldProvider.ToXmlString($true)
            Write-Utf8NoBomJson -Path $oldPublic -Value ([ordered]@{ keys = @([ordered]@{ key_id = "nightowl-release-2026-01"; algorithm = "RSA-PSS-SHA256"; public_key_xml = $oldProvider.ToXmlString($false); status = "active"; valid_from = ""; valid_until = ""; revoked_at = "" }) })
            Write-Utf8NoBomJson -Path $newPublic -Value ([ordered]@{ keys = @([ordered]@{ key_id = "nightowl-release-2026-02"; algorithm = "RSA-PSS-SHA256"; public_key_xml = $newProvider.ToXmlString($false); status = "active"; valid_from = ""; valid_until = ""; revoked_at = "" }) })
            $bundlePath = Join-Path $temp "release-public-keys.json"
            $sigPath = Join-Path $temp "release-public-keys.sig"
            $bundle = Merge-KeyBundles -ExistingPath $oldPublic -NewPath $newPublic
            Write-Utf8NoBomJson -Path $bundlePath -Value $bundle -Depth 8
            Sign-Bundle -BundlePath $bundlePath -DestinationSignaturePath $sigPath -PrivateKeyPath $oldPrivate
            if (-not (Test-Path $sigPath)) { throw "SelfTest falhou: assinatura nao criada." }
            Write-Step "SelfTest OK: bundle 2026-01+2026-02 gerado e assinado pela chave antiga."
        }
        finally {
            $oldProvider.Clear(); $oldProvider.Dispose()
            $newProvider.Clear(); $newProvider.Dispose()
        }
    }
    finally { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}

function Get-FileSha256([string]$Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            return [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
        }
        finally { $stream.Dispose() }
    }
    finally { $sha.Dispose() }
}

if ($SelfTest) { Invoke-SelfTest; return }

foreach ($path in @($OutputPath, $SignaturePath)) {
    if ((Test-Path $path) -and -not $Force) { throw "Arquivo ja existe: $path. Use -Force para substituir conscientemente." }
}

$bundle = Merge-KeyBundles -ExistingPath $ExistingPublicKeysPath -NewPath $NewPublicKeysPath
Write-Utf8NoBomJson -Path $OutputPath -Value $bundle -Depth 8
$sha = Get-FileSha256 $OutputPath
Write-Step "Bundle gerado em: $OutputPath"
Write-Step "SHA-256: $sha"

if ([string]::IsNullOrWhiteSpace($SigningKeyPath)) {
    if (-not $AllowUnsignedTestBundle) {
        throw "ROTATION_SIGNING_KEY_MISSING: a chave privada 2026-01 e necessaria para assinar o bundle. Sem ela, use apenas bootstrap manual de laboratorio com -AllowUnsignedTestBundle."
    }
    Write-Step "ATENCAO: bundle sem assinatura criado apenas para laboratorio. Nao usar em producao."
    return
}

if ($SigningKeyId -ne "nightowl-release-2026-01") {
    throw "ROTATION_SIGNER_INVALID: o bundle de bootstrap deve ser assinado por nightowl-release-2026-01."
}
Sign-Bundle -BundlePath $OutputPath -DestinationSignaturePath $SignaturePath -PrivateKeyPath $SigningKeyPath
Write-Step "Assinatura destacada gerada em: $SignaturePath"
