param(
    [string]$TrustRootsPath = $env:NIGHTOWL_RELEASE_TRUST_ROOTS_JSON,
    [string]$ReleasePublicKeysPath = $env:NIGHTOWL_RELEASE_PUBLIC_KEYS_PATH,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($TrustRootsPath)) {
    $TrustRootsPath = Join-Path $env:USERPROFILE ".nightowl\release-trust-roots.json"
}
if ([string]::IsNullOrWhiteSpace($ReleasePublicKeysPath)) {
    $ReleasePublicKeysPath = Join-Path $env:USERPROFILE ".nightowl\release-public-keys.json"
}

function Write-Step([string]$Message) {
    Write-Host "[nightowl-public-key-check] $Message"
}

function Get-Sha256Text([string]$Value) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Value))).Replace("-", "").ToLowerInvariant())
    }
    finally {
        $sha.Dispose()
    }
}

function Test-HasBom([string]$Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    return ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
}

function Test-PrivateRsaParameters([string]$Xml) {
    foreach ($name in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
        if ($Xml -match ("<{0}>" -f [regex]::Escape($name))) {
            return $true
        }
    }
    return $false
}

function Read-JsonNoBom([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        throw "Arquivo nao encontrado: $Path"
    }
    if (Test-HasBom $Path) {
        throw "JSON contem UTF-8 BOM: $Path"
    }
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function Test-KeySet([string]$Kind, $Items, [string]$RequiredActiveKeyId) {
    $seen = @{}
    $activeCount = 0
    foreach ($item in @($Items)) {
        $keyId = [string]$item.key_id
        $algorithm = [string]$item.algorithm
        $status = if ([string]::IsNullOrWhiteSpace($item.status)) { "active" } else { [string]$item.status }
        $xml = [string]$item.public_key_xml
        if ([string]::IsNullOrWhiteSpace($keyId)) { throw "$Kind contem key_id vazio." }
        if ($seen.ContainsKey($keyId)) { throw "$Kind contem key_id duplicado: $keyId." }
        if ($algorithm -ne "RSA-PSS-SHA256") { throw "$Kind contem algoritmo invalido para $keyId." }
        if (Test-PrivateRsaParameters $xml) { throw "$Kind contem parametros privados para $keyId." }
        if ($status -eq "active") { $activeCount++ }
        $fingerprint = Get-Sha256Text $xml
        Write-Step ("{0}: id={1} algorithm={2} status={3} fingerprint={4}... xml_bytes={5}" -f $Kind, $keyId, $algorithm, $status, $fingerprint.Substring(0, 16), ([System.Text.Encoding]::UTF8.GetByteCount($xml)))
        $seen[$keyId] = $true
    }
    if ($activeCount -le 0) { throw "$Kind precisa conter pelo menos uma chave ativa." }
    if (-not [string]::IsNullOrWhiteSpace($RequiredActiveKeyId)) {
        $match = @($Items | Where-Object { [string]$_.key_id -eq $RequiredActiveKeyId -and ([string]$_.status -eq "active" -or [string]::IsNullOrWhiteSpace($_.status)) }) | Select-Object -First 1
        if ($null -eq $match) { throw "$Kind nao contem $RequiredActiveKeyId ativa." }
    }
}

function Invoke-SelfTest {
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-public-key-check-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $publicXml = "<RSAKeyValue><Modulus>abc</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>"
        $roots = Join-Path $temp "release-trust-roots.json"
        $keys = Join-Path $temp "release-public-keys.json"
        $payloadRoots = [ordered]@{ schema_version = 1; roots = @([ordered]@{ key_id = "root-test"; algorithm = "RSA-PSS-SHA256"; public_key_xml = $publicXml; status = "active" }) }
        $payloadKeys = [ordered]@{ schema_version = 1; keys = @([ordered]@{ key_id = "nightowl-release-2026-02"; algorithm = "RSA-PSS-SHA256"; public_key_xml = $publicXml; status = "active" }) }
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($roots, ($payloadRoots | ConvertTo-Json -Depth 8), $encoding)
        [System.IO.File]::WriteAllText($keys, ($payloadKeys | ConvertTo-Json -Depth 8), $encoding)
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -TrustRootsPath $roots -ReleasePublicKeysPath $keys
        if ($LASTEXITCODE -ne 0) { throw "SelfTest falhou." }
        Write-Step "SelfTest OK."
    }
    finally {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($SelfTest) {
    Invoke-SelfTest
    exit 0
}

$rootsJson = Read-JsonNoBom $TrustRootsPath
if ([int]$rootsJson.schema_version -ne 1) { throw "release-trust-roots.json deve usar schema_version=1." }
Test-KeySet "trust-root" @($rootsJson.roots) ""

$releaseKeysJson = Read-JsonNoBom $ReleasePublicKeysPath
Test-KeySet "release-key" @($releaseKeysJson.keys) "nightowl-release-2026-02"

Write-Step "Arquivos publicos validados sem imprimir XML ou segredos."
