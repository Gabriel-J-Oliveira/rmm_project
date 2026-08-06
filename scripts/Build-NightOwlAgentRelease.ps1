param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [switch]$Force,
    [switch]$ValidateOnly,
    [switch]$Publish,

    [string]$ReleaseDir = "",
    [string]$PublishHost = "",
    [string]$PublishPath = "/opt/nightowl/downloads/agent/windows",
    [string]$PublicBaseUrl = "https://nightowl.controlsul.com.br/downloads/nightowl-agent",
    [string]$Runtime = "win-x64",
    [string]$MinimumUpdaterVersion = "0.1.0.7",

    [ValidateSet("development", "pilot", "stable")]
    [string]$Channel = "",

    [string]$SigningKeyPath = $env:NIGHTOWL_RELEASE_SIGNING_KEY,
    [string]$SigningKeyId = $env:NIGHTOWL_RELEASE_SIGNING_KEY_ID,
    [string]$TrustedPublicKeysPath = $env:NIGHTOWL_RELEASE_TRUSTED_KEYS_JSON,
    [string]$TrustRootsPath = $env:NIGHTOWL_RELEASE_TRUST_ROOTS_JSON,
    [switch]$AllowUnsignedDevelopment,
    [switch]$SkipTests,

    [switch]$UpdatePublicLatest,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $repoRoot "artifacts\nightowl-agent\releases"
$workRoot = Join-Path $repoRoot "artifacts\nightowl-agent\work"
$agentProject = Join-Path $repoRoot "NightOwl.Agent.Windows\NightOwl.Agent.Windows.csproj"
$trayProject = Join-Path $repoRoot "NightOwl.Agent.Tray\NightOwl.Agent.Tray.csproj"
$updaterProject = Join-Path $repoRoot "NightOwl.Agent.Updater\NightOwl.Agent.Updater.csproj"
$diagnosticsProject = Join-Path $repoRoot "NightOwl.Agent.Diagnostics\NightOwl.Agent.Diagnostics.csproj"
$sharedProject = Join-Path $repoRoot "NightOwl.Agent.Shared\NightOwl.Agent.Shared.csproj"
$testProject = Join-Path $repoRoot "NightOwl.Agent.Shared.Tests\NightOwl.Agent.Shared.Tests.csproj"
$updaterTestProject = Join-Path $repoRoot "NightOwl.Agent.Updater.Tests\NightOwl.Agent.Updater.Tests.csproj"
$installScript = Join-Path $repoRoot "NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1"
$uninstallScript = Join-Path $repoRoot "NightOwl.Agent.Windows\scripts\Uninstall-NightOwlAgentDotNet.ps1"
$iconPath = Join-Path $repoRoot "assets\icons\NightOwl.ico"
if ([string]::IsNullOrWhiteSpace($TrustRootsPath)) {
    $TrustRootsPath = $env:NIGHTOWL_RELEASE_TRUST_ROOTS_PATH
}

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-release] {0}" -f $Message)
}

function Resolve-FullPath([string]$Path) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Assert-Version([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Version nao pode ser vazia."
    }
    if ($Value -notmatch '^\d+\.\d+\.\d+(\.\d+)?(-[0-9A-Za-z][0-9A-Za-z.-]*)?$') {
        throw "Version invalida: $Value. Use SemVer major.minor.patch[.build][-prerelease], ex: 0.1.1.0-rc1."
    }
}

function Get-VersionCore([string]$Value) {
    return (($Value -split "\+", 2)[0] -split "-", 2)[0]
}

function Get-VersionPrerelease([string]$Value) {
    $withoutBuild = ($Value -split "\+", 2)[0]
    if ($withoutBuild -notmatch "-") { return "" }
    return ($withoutBuild -split "-", 2)[1]
}

function Convert-VersionParts([string]$Value) {
    $parts = @((Get-VersionCore $Value).Split(".") | ForEach-Object { [int]$_ })
    while ($parts.Count -lt 4) {
        $parts += 0
    }
    return ,$parts[0..3]
}

function Compare-NightOwlVersion([string]$Left, [string]$Right) {
    $a = Convert-VersionParts $Left
    $b = Convert-VersionParts $Right
    for ($i = 0; $i -lt 4; $i++) {
        if ($a[$i] -lt $b[$i]) { return -1 }
        if ($a[$i] -gt $b[$i]) { return 1 }
    }
    $leftPre = Get-VersionPrerelease $Left
    $rightPre = Get-VersionPrerelease $Right
    if ([string]::IsNullOrWhiteSpace($leftPre) -and -not [string]::IsNullOrWhiteSpace($rightPre)) { return 1 }
    if (-not [string]::IsNullOrWhiteSpace($leftPre) -and [string]::IsNullOrWhiteSpace($rightPre)) { return -1 }
    if ($leftPre -lt $rightPre) { return -1 }
    if ($leftPre -gt $rightPre) { return 1 }
    return 0
}

function Get-CurrentProjectVersion {
    [xml]$project = Get-Content -Path $agentProject
    $value = [string]$project.Project.PropertyGroup.Version
    if ([string]::IsNullOrWhiteSpace($value)) { return "0.0.0" }
    return $value
}

function Get-GitCommit {
    try {
        $commit = (& git -C $repoRoot rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0) { return [string]$commit.Trim() }
    }
    catch {}
    return ""
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function Write-Utf8NoBomText([string]$Path, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-Utf8NoBomJson([string]$Path, $Value, [int]$Depth = 8) {
    $content = $Value | ConvertTo-Json -Depth $Depth
    Write-Utf8NoBomText -Path $Path -Content $content
}

function ConvertTo-CanonicalJson($Value) {
    if ($null -eq $Value) { return "null" }
    if ($Value -is [bool]) { if ($Value) { return "true" } else { return "false" } }
    if ($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) {
        return [string]::Format([System.Globalization.CultureInfo]::InvariantCulture, "{0}", $Value)
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $parts = New-Object System.Collections.Generic.List[string]
        foreach ($key in ($Value.Keys | Sort-Object)) {
            $parts.Add(("{0}:{1}" -f (ConvertTo-CanonicalJson ([string]$key)), (ConvertTo-CanonicalJson $Value[$key])))
        }
        return "{" + ($parts -join ",") + "}"
    }
    if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        $parts = @()
        foreach ($item in $Value) { $parts += (ConvertTo-CanonicalJson $item) }
        return "[" + ($parts -join ",") + "]"
    }
    return ([string]$Value | ConvertTo-Json -Compress)
}

function Write-CanonicalJson([string]$Path, $Value) {
    Write-Utf8NoBomText -Path $Path -Content (ConvertTo-CanonicalJson $Value)
}

function Assert-RsaPssProviderAvailable {
    if ($null -eq ("System.Security.Cryptography.RSACng" -as [type])) {
        throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: Provedor CNG RSA indisponivel neste Windows/PowerShell. RSA-PSS e obrigatorio; PKCS#1 nao sera usado como fallback."
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
        throw "Chave privada de assinatura nao encontrada. Informe -SigningKeyPath ou NIGHTOWL_RELEASE_SIGNING_KEY."
    }
    $xml = Get-Content -Raw -Path $Path
    $parameters = Import-RsaParametersFromXml -Xml $xml -IncludePrivateParameters $true
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

function New-RsaPssPublicKeyFromXml([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) {
        throw "Chave publica de assinatura nao encontrada: $Path"
    }
    return New-RsaPssPublicKeyFromXmlText -Xml (Get-Content -Raw -Path $Path)
}

function Export-RsaPublicXmlFromPrivateXmlFile([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) {
        throw "Chave privada de assinatura nao encontrada. Informe -SigningKeyPath ou NIGHTOWL_RELEASE_SIGNING_KEY."
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

function Sign-ReleaseManifest([string]$ManifestPath, [string]$SignaturePath, [string]$PrivateKeyPath, [string]$KeyId) {
    if ([string]::IsNullOrWhiteSpace($KeyId)) {
        throw "SigningKeyId obrigatorio para assinatura do manifesto."
    }
    $rsa = New-RsaPssPrivateKeyFromXml $PrivateKeyPath
    $publicRsa = $null
    try {
        $bytes = [System.IO.File]::ReadAllBytes($ManifestPath)
        try {
            $signature = $rsa.SignData(
                $bytes,
                [System.Security.Cryptography.HashAlgorithmName]::SHA256,
                [System.Security.Cryptography.RSASignaturePadding]::Pss
            )
        }
        catch {
            throw "RELEASE_SIGNATURE_GENERATION_FAILED: Falha ao gerar assinatura RSA-PSS. Verifique o formato da chave e a disponibilidade do provedor CNG. Detalhe: $($_.Exception.Message)"
        }
        Write-Utf8NoBomText -Path $SignaturePath -Content ([Convert]::ToBase64String($signature))
        $publicXml = Export-RsaPublicXmlFromPrivateXmlFile $PrivateKeyPath
        $publicRsa = New-RsaPssPublicKeyFromXmlText $publicXml
        $valid = $publicRsa.VerifyData(
            $bytes,
            $signature,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pss
        )
        if (-not $valid) { throw "RELEASE_SIGNATURE_VERIFICATION_FAILED: Assinatura RSA-PSS gerada nao passou na verificacao local." }
    }
    finally {
        if ($null -ne $publicRsa) { $publicRsa.Dispose() }
        $rsa.Dispose()
    }
}

function Write-TrustedReleasePublicKeys([string]$Path, [string]$PrivateKeyPath, [string]$KeyId, [string]$TrustedPublicKeysPath = "") {
    if ([string]::IsNullOrWhiteSpace($PrivateKeyPath)) { return }
    $publicXml = Export-RsaPublicXmlFromPrivateXmlFile $PrivateKeyPath
    $entries = @()
    if (-not [string]::IsNullOrWhiteSpace($TrustedPublicKeysPath)) {
        if (-not (Test-Path $TrustedPublicKeysPath)) {
            throw "Arquivo de chaves publicas confiaveis nao encontrado: $TrustedPublicKeysPath"
        }
        try {
            $trusted = Get-Content -Raw -Path $TrustedPublicKeysPath | ConvertFrom-Json
        }
        catch {
            throw "release-public-keys.json invalido em TrustedPublicKeysPath: $($_.Exception.Message)"
        }
        foreach ($item in @($trusted.keys)) {
            if ([string]::IsNullOrWhiteSpace($item.key_id)) { continue }
            $entries += [ordered]@{
                key_id = [string]$item.key_id
                algorithm = if ([string]::IsNullOrWhiteSpace($item.algorithm)) { "RSA-PSS-SHA256" } else { [string]$item.algorithm }
                public_key_xml = [string]$item.public_key_xml
                status = if ([string]::IsNullOrWhiteSpace($item.status)) { "active" } else { [string]$item.status }
                valid_from = if ($null -eq $item.valid_from) { "" } else { [string]$item.valid_from }
                valid_until = if ($null -eq $item.valid_until) { "" } else { [string]$item.valid_until }
                revoked_at = if ($null -eq $item.revoked_at) { "" } else { [string]$item.revoked_at }
            }
        }
    }
    $entries = @($entries | Where-Object { $_.key_id -ne $KeyId })
    $entries += [ordered]@{
        key_id = $KeyId
        algorithm = "RSA-PSS-SHA256"
        public_key_xml = $publicXml
        status = "active"
        valid_from = (Get-Date).ToUniversalTime().ToString("O")
        valid_until = ""
        revoked_at = ""
    }
    $keys = [ordered]@{
        keys = @($entries | Sort-Object { $_.key_id })
    }
    Write-Utf8NoBomJson -Path $Path -Value $keys -Depth 8
}

function Assert-ReleaseTrustRootsJson([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) {
        throw "RELEASE_TRUST_ROOTS_MISSING: release-trust-roots.json nao encontrado. Use -TrustRootsPath ou NIGHTOWL_RELEASE_TRUST_ROOTS_JSON."
    }
    if (Test-FileHasUtf8Bom $Path) {
        throw "RELEASE_TRUST_ROOTS_INVALID: release-trust-roots.json contem UTF-8 BOM."
    }
    try {
        $rootsJson = Get-Content -Raw -Path $Path | ConvertFrom-Json
    }
    catch {
        throw "RELEASE_TRUST_ROOTS_INVALID: JSON invalido. Detalhe: $($_.Exception.Message)"
    }
    if ([int]$rootsJson.schema_version -ne 1) {
        throw "RELEASE_TRUST_ROOTS_INVALID: schema_version deve ser 1."
    }
    $roots = @($rootsJson.roots)
    if ($roots.Count -eq 0) {
        throw "RELEASE_TRUST_ROOTS_INVALID: roots vazio."
    }
    $seen = @{}
    foreach ($root in $roots) {
        $keyId = [string]$root.key_id
        $algorithm = [string]$root.algorithm
        $publicXml = [string]$root.public_key_xml
        if ([string]::IsNullOrWhiteSpace($keyId)) {
            throw "RELEASE_TRUST_ROOTS_INVALID: key_id vazio."
        }
        if ($seen.ContainsKey($keyId)) {
            throw "RELEASE_TRUST_ROOTS_INVALID: key_id duplicado: $keyId."
        }
        if ($algorithm -ne "RSA-PSS-SHA256") {
            throw "RELEASE_TRUST_ROOTS_INVALID: algoritmo invalido para $keyId."
        }
        if ([string]::IsNullOrWhiteSpace($publicXml)) {
            throw "RELEASE_TRUST_ROOTS_INVALID: public_key_xml vazio para $keyId."
        }
        foreach ($privateElement in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
            if ($publicXml -match ("<{0}>" -f [regex]::Escape($privateElement))) {
                throw "RELEASE_TRUST_ROOTS_INVALID: root $keyId contem parametro privado $privateElement."
            }
        }
        $seen[$keyId] = $true
    }
}

function Copy-ReleaseTrustRoots([string]$SourcePath, [string]$PackageDir) {
    Assert-ReleaseTrustRootsJson $SourcePath
    Copy-Item -Path $SourcePath -Destination (Join-Path $PackageDir "release-trust-roots.json") -Force
}

function Test-FileHasUtf8Bom([string]$Path) {
    if (-not (Test-Path $Path)) { throw "Arquivo nao encontrado para validacao BOM: $Path" }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    return $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
}

function Assert-JsonFileUtf8NoBom([string]$Path) {
    if (Test-FileHasUtf8Bom $Path) {
        throw "JSON contem UTF-8 BOM: $Path"
    }
    $null = Get-Content -Raw -Path $Path | ConvertFrom-Json
}

function Assert-JsonTextNoBom([string]$Name, [string]$Content) {
    if ($Content.Length -gt 0 -and [int][char]$Content[0] -eq 0xFEFF) {
        throw "JSON contem UTF-8 BOM: $Name"
    }
    $null = $Content | ConvertFrom-Json
}

function Invoke-SelfTest {
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-json-selftest-{0}" -f ([guid]::NewGuid().ToString("N")))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $jsonPath = Join-Path $temp "selftest.json"
        Write-Utf8NoBomJson -Path $jsonPath -Value ([ordered]@{ version = "0.1.1.0-rc4"; url = "https://nightowl.controlsul.com.br/downloads/nightowl-agent" })
        Assert-JsonFileUtf8NoBom $jsonPath
        $canonical = ConvertTo-CanonicalJson ([ordered]@{ z = 1; a = [ordered]@{ b = "ok"; a = $true } })
        if ($canonical -ne '{"a":{"a":true,"b":"ok"},"z":1}') { throw "Canonical JSON inconsistente: $canonical" }

        $legacyProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider 2048
        try {
            $legacyProvider.PersistKeyInCsp = $false
            $privateKeyPath = Join-Path $temp "release-private.xml"
            $publicKeyPath = Join-Path $temp "release-public.xml"
            Write-Utf8NoBomText -Path $privateKeyPath -Content $legacyProvider.ToXmlString($true)
            Write-Utf8NoBomText -Path $publicKeyPath -Content $legacyProvider.ToXmlString($false)
        }
        finally {
            $legacyProvider.PersistKeyInCsp = $false
            $legacyProvider.Clear()
            $legacyProvider.Dispose()
        }

        $privateRsa = New-RsaPssPrivateKeyFromXml $privateKeyPath
        $publicRsa = New-RsaPssPublicKeyFromXml $publicKeyPath
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes("nightowl-rsa-pss-selftest")
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
            if (-not $valid) { throw "SelfTest RSA-PSS falhou: assinatura valida rejeitada." }
            $tampered = [byte[]]$bytes.Clone()
            $tampered[0] = $tampered[0] -bxor 0x01
            $tamperedValid = $publicRsa.VerifyData(
                $tampered,
                $signature,
                [System.Security.Cryptography.HashAlgorithmName]::SHA256,
                [System.Security.Cryptography.RSASignaturePadding]::Pss
            )
            if ($tamperedValid) { throw "SelfTest RSA-PSS falhou: manifesto alterado foi aceito." }
        }
        finally {
            $publicRsa.Dispose()
            $privateRsa.Dispose()
        }

        $invalidKeyPath = Join-Path $temp "invalid.xml"
        Write-Utf8NoBomText -Path $invalidKeyPath -Content "<not-rsa />"
        $invalidRejected = $false
        try { $null = New-RsaPssPrivateKeyFromXml $invalidKeyPath }
        catch { $invalidRejected = $true }
        if (-not $invalidRejected) { throw "SelfTest RSA-PSS falhou: XML invalido foi aceito." }

        $publicAsPrivateRejected = $false
        try { $null = New-RsaPssPrivateKeyFromXml $publicKeyPath }
        catch { $publicAsPrivateRejected = $true }
        if (-not $publicAsPrivateRejected) { throw "SelfTest RSA-PSS falhou: chave publica foi aceita para assinatura." }

        $manifestPath = Join-Path $temp "release-manifest.json"
        $signaturePath = Join-Path $temp "release-manifest.sig"
        Write-CanonicalJson -Path $manifestPath -Value ([ordered]@{
            schema_version = 1
            version = "0.1.1.0-rc6"
            channel = "development"
            key_id = "nightowl-selftest"
            package = [ordered]@{
                filename = "NightOwl.Agent.Windows.zip"
                sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                size = 123
            }
        })
        Sign-ReleaseManifest -ManifestPath $manifestPath -SignaturePath $signaturePath -PrivateKeyPath $privateKeyPath -KeyId "nightowl-selftest"
        $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
        $signatureBytes = [Convert]::FromBase64String((Get-Content -Raw -Path $signaturePath).Trim())
        $manifestPublicRsa = New-RsaPssPublicKeyFromXml $publicKeyPath
        try {
            $manifestSignatureValid = $manifestPublicRsa.VerifyData(
                $manifestBytes,
                $signatureBytes,
                [System.Security.Cryptography.HashAlgorithmName]::SHA256,
                [System.Security.Cryptography.RSASignaturePadding]::Pss
            )
            if (-not $manifestSignatureValid) { throw "SelfTest RSA-PSS falhou: release-manifest.sig valida foi rejeitada." }
            $manifestBytes[0] = $manifestBytes[0] -bxor 0x01
            $tamperedManifestValid = $manifestPublicRsa.VerifyData(
                $manifestBytes,
                $signatureBytes,
                [System.Security.Cryptography.HashAlgorithmName]::SHA256,
                [System.Security.Cryptography.RSASignaturePadding]::Pss
            )
            if ($tamperedManifestValid) { throw "SelfTest RSA-PSS falhou: release-manifest.json alterado foi aceito." }
        }
        finally {
            $manifestPublicRsa.Dispose()
        }

        Write-Step "SelfTest OK: JSON UTF-8 sem BOM e assinatura RSA-PSS/CNG validados em Windows PowerShell."
    }
    finally {
        Remove-Item -Path $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Checked([string]$FileName, [string[]]$Arguments) {
    Write-Step ("Executando: {0} {1}" -f $FileName, ($Arguments -join " "))
    & $FileName @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Comando falhou com exit code ${LASTEXITCODE}: $FileName $($Arguments -join ' ')"
    }
}

function Copy-ReleasePayload([string]$PublishDir, [string]$PackageDir) {
    $forbiddenNames = @(
        "agent.config.json",
        "agent.identity.json",
        "agent.state.json",
        "agent-dotnet.state.json",
        "update-state.json",
        "version.json",
        "checksums.json",
        "release-manifest.json",
        "NightOwl.Agent.Windows.zip"
    )
    $forbiddenExtensions = @(".pdb", ".log", ".tmp", ".ps1")

    New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null
    Get-ChildItem -Path $PublishDir -Force | ForEach-Object {
        Copy-PayloadItem -Source $_.FullName -Destination (Join-Path $PackageDir $_.Name) -ForbiddenNames $forbiddenNames -ForbiddenExtensions $forbiddenExtensions
    }
}

function Copy-PayloadItem([string]$Source, [string]$Destination, [string[]]$ForbiddenNames, [string[]]$ForbiddenExtensions) {
    $item = Get-Item -LiteralPath $Source -Force
    if ($item.Name -in $ForbiddenNames) { return }
    if ($item.Name -like "*.preserved-*") { return }
    if ($item.Name -in @("bin", "obj", ".git", ".vs", "artifacts", "downloads", "publish", "releases")) { return }
    if (-not $item.PSIsContainer -and $ForbiddenExtensions -contains $item.Extension.ToLowerInvariant()) { return }
    if ($item.PSIsContainer) {
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
        Get-ChildItem -LiteralPath $item.FullName -Force | ForEach-Object {
            Copy-PayloadItem -Source $_.FullName -Destination (Join-Path $Destination $_.Name) -ForbiddenNames $ForbiddenNames -ForbiddenExtensions $ForbiddenExtensions
        }
    }
    else {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Force
    }
}

function New-AgentVersionFile([string]$Path, [string]$BuildId, [string]$BuiltAt, [string]$Commit) {
    $agentVersion = [ordered]@{
        product = "NightOwl Agent Windows"
        version = $Version
        build_id = $BuildId
        built_at = $BuiltAt
        git_commit = $Commit
        minimum_updater_version = $MinimumUpdaterVersion
        package = "NightOwl.Agent.Windows.zip"
        installedAt = ""
        channel = $Channel
        packageSha256 = ""
        updatedBy = "installer"
    }
    Write-Utf8NoBomJson -Path $Path -Value $agentVersion -Depth 6
}

function Compress-ReleaseZip([string]$PackageDir, [string]$ZipPath) {
    if (Test-Path $ZipPath) { Remove-Item -Path $ZipPath -Force }
    Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ZipPath -Force
}

function Read-ZipEntryText([System.IO.Compression.ZipArchive]$Zip, [string]$EntryName) {
    $entry = $Zip.GetEntry($EntryName)
    if ($null -eq $entry) { throw "Entrada ausente no ZIP: $EntryName" }
    $reader = New-Object System.IO.StreamReader($entry.Open())
    try { return $reader.ReadToEnd() }
    finally { $reader.Dispose() }
}

function Test-ForbiddenZipEntry([string]$Name) {
    $normalized = $Name.Replace("\", "/").ToLowerInvariant()
    if ($normalized -match '(^|/)(bin|obj|logs?|config|identity|state|diagnostics|artifacts|downloads|publish|releases)(/|$)') { return $true }
    if ($normalized -match 'agent\.config\.json$') { return $true }
    if ($normalized -match 'agent\.identity\.json$') { return $true }
    if ($normalized -match 'agent(\.|-)state\.json$') { return $true }
    if ($normalized -match 'agent-dotnet\.state\.json$') { return $true }
    if ($normalized -match 'update-state\.json$') { return $true }
    if ($normalized -match '\.preserved-') { return $true }
    if ($normalized -match '\.log$|\.tmp$|\.pdb$') { return $true }
    if ($normalized -match 'token|machine_id') { return $true }
    return $false
}

function Validate-Release([string]$Path) {
    if (-not (Test-Path $Path)) { throw "ReleaseDir nao encontrado: $Path" }
    $zipPath = Join-Path $Path "NightOwl.Agent.Windows.zip"
    $versionPath = Join-Path $Path "version.json"
    $checksumsPath = Join-Path $Path "checksums.json"
    $manifestPath = Join-Path $Path "release-manifest.json"
    $signaturePath = Join-Path $Path "release-manifest.sig"
    foreach ($required in @($zipPath, $versionPath, $checksumsPath, $manifestPath)) {
        if (-not (Test-Path $required)) { throw "Artefato obrigatorio ausente: $required" }
    }
    foreach ($jsonPath in @($versionPath, $checksumsPath, $manifestPath)) {
        Assert-JsonFileUtf8NoBom $jsonPath
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entryNames = @($zip.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
        foreach ($requiredEntry in @(
            "NightOwl.Agent.Windows.exe",
            "NightOwl.Agent.Tray.exe",
            "NightOwl.Agent.Updater.exe",
            "NightOwl.Agent.Diagnostics.exe",
            "NightOwl.Agent.Shared.dll",
            "assets/icons/NightOwl.ico",
            "agent.version.json",
            "release-trust-roots.json"
        )) {
            if ($entryNames -notcontains $requiredEntry) {
                throw "ZIP sem arquivo obrigatorio: $requiredEntry"
            }
        }
        foreach ($entryName in $entryNames) {
            if (Test-ForbiddenZipEntry $entryName) {
                throw "ZIP contem arquivo proibido: $entryName"
            }
        }
        $agentVersionJson = Read-ZipEntryText -Zip $zip -EntryName "agent.version.json"
        Assert-JsonTextNoBom -Name "agent.version.json" -Content $agentVersionJson
        $agentVersion = $agentVersionJson | ConvertFrom-Json
        if ([string]$agentVersion.version -ne $Version) {
            throw "agent.version.json inconsistente. Esperado $Version, obtido $($agentVersion.version)"
        }
    }
    finally {
        $zip.Dispose()
    }

    $zipSha = Get-FileSha256 $zipPath
    $versionManifest = Get-Content -Raw -Path $versionPath | ConvertFrom-Json
    if ([string]$versionManifest.version -ne $Version) { throw "version.json com versao inconsistente." }
    if ([string]$versionManifest.sha256 -ne $zipSha) { throw "version.json com SHA256 inconsistente." }
    if ([long]$versionManifest.size -ne (Get-Item $zipPath).Length) { throw "version.json com tamanho inconsistente." }
    if (-not (Test-Path $signaturePath) -and (-not [bool]$versionManifest.legacyUnsigned -or [string]$versionManifest.channel -ne "development")) {
        throw "release-manifest.sig ausente. Releases pilot/stable e releases assinadas exigem assinatura."
    }

    $checksums = Get-Content -Raw -Path $checksumsPath | ConvertFrom-Json
    $zipEntry = @($checksums.files | Where-Object { $_.name -eq "NightOwl.Agent.Windows.zip" }) | Select-Object -First 1
    if ($null -eq $zipEntry -or [string]$zipEntry.sha256 -ne $zipSha) {
        throw "checksums.json sem SHA256 correto do ZIP."
    }

    Write-Step "Release validada: $Path"
}

function New-Checksums([string]$ReleasePath) {
    $files = @(
        "NightOwl.Agent.Windows.zip",
        "Install-NightOwlAgentDotNet.ps1",
        "Uninstall-NightOwlAgentDotNet.ps1",
        "NightOwl.ico",
        "version.json",
        "release-manifest.json",
        "release-manifest.sig"
    )
    $map = [ordered]@{}
    $items = @()
    foreach ($file in $files) {
        $path = Join-Path $ReleasePath $file
        if (-not (Test-Path $path)) { continue }
        $sha = Get-FileSha256 $path
        $size = (Get-Item $path).Length
        $map[$file] = $sha
        $items += [ordered]@{ name = $file; sha256 = $sha; size = $size }
    }
    $map["files"] = $items
    Write-Utf8NoBomJson -Path (Join-Path $ReleasePath "checksums.json") -Value $map -Depth 8
}

function Publish-LocalAtomic([string]$Source, [string]$DestinationRoot) {
    $destination = Resolve-FullPath $DestinationRoot
    $temp = Join-Path $destination (".nightowl-release-{0}-{1}" -f $Version, ([guid]::NewGuid().ToString("N")))
    $releaseStore = Join-Path $destination "releases\$Version"
    if ((Test-Path $releaseStore) -and -not $Force) {
        throw "Release ja publicada em $releaseStore. Use -Force apenas em desenvolvimento."
    }
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        Copy-Item -Path (Join-Path $Source "*") -Destination $temp -Recurse -Force
        Validate-Release $temp
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $releaseStore) | Out-Null
        if (Test-Path $releaseStore) { Remove-Item -Path $releaseStore -Recurse -Force }
        Move-Item -Path $temp -Destination $releaseStore
        if ($UpdatePublicLatest -or $Channel -eq "stable") {
            foreach ($file in @("Install-NightOwlAgentDotNet.ps1", "Uninstall-NightOwlAgentDotNet.ps1", "NightOwl.ico", "NightOwl.Agent.Windows.zip", "checksums.json", "release-manifest.json")) {
                Copy-Item -Path (Join-Path $releaseStore $file) -Destination (Join-Path $destination $file) -Force
            }
            Copy-Item -Path (Join-Path $releaseStore "version.json") -Destination (Join-Path $destination "version.json") -Force
        }
        else {
            Write-Step "Release $Version publicada apenas em releases/$Version; latest publico preservado."
        }
    }
    catch {
        if (Test-Path $temp) { Remove-Item -Path $temp -Recurse -Force -ErrorAction SilentlyContinue }
        throw
    }
}

function Publish-RemoteAtomic([string]$Source, [string]$HostName, [string]$DestinationRoot, [string]$ExpectedZipSha) {
    $remoteTemp = "$DestinationRoot/.nightowl-release-$Version-$([guid]::NewGuid().ToString('N'))"
    $remoteRelease = "$DestinationRoot/releases/$Version"
    Invoke-Checked "ssh" @($HostName, "mkdir -p '$remoteTemp' '$DestinationRoot/releases'")
    try {
        Invoke-Checked "scp" @("-r", (Join-Path $Source "*"), "${HostName}:$remoteTemp/")
        Invoke-Checked "ssh" @($HostName, "test -s '$remoteTemp/NightOwl.Agent.Windows.zip' && test -s '$remoteTemp/checksums.json' && test -s '$remoteTemp/version.json' && test -s '$remoteTemp/release-manifest.json'")
        Invoke-Checked "ssh" @($HostName, "actual=`$(sha256sum '$remoteTemp/NightOwl.Agent.Windows.zip' | awk '{print `$1}'); test `"x`$actual`" = `"x$ExpectedZipSha`"")
        if (-not $Force) {
            Invoke-Checked "ssh" @($HostName, "if [ -e '$remoteRelease' ]; then echo 'remote release exists: $remoteRelease' >&2; exit 17; fi")
        }
        Invoke-Checked "ssh" @($HostName, "rm -rf '$remoteRelease' && mv '$remoteTemp' '$remoteRelease'")
        if ($UpdatePublicLatest -or $Channel -eq "stable") {
            Invoke-Checked "ssh" @($HostName, "cp '$remoteRelease/Install-NightOwlAgentDotNet.ps1' '$DestinationRoot/Install-NightOwlAgentDotNet.ps1' && cp '$remoteRelease/Uninstall-NightOwlAgentDotNet.ps1' '$DestinationRoot/Uninstall-NightOwlAgentDotNet.ps1' && cp '$remoteRelease/NightOwl.ico' '$DestinationRoot/NightOwl.ico' && cp '$remoteRelease/NightOwl.Agent.Windows.zip' '$DestinationRoot/NightOwl.Agent.Windows.zip' && cp '$remoteRelease/checksums.json' '$DestinationRoot/checksums.json' && cp '$remoteRelease/release-manifest.json' '$DestinationRoot/release-manifest.json' && cp '$remoteRelease/version.json' '$DestinationRoot/version.json'")
        }
        else {
            Write-Step "Release $Version publicada apenas em releases/$Version; latest publico preservado."
        }
    }
    catch {
        & ssh $HostName "rm -rf '$remoteTemp'" 2>$null
        throw
    }
}

if ($SelfTest) {
    Invoke-SelfTest
    return
}

Assert-Version $Version
if ([string]::IsNullOrWhiteSpace($Channel)) {
    $Channel = if ($Version -match "-") { "development" } else { "stable" }
}
if ($Channel -ne "stable" -and $UpdatePublicLatest) {
    throw "-UpdatePublicLatest so pode ser usado com canal stable."
}

if ([string]::IsNullOrWhiteSpace($ReleaseDir)) {
    $ReleaseDir = Join-Path $releaseRoot $Version
}
$ReleaseDir = Resolve-FullPath $ReleaseDir

if ($ValidateOnly) {
    Validate-Release $ReleaseDir
    return
}

$currentVersion = Get-CurrentProjectVersion
if ((Compare-NightOwlVersion $Version $currentVersion) -lt 0 -and -not $Force) {
    throw "Downgrade bloqueado. Projeto atual: $currentVersion, solicitado: $Version. Use -Force apenas em desenvolvimento."
}
if ((Test-Path $ReleaseDir) -and -not $Force) {
    throw "Release ja existe: $ReleaseDir. Use -Force apenas em desenvolvimento."
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
if (Test-Path $ReleaseDir) { Remove-Item -Path $ReleaseDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$buildId = [guid]::NewGuid().ToString("N")
$builtAt = (Get-Date).ToUniversalTime().ToString("o")
$commit = Get-GitCommit
$workDir = Join-Path $workRoot "$Version-$buildId"
$publishDir = Join-Path $workDir "publish"
$packageDir = Join-Path $workDir "package"
New-Item -ItemType Directory -Force -Path $publishDir,$packageDir | Out-Null
$releaseReady = $false

$assemblyVersion = ((Get-VersionCore $Version).Split(".") + @("0", "0", "0", "0"))[0..3] -join "."
$msbuildVersionArgs = @(
    "-p:Version=$Version",
    "-p:AssemblyVersion=$assemblyVersion",
    "-p:FileVersion=$assemblyVersion",
    "-p:InformationalVersion=$Version+$buildId"
)
$projects = @($sharedProject, $agentProject, $trayProject, $updaterProject, $diagnosticsProject, $testProject, $updaterTestProject)

try {
    foreach ($project in @($sharedProject, $agentProject, $trayProject, $updaterProject, $diagnosticsProject, $testProject, $updaterTestProject)) {
        Invoke-Checked "dotnet" @("clean", $project, "-c", "Release")
    }

    foreach ($project in @($sharedProject, $testProject, $updaterTestProject)) {
        Invoke-Checked "dotnet" @("restore", $project)
    }

    foreach ($project in @($agentProject, $trayProject, $updaterProject, $diagnosticsProject)) {
        Invoke-Checked "dotnet" @("restore", $project, "-r", $Runtime)
    }

    foreach ($project in @($sharedProject, $agentProject, $trayProject, $updaterProject, $diagnosticsProject, $testProject, $updaterTestProject)) {
        Invoke-Checked "dotnet" (@("build", $project, "-c", "Release", "--no-restore") + $msbuildVersionArgs)
    }

    if ($SkipTests) {
        Write-Step "SkipTests ativo; build/publish serao executados sem rodar testes .NET."
    }
    else {
        Invoke-Checked "dotnet" (@("test", $testProject, "-c", "Release", "--no-restore", "--no-build") + $msbuildVersionArgs)
        Invoke-Checked "dotnet" @("run", "--project", $testProject, "-c", "Release", "--no-restore")
        Invoke-Checked "dotnet" @("run", "--project", $updaterTestProject, "-c", "Release", "--no-restore")
    }

    foreach ($project in @($agentProject, $trayProject, $updaterProject, $diagnosticsProject)) {
        Invoke-Checked "dotnet" (@("publish", $project, "-c", "Release", "-r", $Runtime, "--self-contained", "true", "-o", $publishDir, "--no-restore") + $msbuildVersionArgs)
    }

    Copy-ReleasePayload -PublishDir $publishDir -PackageDir $packageDir
    $packageIconDir = Join-Path $packageDir "assets\icons"
    New-Item -ItemType Directory -Force -Path $packageIconDir | Out-Null
    Copy-Item -Path $iconPath -Destination (Join-Path $packageIconDir "NightOwl.ico") -Force
    New-AgentVersionFile -Path (Join-Path $packageDir "agent.version.json") -BuildId $buildId -BuiltAt $builtAt -Commit $commit
    if (-not [string]::IsNullOrWhiteSpace($SigningKeyPath)) {
        Write-TrustedReleasePublicKeys -Path (Join-Path $packageDir "release-public-keys.json") -PrivateKeyPath $SigningKeyPath -KeyId $SigningKeyId -TrustedPublicKeysPath $TrustedPublicKeysPath
    }
    Copy-ReleaseTrustRoots -SourcePath $TrustRootsPath -PackageDir $packageDir

    $zipPath = Join-Path $ReleaseDir "NightOwl.Agent.Windows.zip"
    Compress-ReleaseZip -PackageDir $packageDir -ZipPath $zipPath
    $zipSha = Get-FileSha256 $zipPath
    $zipSize = (Get-Item $zipPath).Length

    Copy-Item -Path $installScript -Destination (Join-Path $ReleaseDir "Install-NightOwlAgentDotNet.ps1") -Force
    Copy-Item -Path $uninstallScript -Destination (Join-Path $ReleaseDir "Uninstall-NightOwlAgentDotNet.ps1") -Force
    Copy-Item -Path $iconPath -Destination (Join-Path $ReleaseDir "NightOwl.ico") -Force

    $zipSha = Get-FileSha256 $zipPath
    $zipSize = (Get-Item $zipPath).Length
    $artifactBaseUrl = if ($UpdatePublicLatest -or $Channel -eq "stable") {
        $PublicBaseUrl.TrimEnd("/")
    }
    else {
        "{0}/releases/{1}" -f $PublicBaseUrl.TrimEnd("/"), $Version
    }
    $manifestPath = Join-Path $ReleaseDir "release-manifest.json"
    $signaturePath = Join-Path $ReleaseDir "release-manifest.sig"
    $signed = -not [string]::IsNullOrWhiteSpace($SigningKeyPath)
    if (-not $signed -and (-not $AllowUnsignedDevelopment -or $Channel -ne "development")) {
        throw "Chave privada obrigatoria para assinar release. Use -SigningKeyPath ou NIGHTOWL_RELEASE_SIGNING_KEY. Bypass somente com -AllowUnsignedDevelopment em development."
    }
    $versionManifest = [ordered]@{
        product = "NightOwl Agent Windows"
        agent = "NightOwl.Agent.Windows"
        channel = $Channel
        version = $Version
        publishedAt = $builtAt
        published_at = $builtAt
        minimumSupportedVersion = "0.1.0"
        minimum_updater_version = $MinimumUpdaterVersion
        packageUrl = ("{0}/NightOwl.Agent.Windows.zip" -f $artifactBaseUrl)
        checksumUrl = ("{0}/checksums.json" -f $artifactBaseUrl)
        installerUrl = ("{0}/Install-NightOwlAgentDotNet.ps1" -f $artifactBaseUrl)
        notes = "Release $Version do NightOwl Agent Windows."
        requiresRestart = $true
        force = $false
        platform = "windows-x64"
        package = "NightOwl.Agent.Windows.zip"
        manifestUrl = ("{0}/release-manifest.json" -f $artifactBaseUrl)
        signatureUrl = if ($signed) { ("{0}/release-manifest.sig" -f $artifactBaseUrl) } else { "" }
        key_id = if ($signed) { $SigningKeyId } else { "" }
        sha256 = $zipSha
        size = $zipSize
        manifest_sha256 = ""
        signature_sha256 = ""
        signature_key_id = if ($signed) { $SigningKeyId } else { "" }
        legacyUnsigned = -not $signed
        build_id = $buildId
        git_commit = $commit
    }

    $releaseManifest = [ordered]@{
        schema_version = 1
        product = "NightOwl Agent Windows"
        version = $Version
        channel = $Channel
        build_id = $buildId
        built_at = $builtAt
        git_commit = $commit
        runtime = $Runtime
        public_base_url = $PublicBaseUrl
        artifact_base_url = $artifactBaseUrl
        initial_channel = $Channel
        minimum_updater_version = $MinimumUpdaterVersion
        published_at = $builtAt
        key_id = if ($signed) { $SigningKeyId } else { "" }
        legacy_unsigned = -not $signed
        rollout_percentage = 0
        rollout_paused = $true
        auto_publish_latest = [bool]$UpdatePublicLatest
        package = [ordered]@{
            filename = "NightOwl.Agent.Windows.zip"
            name = "NightOwl.Agent.Windows.zip"
            sha256 = $zipSha
            size = $zipSize
        }
        required_zip_entries = @(
            "NightOwl.Agent.Windows.exe",
            "NightOwl.Agent.Tray.exe",
            "NightOwl.Agent.Updater.exe",
            "NightOwl.Agent.Diagnostics.exe",
            "NightOwl.Agent.Shared.dll",
            "assets/icons/NightOwl.ico",
            "agent.version.json",
            "release-trust-roots.json"
        )
        forbidden_patterns = @("agent.config.json", "agent.identity.json", "agent.state.json", "agent-dotnet.state.json", "update-state.json", "*.preserved-*", "*.log", "*.tmp", "*.pdb", "*.ps1", "bin/", "obj/", "publish/", "downloads/", "artifacts/", "releases/")
    }
    Write-CanonicalJson -Path $manifestPath -Value $releaseManifest
    $versionManifest.manifest_sha256 = Get-FileSha256 $manifestPath
    if ($signed) {
        Sign-ReleaseManifest -ManifestPath $manifestPath -SignaturePath $signaturePath -PrivateKeyPath $SigningKeyPath -KeyId $SigningKeyId
        $versionManifest.signature_sha256 = Get-FileSha256 $signaturePath
    }
    Write-Utf8NoBomJson -Path (Join-Path $ReleaseDir "version.json") -Value $versionManifest -Depth 8

    New-Checksums $ReleaseDir
    Validate-Release $ReleaseDir
    $releaseReady = $true

    if ($Publish) {
        if ([string]::IsNullOrWhiteSpace($PublishHost)) {
            Publish-LocalAtomic -Source $ReleaseDir -DestinationRoot $PublishPath
        }
        else {
            Publish-RemoteAtomic -Source $ReleaseDir -HostName $PublishHost -DestinationRoot $PublishPath -ExpectedZipSha $zipSha
        }
        Write-Step "Publicacao concluida."
    }

    Write-Step "Release pronta em: $ReleaseDir"
}
catch {
    if (-not $releaseReady -and (Test-Path $ReleaseDir)) {
        Remove-Item -Path $ReleaseDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Error $_
    throw
}
finally {
    if (Test-Path $workDir) {
        Remove-Item -Path $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
