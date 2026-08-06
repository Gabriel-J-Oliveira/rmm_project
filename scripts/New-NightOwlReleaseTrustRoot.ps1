param(
    [string]$RootKeyId = "",
    [string]$PrivateKeyPath = "",
    [string]$PublicRootsPath = "",
    [int]$KeySize = 3072,
    [switch]$Force,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) { Write-Host "[nightowl-trust-root] $Message" }

function Write-Utf8NoBomText([string]$Path, [string]$Content) {
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function ConvertTo-CanonicalJson($Value) {
    return (($Value | ConvertTo-Json -Depth 20 -Compress) + "`n")
}

function Assert-RootKeyId([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{2,119}$') {
        throw "TRUST_ROOT_KEY_ID_INVALID: informe RootKeyId explicito com 3 a 120 caracteres seguros."
    }
}

function Assert-PublicXmlSafe([string]$Xml, [string]$KeyId) {
    foreach ($name in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
        if ($Xml -match ("<{0}>" -f [regex]::Escape($name))) {
            throw "TRUST_ROOT_PUBLIC_KEY_UNSAFE: root_key_id $KeyId contem parametro privado $name."
        }
    }
}

function Assert-RootsJson([string]$Path) {
    if (-not (Test-Path $Path)) { throw "TRUST_ROOTS_JSON_MISSING: $Path" }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "TRUST_ROOTS_JSON_BOM: $Path contem BOM."
    }
    $json = Get-Content -Raw -Path $Path | ConvertFrom-Json
    if ([int]$json.schema_version -ne 1) { throw "TRUST_ROOTS_SCHEMA_INVALID: schema_version deve ser 1." }
    $roots = @($json.roots)
    if ($roots.Count -eq 0) { throw "TRUST_ROOTS_EMPTY: roots vazio." }
    $seen = @{}
    foreach ($root in $roots) {
        $keyId = [string]$root.key_id
        $algorithm = if ([string]::IsNullOrWhiteSpace($root.algorithm)) { "" } else { [string]$root.algorithm }
        $xml = [string]$root.public_key_xml
        if ([string]::IsNullOrWhiteSpace($keyId)) { throw "TRUST_ROOTS_KEY_ID_EMPTY: key_id vazio." }
        if ($seen.ContainsKey($keyId)) { throw "TRUST_ROOTS_KEY_DUPLICATE: $keyId." }
        if ($algorithm -ne "RSA-PSS-SHA256") { throw "TRUST_ROOTS_ALGORITHM_INVALID: $keyId." }
        if ([string]::IsNullOrWhiteSpace($xml)) { throw "TRUST_ROOTS_PUBLIC_KEY_MISSING: $keyId." }
        Assert-PublicXmlSafe -Xml $xml -KeyId $keyId
        $seen[$keyId] = $true
    }
}

function Protect-PrivateKeyAcl([string]$Path) {
    if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) { return }
    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $Path /inheritance:r /grant:r "*$currentSid`:F" "*S-1-5-18:F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "TRUST_ROOT_PRIVATE_KEY_ACL_FAILED: icacls falhou." }
}

function New-RsaCngFromXml([string]$Xml, [bool]$Private) {
    if ($null -eq ("System.Security.Cryptography.RSACng" -as [type])) {
        throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: RSACng indisponivel."
    }
    $legacy = New-Object System.Security.Cryptography.RSACryptoServiceProvider
    try {
        $legacy.PersistKeyInCsp = $false
        $legacy.FromXmlString($Xml)
        $parameters = $legacy.ExportParameters($Private)
    }
    finally {
        $legacy.PersistKeyInCsp = $false
        $legacy.Clear()
        $legacy.Dispose()
    }
    $rsa = New-Object System.Security.Cryptography.RSACng
    $rsa.ImportParameters($parameters)
    return $rsa
}

function Test-KeyPair([string]$PrivateXml, [string]$PublicXml) {
    $private = New-RsaCngFromXml -Xml $PrivateXml -Private $true
    $public = New-RsaCngFromXml -Xml $PublicXml -Private $false
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes("nightowl trust root self-test")
        $signature = $private.SignData($bytes, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
        if (-not $public.VerifyData($bytes, $signature, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)) {
            throw "TRUST_ROOT_SELFTEST_FAILED: assinatura RSA-PSS nao validou."
        }
        $bytes[0] = $bytes[0] -bxor 1
        if ($public.VerifyData($bytes, $signature, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)) {
            throw "TRUST_ROOT_SELFTEST_FAILED: tamper nao foi detectado."
        }
    }
    finally {
        $private.Dispose()
        $public.Dispose()
    }
}

function Invoke-SelfTest {
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-trust-root-selftest-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath `
            -RootKeyId "nightowl-root-selftest" `
            -PrivateKeyPath (Join-Path $temp "root-private.xml") `
            -PublicRootsPath (Join-Path $temp "release-trust-roots.json")
        if ($LASTEXITCODE -ne 0) { throw "SelfTest child failed." }
        Assert-RootsJson (Join-Path $temp "release-trust-roots.json")
        Write-Step "SelfTest OK."
    }
    finally {
        Remove-Item -Recurse -Force -LiteralPath $temp -ErrorAction SilentlyContinue
    }
}

if ($SelfTest) {
    Invoke-SelfTest
    exit 0
}

Assert-RootKeyId $RootKeyId
if ($KeySize -lt 3072) { throw "TRUST_ROOT_KEY_SIZE_INVALID: use RSA 3072 ou superior." }
if ([string]::IsNullOrWhiteSpace($PrivateKeyPath)) {
    $PrivateKeyPath = Join-Path $env:USERPROFILE (".nightowl\trust-root\{0}-private.xml" -f $RootKeyId)
}
if ([string]::IsNullOrWhiteSpace($PublicRootsPath)) {
    $PublicRootsPath = Join-Path $env:USERPROFILE ".nightowl\release-trust-roots.json"
}
if ((Test-Path $PrivateKeyPath) -and -not $Force) { throw "TRUST_ROOT_PRIVATE_KEY_EXISTS: $PrivateKeyPath ja existe. Use -Force para recriar intencionalmente." }
if ((Test-Path $PublicRootsPath) -and -not $Force) { throw "TRUST_ROOTS_JSON_EXISTS: $PublicRootsPath ja existe. Use -Force para recriar intencionalmente." }

$rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider $KeySize
try {
    $rsa.PersistKeyInCsp = $false
    $privateXml = $rsa.ToXmlString($true)
    $publicXml = $rsa.ToXmlString($false)
}
finally {
    $rsa.PersistKeyInCsp = $false
    $rsa.Clear()
    $rsa.Dispose()
}

Assert-PublicXmlSafe -Xml $publicXml -KeyId $RootKeyId
Test-KeyPair -PrivateXml $privateXml -PublicXml $publicXml

Write-Utf8NoBomText -Path $PrivateKeyPath -Content $privateXml
Protect-PrivateKeyAcl -Path $PrivateKeyPath

$roots = [ordered]@{
    schema_version = 1
    roots = @([ordered]@{
        key_id = $RootKeyId
        algorithm = "RSA-PSS-SHA256"
        public_key_xml = $publicXml
        status = "active"
    })
}
Write-Utf8NoBomText -Path $PublicRootsPath -Content (ConvertTo-CanonicalJson $roots)
Assert-RootsJson $PublicRootsPath

Write-Step "Raiz gerada."
Write-Step "Private key: $PrivateKeyPath"
Write-Step "Public roots: $PublicRootsPath"
