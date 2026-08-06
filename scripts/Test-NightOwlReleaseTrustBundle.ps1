param(
    [string]$BundleDir = "",
    [string]$RootPublicKeyXmlPath = "",
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) { Write-Host "[nightowl-trust-test] $Message" }

function Get-Sha256Hex([string]$Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try { return ([BitConverter]::ToString($sha.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()) }
        finally { $stream.Dispose() }
    }
    finally { $sha.Dispose() }
}

function Assert-NoBom([string]$Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "TRUST_JSON_BOM: $Path contem BOM."
    }
}

function New-RsaCngFromPublicXml([string]$Xml) {
    if ($null -eq ("System.Security.Cryptography.RSACng" -as [type])) { throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE" }
    $legacy = New-Object System.Security.Cryptography.RSACryptoServiceProvider
    try {
        $legacy.PersistKeyInCsp = $false
        $legacy.FromXmlString($Xml)
        $params = $legacy.ExportParameters($false)
    }
    finally {
        $legacy.PersistKeyInCsp = $false
        $legacy.Clear()
        $legacy.Dispose()
    }
    $rsa = New-Object System.Security.Cryptography.RSACng
    $rsa.ImportParameters($params)
    return $rsa
}

function Test-Bundle([string]$Path, [string]$RootXmlPath) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) { throw "TRUST_BUNDLE_DIR_MISSING: $Path" }
    $bundlePath = Join-Path $Path "release-public-keys.json"
    $sigPath = Join-Path $Path "release-public-keys.sig"
    $metaPath = Join-Path $Path "release-public-keys.meta.json"
    foreach ($file in @($bundlePath, $sigPath, $metaPath)) {
        if (-not (Test-Path $file)) { throw "TRUST_BUNDLE_FILE_MISSING: $file" }
    }
    Assert-NoBom $bundlePath
    Assert-NoBom $metaPath
    $bundle = Get-Content -Raw -Path $bundlePath | ConvertFrom-Json
    $meta = Get-Content -Raw -Path $metaPath | ConvertFrom-Json
    if ([int64]$bundle.bundle_version -ne [int64]$meta.bundle_version) { throw "TRUST_METADATA_INVALID: bundle_version divergente." }
    if ((Get-Sha256Hex $bundlePath) -ne ([string]$meta.bundle_sha256).ToLowerInvariant()) { throw "TRUST_METADATA_INVALID: bundle_sha256 divergente." }
    if ((Get-Sha256Hex $sigPath) -ne ([string]$meta.signature_sha256).ToLowerInvariant()) { throw "TRUST_METADATA_INVALID: signature_sha256 divergente." }
    $ids = @{}
    foreach ($key in @($bundle.keys)) {
        $keyId = [string]$key.key_id
        if ($ids.ContainsKey($keyId)) { throw "TRUST_BUNDLE_KEY_DUPLICATE: $keyId" }
        $ids[$keyId] = $true
        foreach ($name in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
            if ([string]$key.public_key_xml -match ("<{0}>" -f [regex]::Escape($name))) { throw "TRUST_BUNDLE_PRIVATE_PARAMETERS: $keyId" }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($RootXmlPath)) {
        $rsa = New-RsaCngFromPublicXml (Get-Content -Raw -Path $RootXmlPath)
        try {
            $ok = $rsa.VerifyData([System.IO.File]::ReadAllBytes($bundlePath), [Convert]::FromBase64String((Get-Content -Raw -Path $sigPath).Trim()), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
            if (-not $ok) { throw "TRUST_SIGNATURE_INVALID: assinatura invalida." }
        }
        finally {
            $rsa.Dispose()
        }
    }
    Write-Step "Bundle valido: v$($bundle.bundle_version), root=$($meta.root_key_id)"
}

function Invoke-SelfTest {
    $script = Join-Path $PSScriptRoot "Build-NightOwlReleaseTrustBundle.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script -SelfTest
    if ($LASTEXITCODE -ne 0) { throw "SelfTest do build falhou." }
    Write-Step "SelfTest OK."
}

if ($SelfTest) {
    Invoke-SelfTest
    exit 0
}

Test-Bundle -Path $BundleDir -RootXmlPath $RootPublicKeyXmlPath
