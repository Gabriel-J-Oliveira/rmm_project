param(
    [long]$BundleVersion = 0,
    [string]$ReleasePublicKeysPath = $env:NIGHTOWL_RELEASE_PUBLIC_KEYS_PATH,
    [string]$RootSigningKeyPath = $env:NIGHTOWL_TRUST_ROOT_SIGNING_KEY_PATH,
    [string]$RootKeyId = $env:NIGHTOWL_TRUST_ROOT_KEY_ID,
    [string]$OutputDir = "",
    [string]$PublicBaseUrl = $env:NIGHTOWL_RELEASE_PUBLIC_BASE_URL,
    [datetime]$ValidFrom = [datetime]::UtcNow,
    [datetime]$ValidUntil = ([datetime]::UtcNow.AddYears(1)),
    [switch]$Force,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($PublicBaseUrl)) { $PublicBaseUrl = "https://nightowl.controlsul.com.br/downloads/nightowl-agent" }

function Write-Step([string]$Message) { Write-Host "[nightowl-trust-build] $Message" }

function Write-Utf8NoBomText([string]$Path, [string]$Content) {
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function ConvertTo-CanonicalJson($Value) {
    return (($Value | ConvertTo-Json -Depth 20 -Compress) + "`n")
}

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
        throw "TRUST_BUNDLE_JSON_BOM: $Path contem BOM."
    }
}

function New-RsaCngInstance {
    if ($null -eq ("System.Security.Cryptography.RSACng" -as [type])) {
        throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: RSACng indisponivel."
    }
    return New-Object System.Security.Cryptography.RSACng
}

function Import-RsaParametersFromXml([string]$Xml, [bool]$Private) {
    $legacy = $null
    try {
        $legacy = New-Object System.Security.Cryptography.RSACryptoServiceProvider
        $legacy.PersistKeyInCsp = $false
        $legacy.FromXmlString($Xml)
        return $legacy.ExportParameters($Private)
    }
    finally {
        if ($null -ne $legacy) {
            $legacy.PersistKeyInCsp = $false
            $legacy.Clear()
            $legacy.Dispose()
        }
    }
}

function New-RsaPssPrivateKeyFromXml([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) { throw "TRUST_ROOT_PRIVATE_KEY_MISSING: chave privada raiz nao encontrada." }
    $rsa = New-RsaCngInstance
    try {
        $rsa.ImportParameters((Import-RsaParametersFromXml -Xml (Get-Content -Raw -Path $Path) -Private $true))
        return $rsa
    }
    catch {
        $rsa.Dispose()
        throw "TRUST_ROOT_PRIVATE_KEY_INVALID: falha ao importar chave raiz. $($_.Exception.Message)"
    }
}

function New-RsaPssPublicKeyFromXmlText([string]$Xml) {
    $rsa = New-RsaCngInstance
    try {
        $rsa.ImportParameters((Import-RsaParametersFromXml -Xml $Xml -Private $false))
        return $rsa
    }
    catch {
        $rsa.Dispose()
        throw "TRUST_ROOT_PUBLIC_KEY_INVALID: falha ao importar chave publica raiz. $($_.Exception.Message)"
    }
}

function Export-PublicXmlFromPrivateXml([string]$PrivateXmlPath) {
    $legacy = New-Object System.Security.Cryptography.RSACryptoServiceProvider
    try {
        $legacy.PersistKeyInCsp = $false
        $legacy.FromXmlString((Get-Content -Raw -Path $PrivateXmlPath))
        return $legacy.ToXmlString($false)
    }
    finally {
        $legacy.PersistKeyInCsp = $false
        $legacy.Clear()
        $legacy.Dispose()
    }
}

function Assert-PublicXmlSafe([string]$Xml, [string]$KeyId) {
    foreach ($name in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
        if ($Xml -match ("<{0}>" -f [regex]::Escape($name))) {
            throw "TRUST_BUNDLE_PRIVATE_PARAMETERS: key_id $KeyId contem parametro privado $name."
        }
    }
}

function Convert-OptionalTrustDate([object]$Value, [string]$FieldName, [string]$KeyId) {
    if ($null -eq $Value) {
        return $null
    }

    $text = ([string]$Value).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }

    $parsed = [System.DateTimeOffset]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
    $ok = [System.DateTimeOffset]::TryParse(
        $text,
        [System.Globalization.CultureInfo]::InvariantCulture,
        $styles,
        [ref]$parsed)
    if (-not $ok) {
        throw "TRUST_BUNDLE_DATE_INVALID: $FieldName invalido para key_id $KeyId."
    }

    return $parsed.UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Read-ReleasePublicKeys([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) { throw "TRUST_RELEASE_KEYS_MISSING: release-public-keys.json nao encontrado." }
    $json = Get-Content -Raw -Path $Path | ConvertFrom-Json
    $keys = @($json.keys)
    if ($keys.Count -eq 0) { throw "TRUST_BUNDLE_EMPTY: release-public-keys.json sem chaves." }
    $seen = @{}
    $entries = @()
    foreach ($item in $keys) {
        $keyId = [string]$item.key_id
        $algorithm = if ([string]::IsNullOrWhiteSpace($item.algorithm)) { "RSA-PSS-SHA256" } else { [string]$item.algorithm }
        $status = if ([string]::IsNullOrWhiteSpace($item.status)) { "active" } else { [string]$item.status }
        $xml = [string]$item.public_key_xml
        if ([string]::IsNullOrWhiteSpace($keyId)) { throw "TRUST_BUNDLE_INVALID: key_id vazio." }
        if ($seen.ContainsKey($keyId)) { throw "TRUST_BUNDLE_KEY_DUPLICATE: $keyId." }
        if ($algorithm -ne "RSA-PSS-SHA256") { throw "TRUST_BUNDLE_ALGORITHM_INVALID: $keyId." }
        Assert-PublicXmlSafe -Xml $xml -KeyId $keyId
        $seen[$keyId] = $true
        $entries += [ordered]@{
            key_id = $keyId
            algorithm = $algorithm
            public_key_xml = $xml
            status = $status
            valid_from = Convert-OptionalTrustDate -Value $item.valid_from -FieldName "valid_from" -KeyId $keyId
            valid_until = Convert-OptionalTrustDate -Value $item.valid_until -FieldName "valid_until" -KeyId $keyId
            revoked_at = Convert-OptionalTrustDate -Value $item.revoked_at -FieldName "revoked_at" -KeyId $keyId
        }
    }
    return @($entries | Sort-Object { $_.key_id })
}

function Invoke-SelfTest {
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-trust-selftest-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $root = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
        $release = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
        $release2 = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
        $rootPrivate = Join-Path $temp "root-private.xml"
        $releasePublic = Join-Path $temp "release-public-keys.json"
        Write-Utf8NoBomText $rootPrivate $root.ToXmlString($true)
        Write-Utf8NoBomText $releasePublic (ConvertTo-CanonicalJson ([ordered]@{
            keys = @(
                [ordered]@{
                    key_id = "nightowl-release-test"
                    algorithm = "RSA-PSS-SHA256"
                    public_key_xml = $release.ToXmlString($false)
                    status = "active"
                    valid_from = "   "
                    valid_until = ""
                    revoked_at = ""
                },
                [ordered]@{
                    key_id = "nightowl-release-test-offset"
                    algorithm = "RSA-PSS-SHA256"
                    public_key_xml = $release2.ToXmlString($false)
                    status = "active"
                    valid_from = "2026-01-02T10:04:05-03:00"
                    valid_until = "2026-02-03T04:05:06+02:00"
                    revoked_at = $null
                }
            )
        }))
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -BundleVersion 1 -ReleasePublicKeysPath $releasePublic -RootSigningKeyPath $rootPrivate -RootKeyId "nightowl-root-test" -OutputDir (Join-Path $temp "out") -PublicBaseUrl "https://example.invalid/downloads/nightowl-agent" | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "SelfTest build child failed." }
        $manifest = Join-Path $temp "out\release-public-keys.json"
        $sig = Join-Path $temp "out\release-public-keys.sig"
        $meta = Join-Path $temp "out\release-public-keys.meta.json"
        $bundleJson = Get-Content -Raw -Path $manifest | ConvertFrom-Json
        $emptyDateKey = @($bundleJson.keys | Where-Object { [string]$_.key_id -eq "nightowl-release-test" })[0]
        if ($null -ne $emptyDateKey.valid_from -or $null -ne $emptyDateKey.valid_until -or $null -ne $emptyDateKey.revoked_at) {
            throw "SelfTest date normalization failed: empty date values must become null."
        }
        $offsetDateKey = @($bundleJson.keys | Where-Object { [string]$_.key_id -eq "nightowl-release-test-offset" })[0]
        if ([string]$offsetDateKey.valid_from -ne "2026-01-02T13:04:05.0000000Z") {
            throw "SelfTest date normalization failed: valid_from offset was not normalized to UTC."
        }
        if ([string]$offsetDateKey.valid_until -ne "2026-02-03T02:05:06.0000000Z") {
            throw "SelfTest date normalization failed: valid_until offset was not normalized to UTC."
        }
        $metaJson = Get-Content -Raw -Path $meta | ConvertFrom-Json
        if ((Get-Sha256Hex $manifest) -ne ([string]$metaJson.bundle_sha256)) { throw "SelfTest metadata bundle hash mismatch." }
        if ((Get-Sha256Hex $sig) -ne ([string]$metaJson.signature_sha256)) { throw "SelfTest metadata signature hash mismatch." }
        $rsa = New-RsaPssPublicKeyFromXmlText (Export-PublicXmlFromPrivateXml $rootPrivate)
        $ok = $rsa.VerifyData([System.IO.File]::ReadAllBytes($manifest), [Convert]::FromBase64String((Get-Content -Raw -Path $sig).Trim()), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
        if (-not $ok) { throw "SelfTest signature verification failed." }
        $tampered = [System.IO.File]::ReadAllBytes($manifest)
        $tampered[$tampered.Length - 2] = $tampered[$tampered.Length - 2] -bxor 1
        $tamperedOk = $rsa.VerifyData($tampered, [Convert]::FromBase64String((Get-Content -Raw -Path $sig).Trim()), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
        if ($tamperedOk) { throw "SelfTest tamper detection failed." }
        $invalidReleasePublic = Join-Path $temp "release-public-keys-invalid-date.json"
        Write-Utf8NoBomText $invalidReleasePublic (ConvertTo-CanonicalJson ([ordered]@{
            keys = @([ordered]@{
                key_id = "nightowl-release-invalid-date"
                algorithm = "RSA-PSS-SHA256"
                public_key_xml = $release.ToXmlString($false)
                status = "active"
                valid_from = $null
                valid_until = "not-a-date"
                revoked_at = $null
            })
        }))
        $invalidAccepted = $false
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -BundleVersion 2 -ReleasePublicKeysPath $invalidReleasePublic -RootSigningKeyPath $rootPrivate -RootKeyId "nightowl-root-test" -OutputDir (Join-Path $temp "invalid-out") -PublicBaseUrl "https://example.invalid/downloads/nightowl-agent" 2>$null | Out-Null
            $invalidAccepted = ($LASTEXITCODE -eq 0)
        }
        catch {
            $invalidAccepted = $false
        }
        if ($invalidAccepted) { throw "SelfTest invalid date failed: build accepted invalid valid_until." }
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

if ($BundleVersion -le 0) { throw "TRUST_BUNDLE_VERSION_REQUIRED: informe -BundleVersion." }
if ([string]::IsNullOrWhiteSpace($RootKeyId)) { throw "TRUST_ROOT_KEY_ID_REQUIRED: informe -RootKeyId ou NIGHTOWL_TRUST_ROOT_KEY_ID." }
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot ("artifacts\nightowl-agent\trust\bundles\{0}" -f $BundleVersion)
}
if ((Test-Path $OutputDir) -and -not $Force) {
    throw "TRUST_BUNDLE_OUTPUT_EXISTS: $OutputDir ja existe. Use -Force apenas para reconstruir artefatos locais."
}
if (Test-Path $OutputDir) { Remove-Item -Recurse -Force -LiteralPath $OutputDir }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$keys = Read-ReleasePublicKeys $ReleasePublicKeysPath
$bundle = [ordered]@{
    schema_version = 1
    bundle_version = $BundleVersion
    generated_at = ([datetime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ"))
    valid_from = $ValidFrom.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
    valid_until = $ValidUntil.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
    keys = @($keys)
}
$bundlePath = Join-Path $OutputDir "release-public-keys.json"
$sigPath = Join-Path $OutputDir "release-public-keys.sig"
$metaPath = Join-Path $OutputDir "release-public-keys.meta.json"
Write-Utf8NoBomText $bundlePath (ConvertTo-CanonicalJson $bundle)
Assert-NoBom $bundlePath

$rsa = New-RsaPssPrivateKeyFromXml $RootSigningKeyPath
try {
    $signatureBytes = $rsa.SignData([System.IO.File]::ReadAllBytes($bundlePath), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
    Write-Utf8NoBomText $sigPath ([Convert]::ToBase64String($signatureBytes) + "`n")
}
catch {
    throw "TRUST_BUNDLE_SIGNATURE_FAILED: falha ao assinar bundle com RSA-PSS/SHA-256. $($_.Exception.Message)"
}
finally {
    $rsa.Dispose()
}

$publicRoot = Export-PublicXmlFromPrivateXml $RootSigningKeyPath
$verifyRsa = New-RsaPssPublicKeyFromXmlText $publicRoot
try {
    $verified = $verifyRsa.VerifyData([System.IO.File]::ReadAllBytes($bundlePath), [Convert]::FromBase64String((Get-Content -Raw -Path $sigPath).Trim()), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
    if (-not $verified) { throw "TRUST_BUNDLE_SIGNATURE_INVALID: verificacao local falhou." }
}
finally {
    $verifyRsa.Dispose()
}

$bundleUrl = ($PublicBaseUrl.TrimEnd("/") + "/trust/bundles/$BundleVersion/release-public-keys.json")
$sigUrl = ($PublicBaseUrl.TrimEnd("/") + "/trust/bundles/$BundleVersion/release-public-keys.sig")
$metaUrl = ($PublicBaseUrl.TrimEnd("/") + "/trust/bundles/$BundleVersion/release-public-keys.meta.json")
$meta = [ordered]@{
    schema_version = 1
    bundle_version = $BundleVersion
    bundle_sha256 = Get-Sha256Hex $bundlePath
    signature_sha256 = Get-Sha256Hex $sigPath
    root_key_id = $RootKeyId
    size = (Get-Item $bundlePath).Length
    generated_at = $bundle.generated_at
    bundle_url = $bundleUrl
    signature_url = $sigUrl
    metadata_url = $metaUrl
}
Write-Utf8NoBomText $metaPath (ConvertTo-CanonicalJson $meta)
Assert-NoBom $metaPath
$null = Get-Content -Raw -Path $metaPath | ConvertFrom-Json

Write-Step "Bundle gerado em $OutputDir"
Write-Step "Bundle SHA-256: $($meta.bundle_sha256)"
Write-Step "Signature SHA-256: $($meta.signature_sha256)"
