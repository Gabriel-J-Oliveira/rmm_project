param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$RemoteAlias = "nightowl-release",
    [string]$RemoteProjectPath = "/opt/nightowl",
    [string]$PublicBaseUrl = "https://nightowl.controlsul.com.br/downloads/nightowl-agent",

    [ValidateSet("development", "pilot", "stable")]
    [string]$Channel = "development",

    [ValidateRange(0, 100)]
    [int]$Rollout = 0,

    [bool]$Paused = $true,

    [switch]$SkipBuild,
    [switch]$SkipUpload,
    [switch]$SkipImport,
    [switch]$Force,
    [switch]$KeepLocalWork,
    [switch]$PruneOldReleases,
    [ValidateRange(1, 50)]
    [int]$KeepLastReleases = 5,
    [switch]$DryRun,
    [switch]$ValidateOnly,
    [switch]$ResumeImport,
    [switch]$SkipTests,
    [switch]$SaveConfig,
    [switch]$AllowDirtyWorkingTree,
    [string]$ConfigPath = (Join-Path $env:USERPROFILE ".nightowl\release-publisher.json"),
    [string]$SigningKeyPath = $env:NIGHTOWL_RELEASE_SIGNING_KEY,
    [string]$SigningKeyId = $env:NIGHTOWL_RELEASE_SIGNING_KEY_ID,
    [string]$TrustedPublicKeysPath = $env:NIGHTOWL_RELEASE_TRUSTED_KEYS_JSON,
    [switch]$AllowUnsignedDevelopment,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$script:InitialBoundParameters = @{}
foreach ($key in $PSBoundParameters.Keys) {
    $script:InitialBoundParameters[$key] = $true
}

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:ReleaseRoot = Join-Path $script:RepoRoot "artifacts\nightowl-agent\releases"
$script:BuildScript = Join-Path $script:RepoRoot "scripts\Build-NightOwlAgentRelease.ps1"
$script:RequiredFiles = @(
    "NightOwl.Agent.Windows.zip",
    "Install-NightOwlAgentDotNet.ps1",
    "Uninstall-NightOwlAgentDotNet.ps1",
    "NightOwl.ico",
    "checksums.json",
    "version.json",
    "release-manifest.json"
)

$script:ExitCodes = @{
    validation_failed = 2
    build_failed = 10
    ssh_failed = 20
    upload_failed = 30
    checksum_failed = 40
    http_validation_failed = 50
    import_failed = 60
}
$script:DefaultRemoteAlias = "nightowl-release"
$script:DefaultRemoteProjectPath = "/opt/nightowl"
$script:DefaultPublicBaseUrl = "https://nightowl.controlsul.com.br/downloads/nightowl-agent"

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-release-publish] {0}" -f $Message)
}

function Fail([string]$Code, [string]$Message) {
    $exitCode = if ($script:ExitCodes.ContainsKey($Code)) { $script:ExitCodes[$Code] } else { 1 }
    $ex = New-Object System.Exception($Message)
    $ex.Data["ExitCode"] = $exitCode
    $ex.Data["Code"] = $Code
    throw $ex
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
    Write-Utf8NoBomText -Path $Path -Content ($Value | ConvertTo-Json -Depth $Depth)
}

function Read-PublisherConfig([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    }
    catch {
        Fail "validation_failed" "Configuracao local invalida em $Path`: $($_.Exception.Message)"
    }
}

function Resolve-ConfigValue([string]$ParameterName, [string]$CurrentValue, [string]$EnvironmentValue, $ConfigValue, [string]$DefaultValue) {
    if ($script:InitialBoundParameters.ContainsKey($ParameterName) -and -not [string]::IsNullOrWhiteSpace($CurrentValue)) { return $CurrentValue }
    if (-not [string]::IsNullOrWhiteSpace($EnvironmentValue)) { return $EnvironmentValue }
    if ($null -ne $ConfigValue -and -not [string]::IsNullOrWhiteSpace([string]$ConfigValue)) { return [string]$ConfigValue }
    return $CurrentValue
}

function Initialize-PublisherConfiguration {
    $config = Read-PublisherConfig $ConfigPath
    if ($null -ne $config) {
        $script:SigningKeyPath = Resolve-ConfigValue "SigningKeyPath" $SigningKeyPath $env:NIGHTOWL_RELEASE_SIGNING_KEY $config.signing_key_path ""
        $script:SigningKeyId = Resolve-ConfigValue "SigningKeyId" $SigningKeyId $env:NIGHTOWL_RELEASE_SIGNING_KEY_ID $config.signing_key_id ""
        $script:TrustedPublicKeysPath = Resolve-ConfigValue "TrustedPublicKeysPath" $TrustedPublicKeysPath $env:NIGHTOWL_RELEASE_TRUSTED_KEYS_JSON $config.trusted_public_keys_path ""
        $script:RemoteAlias = Resolve-ConfigValue "RemoteAlias" $RemoteAlias $env:NIGHTOWL_RELEASE_REMOTE_ALIAS $config.remote_alias $script:DefaultRemoteAlias
        $script:RemoteProjectPath = Resolve-ConfigValue "RemoteProjectPath" $RemoteProjectPath $env:NIGHTOWL_RELEASE_REMOTE_PROJECT_PATH $config.remote_project_path $script:DefaultRemoteProjectPath
        $script:PublicBaseUrl = Resolve-ConfigValue "PublicBaseUrl" $PublicBaseUrl $env:NIGHTOWL_RELEASE_PUBLIC_BASE_URL $config.public_base_url $script:DefaultPublicBaseUrl
    }
    else {
        if (-not [string]::IsNullOrWhiteSpace($env:NIGHTOWL_RELEASE_REMOTE_ALIAS) -and $RemoteAlias -eq $script:DefaultRemoteAlias) { $script:RemoteAlias = $env:NIGHTOWL_RELEASE_REMOTE_ALIAS }
        if (-not [string]::IsNullOrWhiteSpace($env:NIGHTOWL_RELEASE_REMOTE_PROJECT_PATH) -and $RemoteProjectPath -eq $script:DefaultRemoteProjectPath) { $script:RemoteProjectPath = $env:NIGHTOWL_RELEASE_REMOTE_PROJECT_PATH }
        if (-not [string]::IsNullOrWhiteSpace($env:NIGHTOWL_RELEASE_PUBLIC_BASE_URL) -and $PublicBaseUrl -eq $script:DefaultPublicBaseUrl) { $script:PublicBaseUrl = $env:NIGHTOWL_RELEASE_PUBLIC_BASE_URL }
    }

    if ($SaveConfig) {
        $safeConfig = [ordered]@{
            signing_key_path = $SigningKeyPath
            signing_key_id = $SigningKeyId
            trusted_public_keys_path = $TrustedPublicKeysPath
            remote_alias = $RemoteAlias
            remote_project_path = $RemoteProjectPath
            public_base_url = $PublicBaseUrl
        }
        Write-Utf8NoBomJson -Path $ConfigPath -Value $safeConfig -Depth 4
        Write-Step "Configuracao local salva em $ConfigPath (sem material de chave privada)."
    }
}

function Assert-GitStatusClean {
    $gitStatus = (& git -C $script:RepoRoot status --porcelain 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Write-Step "Aviso: git status nao disponivel; seguindo sem validacao de worktree."
        return
    }
    if (-not [string]::IsNullOrWhiteSpace(($gitStatus | Out-String))) {
        $message = "Worktree possui alteracoes nao commitadas. Commit atual ainda sera usado no manifesto, mas a release nao seria totalmente reproduzivel.`n$($gitStatus | Out-String)"
        if (-not $AllowDirtyWorkingTree) {
            Fail "validation_failed" "$message`nUse -AllowDirtyWorkingTree apenas para laboratorio."
        }
        Write-Step "Aviso: $message"
    }
}

function Assert-RsaPssProviderAvailable {
    if ($null -eq ("System.Security.Cryptography.RSACng" -as [type])) {
        Fail "validation_failed" "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: provedor CNG RSA indisponivel; RSA-PSS e obrigatorio."
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
        Fail "validation_failed" "RELEASE_SIGNING_KEY_INVALID: chave RSA XML invalida ou incompleta. Detalhe: $($_.Exception.Message)"
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
    Assert-RsaPssProviderAvailable
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        Fail "validation_failed" "Chave privada de assinatura nao encontrada. Configure signing_key_path, -SigningKeyPath ou NIGHTOWL_RELEASE_SIGNING_KEY."
    }
    $rsa = New-Object System.Security.Cryptography.RSACng
    try {
        $rsa.ImportParameters((Import-RsaParametersFromXml (Get-Content -Raw -LiteralPath $Path) $true))
        return $rsa
    }
    catch {
        $rsa.Dispose()
        Fail "validation_failed" "RELEASE_SIGNING_KEY_INVALID: chave privada nao pode assinar com RSA-PSS/CNG. Detalhe: $($_.Exception.Message)"
    }
}

function New-RsaPssPublicKeyFromXmlText([string]$Xml) {
    Assert-RsaPssProviderAvailable
    $rsa = New-Object System.Security.Cryptography.RSACng
    try {
        $rsa.ImportParameters((Import-RsaParametersFromXml $Xml $false))
        return $rsa
    }
    catch {
        $rsa.Dispose()
        Fail "validation_failed" "RELEASE_PUBLIC_KEY_INVALID: chave publica nao pode verificar RSA-PSS/CNG. Detalhe: $($_.Exception.Message)"
    }
}

function Test-PublicKeyXmlHasPrivateParameters([string]$Xml) {
    foreach ($privateElement in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
        if ($Xml -match ("<\s*{0}\s*>" -f $privateElement)) { return $true }
    }
    return $false
}

function Assert-SigningMaterial {
    if ($AllowUnsignedDevelopment -and $Channel -eq "development") {
        Write-Step "AllowUnsignedDevelopment ativo; assinatura forte nao sera exigida neste build de laboratorio."
        return
    }
    if ([string]::IsNullOrWhiteSpace($SigningKeyId)) {
        Fail "validation_failed" "SigningKeyId obrigatorio. Configure signing_key_id, -SigningKeyId ou NIGHTOWL_RELEASE_SIGNING_KEY_ID."
    }
    if ([string]::IsNullOrWhiteSpace($TrustedPublicKeysPath) -or -not (Test-Path -LiteralPath $TrustedPublicKeysPath)) {
        Fail "validation_failed" "Bundle publico nao encontrado. Configure trusted_public_keys_path ou NIGHTOWL_RELEASE_TRUSTED_KEYS_JSON."
    }

    $bundle = Read-JsonFile $TrustedPublicKeysPath
    $keyIds = @{}
    $matching = $null
    foreach ($item in @($bundle.keys)) {
        $itemKeyId = [string]$item.key_id
        if ([string]::IsNullOrWhiteSpace($itemKeyId)) { Fail "validation_failed" "release-public-keys.json contem key_id vazio." }
        if ($keyIds.ContainsKey($itemKeyId)) { Fail "validation_failed" "release-public-keys.json contem key_id duplicado: $itemKeyId" }
        $keyIds[$itemKeyId] = $true
        if ($itemKeyId -eq $SigningKeyId) { $matching = $item }
        if ([string]$item.algorithm -ne "RSA-PSS-SHA256") { Fail "validation_failed" "Algoritmo nao permitido no bundle: $($item.algorithm)" }
        if (Test-PublicKeyXmlHasPrivateParameters ([string]$item.public_key_xml)) {
            Fail "validation_failed" "release-public-keys.json contem parametros privados para key_id $itemKeyId."
        }
    }
    if ($null -eq $matching) {
        Fail "validation_failed" "key_id $SigningKeyId nao encontrado em $TrustedPublicKeysPath."
    }
    if ([string]$matching.status -eq "revoked") {
        Fail "validation_failed" "key_id $SigningKeyId esta revogado no bundle publico."
    }

    $privateRsa = New-RsaPssPrivateKeyFromXml $SigningKeyPath
    $publicRsa = New-RsaPssPublicKeyFromXmlText ([string]$matching.public_key_xml)
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes("nightowl-release-publisher-key-match")
        $signature = $privateRsa.SignData($bytes, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
        $valid = $publicRsa.VerifyData($bytes, $signature, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
        if (-not $valid) {
            Fail "validation_failed" "Bundle publico nao corresponde a chave privada informada para key_id $SigningKeyId."
        }
    }
    finally {
        $publicRsa.Dispose()
        $privateRsa.Dispose()
    }
    Write-Step "Chave privada, key_id e bundle publico validados com RSA-PSS/SHA-256."
}

function Assert-Version([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Fail "validation_failed" "Version nao pode ser vazia."
    }
    if ($Value -notmatch '^\d+\.\d+\.\d+(\.\d+)?(-[0-9A-Za-z][0-9A-Za-z.-]*)?$') {
        Fail "validation_failed" "Version invalida: $Value. Use major.minor.patch[.build][-prerelease]."
    }
}

function Assert-SafeRemoteSegment([string]$Value) {
    if ($Value -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]*$') {
        Fail "validation_failed" "Segmento de path remoto inseguro: $Value"
    }
}

function ConvertTo-BashSingleQuoted([string]$Value) {
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function Invoke-Native([string]$FileName, [string[]]$Arguments, [string]$FailureCode) {
    Write-Step ("Executando: {0} {1}" -f $FileName, ($Arguments -join " "))
    if ($DryRun) {
        return @()
    }
    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        & $FileName @Arguments > $stdoutFile 2> $stderrFile
        $nativeExitCode = $LASTEXITCODE
        $stdout = Get-Content -Path $stdoutFile -Raw -ErrorAction SilentlyContinue
        $stderr = Get-Content -Path $stderrFile -Raw -ErrorAction SilentlyContinue
        if ($nativeExitCode -ne 0) {
            $message = @(
                "Comando executado: $FileName $($Arguments -join ' ')",
                "Exit code: $nativeExitCode",
                "Pipeline error code: $FailureCode",
                "STDOUT:",
                ($stdout -replace '\s+$', ''),
                "STDERR:",
                ($stderr -replace '\s+$', '')
            ) -join "`n"
            Fail $FailureCode $message
        }
        return @($stdout -split "`r?`n" | Where-Object { $_ -ne "" })
    }
    finally {
        Remove-Item -LiteralPath $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

function New-BuildReleaseArguments([string]$RequestedVersion, [string]$RequestedChannel, [string]$RequestedPublicBaseUrl, [bool]$AllowForce) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $script:BuildScript,
        "-Version", $RequestedVersion,
        "-Channel", $RequestedChannel,
        "-PublicBaseUrl", $RequestedPublicBaseUrl
    )
    if (-not [string]::IsNullOrWhiteSpace($SigningKeyPath)) {
        $arguments += @("-SigningKeyPath", $SigningKeyPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($SigningKeyId)) {
        $arguments += @("-SigningKeyId", $SigningKeyId)
    }
    if (-not [string]::IsNullOrWhiteSpace($TrustedPublicKeysPath)) {
        $arguments += @("-TrustedPublicKeysPath", $TrustedPublicKeysPath)
    }
    if ($AllowUnsignedDevelopment) {
        $arguments += "-AllowUnsignedDevelopment"
    }
    if ($SkipTests) {
        $arguments += "-SkipTests"
    }
    if ($AllowForce) {
        $arguments += "-Force"
    }
    return ,$arguments
}

function Assert-ArrayDoesNotContainFalseSwitch([string[]]$Arguments) {
    foreach ($argument in $Arguments) {
        if ($argument -match '^-[-A-Za-z0-9]+:(False|false|\$false)$') {
            Fail "validation_failed" "Switch falso serializado incorretamente: $argument"
        }
    }
}

function Invoke-SelfTest {
    $oldBuildScript = $script:BuildScript
    $oldSkipTests = $script:SkipTests
    $oldSigningKeyPath = $script:SigningKeyPath
    $oldSigningKeyId = $script:SigningKeyId
    $oldTrustedPublicKeysPath = $script:TrustedPublicKeysPath
    $oldAllowUnsignedDevelopment = $script:AllowUnsignedDevelopment
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-publisher-selftest-{0}" -f ([guid]::NewGuid().ToString("N")))
    try {
        New-Item -ItemType Directory -Force -Path $temp | Out-Null
        $script:BuildScript = "C:\Path With Spaces\Build-NightOwlAgentRelease.ps1"
        $script:SkipTests = $true
        $withoutForce = New-BuildReleaseArguments -RequestedVersion "0.1.1.0-rc4" -RequestedChannel "development" -RequestedPublicBaseUrl "https://nightowl.controlsul.com.br/downloads/nightowl-agent?x=1&y=2" -AllowForce $false
        Assert-ArrayDoesNotContainFalseSwitch $withoutForce
        if ($withoutForce -contains "-Force") {
            Fail "validation_failed" "SelfTest falhou: Force false incluiu -Force."
        }
        if ($withoutForce -notcontains "-SkipTests") {
            Fail "validation_failed" "SelfTest falhou: SkipTests true nao incluiu -SkipTests."
        }
        if (@($withoutForce | Where-Object { $_ -eq "-Force" }).Count -ne 0) {
            Fail "validation_failed" "SelfTest falhou: Force false duplicou -Force."
        }
        if ($withoutForce[4] -ne "C:\Path With Spaces\Build-NightOwlAgentRelease.ps1") {
            Fail "validation_failed" "SelfTest falhou: path com espacos nao foi preservado."
        }

        $withForce = New-BuildReleaseArguments -RequestedVersion "0.1.1.0-rc4" -RequestedChannel "development" -RequestedPublicBaseUrl "https://nightowl.controlsul.com.br/downloads/nightowl-agent" -AllowForce $true
        Assert-ArrayDoesNotContainFalseSwitch $withForce
        if (@($withForce | Where-Object { $_ -eq "-Force" }).Count -ne 1) {
            Fail "validation_failed" "SelfTest falhou: Force true deve incluir -Force exatamente uma vez."
        }
        if ($withForce -contains "-Force:False" -or $withForce -contains "-Force:$false") {
            Fail "validation_failed" "SelfTest falhou: Force true/false gerou sintaxe :False."
        }

        $legacyProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
        try {
            $legacyProvider.PersistKeyInCsp = $false
            $privatePath = Join-Path $temp "private.xml"
            $bundlePath = Join-Path $temp "release-public-keys.json"
            Write-Utf8NoBomText -Path $privatePath -Content $legacyProvider.ToXmlString($true)
            $bundle = [ordered]@{
                keys = @([ordered]@{
                    key_id = "nightowl-selftest"
                    algorithm = "RSA-PSS-SHA256"
                    public_key_xml = $legacyProvider.ToXmlString($false)
                    status = "active"
                    valid_from = "2026-01-01T00:00:00Z"
                    valid_until = ""
                    revoked_at = ""
                })
            }
            Write-Utf8NoBomJson -Path $bundlePath -Value $bundle -Depth 8
        }
        finally {
            $legacyProvider.PersistKeyInCsp = $false
            $legacyProvider.Clear()
            $legacyProvider.Dispose()
        }

        $script:SigningKeyPath = $privatePath
        $script:SigningKeyId = "nightowl-selftest"
        $script:TrustedPublicKeysPath = $bundlePath
        $script:AllowUnsignedDevelopment = $false
        Assert-SigningMaterial

        Write-Step "SelfTest OK: argumentos do build montados sem switches falsos."
        Write-Step ("Force false: powershell.exe {0}" -f ($withoutForce -join " "))
        Write-Step ("Force true:  powershell.exe {0}" -f ($withForce -join " "))
    }
    finally {
        $script:BuildScript = $oldBuildScript
        $script:SkipTests = $oldSkipTests
        $script:SigningKeyPath = $oldSigningKeyPath
        $script:SigningKeyId = $oldSigningKeyId
        $script:TrustedPublicKeysPath = $oldTrustedPublicKeysPath
        $script:AllowUnsignedDevelopment = $oldAllowUnsignedDevelopment
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Invoke-Ssh([string]$Command, [string]$FailureCode = "ssh_failed") {
    $quoted = ConvertTo-BashSingleQuoted $Command
    return Invoke-Native "ssh.exe" @($RemoteAlias, "bash", "-lc", $quoted) $FailureCode
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function Read-JsonFile([string]$Path) {
    try {
        return Get-Content -Path $Path -Raw | ConvertFrom-Json
    }
    catch {
        Fail "validation_failed" "JSON invalido em $Path`: $($_.Exception.Message)"
    }
}

function Test-FileHasUtf8Bom([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    return $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
}

function Assert-JsonFileUtf8NoBom([string]$Path) {
    if (Test-FileHasUtf8Bom $Path) {
        Fail "validation_failed" "JSON contem UTF-8 BOM: $Path"
    }
    $null = Read-JsonFile $Path
}

function Read-ZipEntryBytes([string]$ZipPath, [string]$EntryName) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $entry = $zip.GetEntry($EntryName)
        if ($null -eq $entry) { Fail "validation_failed" "ZIP sem entrada obrigatoria: $EntryName" }
        $stream = $entry.Open()
        try {
            $memory = New-Object System.IO.MemoryStream
            try {
                $stream.CopyTo($memory)
                return ,$memory.ToArray()
            }
            finally { $memory.Dispose() }
        }
        finally { $stream.Dispose() }
    }
    finally { $zip.Dispose() }
}

function Read-ZipEntryJson([string]$ZipPath, [string]$EntryName) {
    $bytes = Read-ZipEntryBytes -ZipPath $ZipPath -EntryName $EntryName
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        Fail "validation_failed" "$EntryName dentro do ZIP contem UTF-8 BOM."
    }
    try {
        return [System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
    }
    catch {
        Fail "validation_failed" "$EntryName dentro do ZIP e JSON invalido: $($_.Exception.Message)"
    }
}

function Assert-ReleaseSignatureWithPackagedBundle([string]$ReleaseDir, $VersionJson) {
    if ([bool]$VersionJson.legacyUnsigned) {
        if ($Channel -eq "development" -and $AllowUnsignedDevelopment) {
            Write-Step "Release development legacy_unsigned permitida apenas por -AllowUnsignedDevelopment."
            return
        }
        Fail "validation_failed" "Release assinada obrigatoria; legacyUnsigned=true nao e permitido neste fluxo."
    }
    $manifestPath = Join-Path $ReleaseDir "release-manifest.json"
    $signaturePath = Join-Path $ReleaseDir "release-manifest.sig"
    $zipPath = Join-Path $ReleaseDir "NightOwl.Agent.Windows.zip"
    foreach ($required in @($manifestPath, $signaturePath, $zipPath)) {
        if (-not (Test-Path -LiteralPath $required)) { Fail "validation_failed" "Artefato assinado obrigatorio ausente: $required" }
    }
    $bundle = Read-ZipEntryJson -ZipPath $zipPath -EntryName "release-public-keys.json"
    $keyId = [string]$VersionJson.signature_key_id
    if ([string]::IsNullOrWhiteSpace($keyId)) { $keyId = [string]$VersionJson.key_id }
    $key = @($bundle.keys | Where-Object { [string]$_.key_id -eq $keyId }) | Select-Object -First 1
    if ($null -eq $key) { Fail "validation_failed" "release-public-keys.json do pacote nao contem key_id $keyId." }
    if ([string]$key.status -eq "revoked") { Fail "validation_failed" "release-public-keys.json do pacote marca key_id $keyId como revogado." }
    if ([string]$key.algorithm -ne "RSA-PSS-SHA256") { Fail "validation_failed" "Algoritmo invalido no bundle do pacote: $($key.algorithm)" }
    if (Test-PublicKeyXmlHasPrivateParameters ([string]$key.public_key_xml)) {
        Fail "validation_failed" "Bundle publico do ZIP contem parametros privados."
    }
    $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
    $signatureText = (Get-Content -Raw -LiteralPath $signaturePath).Trim()
    try { $signatureBytes = [Convert]::FromBase64String($signatureText) }
    catch { Fail "validation_failed" "release-manifest.sig nao esta em Base64 valido." }
    $publicRsa = New-RsaPssPublicKeyFromXmlText ([string]$key.public_key_xml)
    try {
        $valid = $publicRsa.VerifyData($manifestBytes, $signatureBytes, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
        if (-not $valid) {
            Fail "validation_failed" "Assinatura do manifesto nao valida contra release-public-keys.json incluido no pacote."
        }
    }
    finally { $publicRsa.Dispose() }
    Write-Step "Assinatura local validada contra o bundle publico empacotado."
}

function Get-ChecksumEntry($Checksums, [string]$Name) {
    return @($Checksums.files | Where-Object { $_.name -eq $Name }) | Select-Object -First 1
}

function Assert-LocalRelease([string]$ReleaseDir) {
    if (-not (Test-Path $ReleaseDir)) {
        Fail "validation_failed" "Release local nao encontrada: $ReleaseDir"
    }
    foreach ($file in $script:RequiredFiles) {
        $path = Join-Path $ReleaseDir $file
        if (-not (Test-Path $path)) {
            Fail "validation_failed" "Artefato obrigatorio ausente: $path"
        }
    }

    $versionJson = Read-JsonFile (Join-Path $ReleaseDir "version.json")
    $checksumsJson = Read-JsonFile (Join-Path $ReleaseDir "checksums.json")
    Assert-JsonFileUtf8NoBom (Join-Path $ReleaseDir "version.json")
    Assert-JsonFileUtf8NoBom (Join-Path $ReleaseDir "checksums.json")
    Assert-JsonFileUtf8NoBom (Join-Path $ReleaseDir "release-manifest.json")

    if ([string]$versionJson.version -ne $Version) {
        Fail "validation_failed" "version.json declara $($versionJson.version), esperado $Version."
    }
    $zipPath = Join-Path $ReleaseDir "NightOwl.Agent.Windows.zip"
    $zipSha = Get-FileSha256 $zipPath
    $zipSize = (Get-Item $zipPath).Length
    if ([string]$versionJson.sha256 -ne $zipSha) {
        Fail "checksum_failed" "SHA256 local do ZIP diverge de version.json."
    }
    if ([long]$versionJson.size -ne $zipSize) {
        Fail "checksum_failed" "Tamanho local do ZIP diverge de version.json."
    }
    $zipEntry = Get-ChecksumEntry $checksumsJson "NightOwl.Agent.Windows.zip"
    if ($null -eq $zipEntry -or [string]$zipEntry.sha256 -ne $zipSha) {
        Fail "checksum_failed" "checksums.json sem SHA256 correto do ZIP."
    }
    Assert-ReleaseSignatureWithPackagedBundle -ReleaseDir $ReleaseDir -VersionJson $versionJson
    return [ordered]@{
        VersionJson = $versionJson
        ChecksumsJson = $checksumsJson
        ZipSha = $zipSha
        ZipSize = $zipSize
    }
}

function Test-SshNoPassword {
    if ($DryRun) {
        Write-Step "DryRun: pular teste SSH real para $RemoteAlias"
        return
    }
    $result = Invoke-Native "ssh.exe" @("-o", "BatchMode=yes", $RemoteAlias, "echo ok") "ssh_failed"
    if (($result | Out-String).Trim() -ne "ok") {
        Fail "ssh_failed" "SSH sem senha nao retornou ok para $RemoteAlias."
    }
}

function Assert-RemoteServerConfiguration {
    if ($DryRun) {
        Write-Step "DryRun: pular validacao real de diretorios e Python no servidor."
        return
    }
    $releaseRootRemote = "$RemoteProjectPath/downloads/agent/windows/releases"
    $command = @"
set -euo pipefail
test -d $(ConvertTo-BashSingleQuoted $RemoteProjectPath)
test -d $(ConvertTo-BashSingleQuoted "$RemoteProjectPath/downloads/agent/windows") || mkdir -p $(ConvertTo-BashSingleQuoted "$RemoteProjectPath/downloads/agent/windows")
test -d $(ConvertTo-BashSingleQuoted "$RemoteProjectPath/downloads/agent/windows")
test -x $(ConvertTo-BashSingleQuoted "$RemoteProjectPath/.venv/bin/python") || true
mkdir -p $(ConvertTo-BashSingleQuoted $releaseRootRemote)
python3 --version >/dev/null
echo ok
"@
    $result = Invoke-Ssh $command "ssh_failed"
    if (($result | Out-String).Trim() -notmatch "ok") {
        Fail "ssh_failed" "Validacao remota nao retornou ok para $RemoteProjectPath."
    }
}

function Copy-ReleaseToRemote([string]$ReleaseDir, [string]$RemoteTemp) {
    $files = @()
    foreach ($file in $script:RequiredFiles) {
        $files += (Join-Path $ReleaseDir $file)
    }
    $signaturePath = Join-Path $ReleaseDir "release-manifest.sig"
    if (Test-Path $signaturePath) {
        $files += $signaturePath
    }
    $target = "${RemoteAlias}:$RemoteTemp/"
    Invoke-Native "scp.exe" (@("-q") + $files + @($target)) "upload_failed" | Out-Null
}

function Assert-RemoteRelease([string]$RemoteDir, [string]$ExpectedSha, [long]$ExpectedSize) {
    $required = ($script:RequiredFiles | ForEach-Object { ConvertTo-BashSingleQuoted $_ }) -join " "
    $command = @"
set -euo pipefail
cd $(ConvertTo-BashSingleQuoted $RemoteDir)
for f in $required; do test -s "`$f"; done
python3 -m json.tool version.json >/dev/null
python3 -m json.tool checksums.json >/dev/null
python3 -m json.tool release-manifest.json >/dev/null
legacy=`$(python3 - <<'PY'
import json
print('1' if json.load(open('version.json', encoding='utf-8')).get('legacyUnsigned') else '0')
PY
)
channel=`$(python3 - <<'PY'
import json
print(json.load(open('version.json', encoding='utf-8')).get('channel',''))
PY
)
if [ "`$legacy" != "1" ] || [ "`$channel" != "development" ]; then test -s release-manifest.sig; fi
actual=`$(sha256sum NightOwl.Agent.Windows.zip | awk '{print `$1}')
test "x`$actual" = "x$ExpectedSha"
size=`$(stat -c%s NightOwl.Agent.Windows.zip)
test "x`$size" = "x$ExpectedSize"
"@
    Invoke-Ssh $command "checksum_failed" | Out-Null
}

function Publish-RemoteAtomic([string]$RemoteTemp, [string]$RemoteTarget, [bool]$AllowReplace) {
    $backup = "$RemoteTarget.backup-$([guid]::NewGuid().ToString("N"))"
    $allowReplaceValue = if ($AllowReplace) { "1" } else { "0" }
    $command = @"
set -euo pipefail
target=$(ConvertTo-BashSingleQuoted $RemoteTarget)
tmp=$(ConvertTo-BashSingleQuoted $RemoteTemp)
backup=$(ConvertTo-BashSingleQuoted $backup)
allow_replace="$allowReplaceValue"
if [ -e "`$target" ]; then
  old_sha=`$(sha256sum "`$target/NightOwl.Agent.Windows.zip" | awk '{print `$1}' 2>/dev/null || true)
  new_sha=`$(sha256sum "`$tmp/NightOwl.Agent.Windows.zip" | awk '{print `$1}' 2>/dev/null || true)
  old_manifest=`$(sha256sum "`$target/release-manifest.json" | awk '{print `$1}' 2>/dev/null || true)
  new_manifest=`$(sha256sum "`$tmp/release-manifest.json" | awk '{print `$1}' 2>/dev/null || true)
  old_signature=`$(sha256sum "`$target/release-manifest.sig" | awk '{print `$1}' 2>/dev/null || true)
  new_signature=`$(sha256sum "`$tmp/release-manifest.sig" | awk '{print `$1}' 2>/dev/null || true)
  if [ "x`$old_sha" = "x`$new_sha" ] && [ "x`$old_manifest" = "x`$new_manifest" ] && [ "x`$old_signature" = "x`$new_signature" ]; then
    rm -rf "`$tmp"
    echo "release_already_current"
    exit 0
  fi
  echo "RELEASE_IMMUTABILITY_VIOLATION"
  exit 19
fi
if mv "`$tmp" "`$target"; then
  find "`$target" -type d -exec chmod 755 {} \;
  find "`$target" -type f -exec chmod 644 {} \;
  chown -R www-data:www-data "`$target" 2>/dev/null || true
  rm -rf "`$backup"
else
  if [ -e "`$backup" ]; then mv "`$backup" "`$target"; fi
  exit 18
fi
"@
    Invoke-Ssh $command "upload_failed" | Out-Null
}

function Test-PublicUrls($LocalRelease) {
    $releaseBase = "{0}/releases/{1}" -f $PublicBaseUrl.TrimEnd("/"), $Version
    foreach ($url in @(
        "$releaseBase/release-manifest.json",
        "$releaseBase/NightOwl.Agent.Windows.zip"
    )) {
        Write-Step "Validando URL publica: $url"
        if ($DryRun) { continue }
        try {
            $response = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -TimeoutSec 30
            if ([int]$response.StatusCode -ne 200) {
                Fail "http_validation_failed" "URL nao retornou 200: $url ($($response.StatusCode))"
            }
        }
        catch {
            Fail "http_validation_failed" "Falha ao validar URL $url`: $($_.Exception.Message)"
        }
    }

    if (-not $DryRun) {
        $versionUrl = "$releaseBase/version.json"
        $publicVersion = Invoke-WebRequest -Uri $versionUrl -UseBasicParsing -TimeoutSec 30
        if ([int]$publicVersion.StatusCode -ne 200) {
            Fail "http_validation_failed" "version.json publico nao retornou 200."
        }
        $manifest = $publicVersion.Content | ConvertFrom-Json
        if ([string]$manifest.version -ne $Version) {
            Fail "http_validation_failed" "version.json publico declarou $($manifest.version), esperado $Version."
        }
        if ([string]$manifest.sha256 -ne [string]$LocalRelease.ZipSha) {
            Fail "http_validation_failed" "version.json publico com SHA256 divergente."
        }
        if ([string]$manifest.packageUrl -ne "$releaseBase/NightOwl.Agent.Windows.zip") {
            Fail "http_validation_failed" "version.json publico com packageUrl inesperada: $($manifest.packageUrl)"
        }
    }
}

function Import-ReleaseInDjango($LocalRelease) {
    $versionJsonUrl = "{0}/releases/{1}/version.json" -f $PublicBaseUrl.TrimEnd("/"), $Version
    $forceFlag = if ($Force) { " --force" } else { "" }
    $pyPaused = if ($Paused) { "True" } else { "False" }
    $signatureKeyId = [string]$LocalRelease.VersionJson.signature_key_id
    $command = @"
set -euo pipefail
cd $(ConvertTo-BashSingleQuoted $RemoteProjectPath)
source .venv/bin/activate
python manage.py import_agent_release --agent-version $(ConvertTo-BashSingleQuoted $Version) --channel $(ConvertTo-BashSingleQuoted $Channel) --release-status paused --version-json $(ConvertTo-BashSingleQuoted $versionJsonUrl)$forceFlag
python manage.py verify_agent_release --agent-version $(ConvertTo-BashSingleQuoted $Version)
python manage.py shell -c $(ConvertTo-BashSingleQuoted "from agents.models import AgentRelease, AgentReleaseSigningKey; r=AgentRelease.objects.get(version='$Version'); assert r.channel == '$Channel'; assert r.status == 'paused'; assert r.rollout_percentage == $Rollout; assert r.rollout_paused == $pyPaused; assert r.package_url == '$($LocalRelease.VersionJson.packageUrl)'; assert r.sha256 == '$($LocalRelease.ZipSha)'; assert r.signature_valid is True; assert r.legacy_unsigned is False; k=AgentReleaseSigningKey.objects.get(key_id='$signatureKeyId'); assert not k.revoked; print('release_import_verified')")
"@
    Invoke-Ssh $command "import_failed" | Out-Null
}

function Invoke-PruneOldReleases {
    $releaseRootRemote = "$RemoteProjectPath/downloads/agent/windows/releases"
    $keep = [Math]::Max(1, $KeepLastReleases)
    $dry = if ($DryRun) { "1" } else { "0" }
    $command = @"
set -euo pipefail
cd $(ConvertTo-BashSingleQuoted $RemoteProjectPath)
source .venv/bin/activate
python - <<'PY'
import json
from pathlib import Path
from agents.models import AgentMachine, AgentJob, AgentRelease
release_root = Path("$releaseRootRemote")
current = "$Version"
keep = int("$keep")
protected = {current}
protected.update(v for v in AgentMachine.objects.exclude(agent_version='').values_list('agent_version', flat=True))
protected.update(v for v in AgentMachine.objects.exclude(pinned_agent_version='').values_list('pinned_agent_version', flat=True))
protected.update(AgentRelease.objects.filter(channel='stable', status__in=['published', 'paused']).values_list('version', flat=True))
for job in AgentJob.objects.filter(status__in=['queued','pending','dispatched','running'], job_type='update_agent'):
    payload = job.payload or {}
    if payload.get('target_version'):
        protected.add(str(payload['target_version']))
    if job.agent_release_id:
        protected.add(job.agent_release.version)
dirs = [p.name for p in release_root.iterdir() if p.is_dir() and not p.name.startswith('.')]
known = {r.version: r for r in AgentRelease.objects.filter(version__in=dirs)}
ordered = sorted(dirs, key=lambda v: (known.get(v).released_at or known.get(v).created_at if known.get(v) else None, v), reverse=True)
keep_set = set(ordered[:keep])
delete = [v for v in ordered if v not in protected and v not in keep_set]
print(json.dumps({'protected': sorted(protected), 'kept': sorted(keep_set), 'delete': delete}))
PY
"@
    $planText = (Invoke-Ssh $command "import_failed" | Out-String).Trim()
    Write-Step "Plano de limpeza: $planText"
    if ($DryRun) {
        return
    }
    $plan = $planText | ConvertFrom-Json
    foreach ($versionToDelete in @($plan.delete)) {
        Assert-SafeRemoteSegment $versionToDelete
        Invoke-Ssh "rm -rf $(ConvertTo-BashSingleQuoted "$releaseRootRemote/$versionToDelete")" "upload_failed" | Out-Null
        Write-Step "Release antiga removida: $versionToDelete"
    }
}

try {
    if ($SelfTest) {
        Invoke-SelfTest
        exit 0
    }

    Initialize-PublisherConfiguration
    Assert-Version $Version
    Assert-SafeRemoteSegment $Version
    Assert-GitStatusClean
    if (-not (Test-Path $script:BuildScript)) {
        Fail "validation_failed" "Build script nao encontrado: $script:BuildScript"
    }
    if ($Rollout -ne 0 -or -not $Paused) {
        Fail "validation_failed" "O comando import_agent_release atual importa sempre pausado e rollout 0. Use Rollout=0 e Paused=true."
    }
    Assert-SigningMaterial

    Write-Step "Validando SSH sem senha para $RemoteAlias"
    Test-SshNoPassword
    Assert-RemoteServerConfiguration

    if ($ValidateOnly) {
        Write-Step "ValidateOnly ativo; validando artefatos locais e remoto sem build/upload/import."
    }
    elseif ($ResumeImport) {
        Write-Step "ResumeImport ativo; pulando build/upload e retomando importacao/verificacao no Django."
        $SkipBuild = $true
        $SkipUpload = $true
    }

    if (-not $SkipBuild -and -not $ValidateOnly) {
        $buildArguments = New-BuildReleaseArguments -RequestedVersion $Version -RequestedChannel $Channel -RequestedPublicBaseUrl $PublicBaseUrl -AllowForce ([bool]$Force)
        Assert-ArrayDoesNotContainFalseSwitch $buildArguments
        Invoke-Native "powershell.exe" $buildArguments "build_failed" | Out-Null
    }
    else {
        Write-Step "SkipBuild ativo; usando artefatos locais existentes."
    }

    $releaseDir = Join-Path $script:ReleaseRoot $Version
    $localRelease = Assert-LocalRelease $releaseDir
    if ($ValidateOnly) {
        Write-Step "ValidateOnly OK: release local validada com assinatura, hashes e bundle publico."
        exit 0
    }
    $releaseRootRemote = "$RemoteProjectPath/downloads/agent/windows/releases"
    $uploadId = [guid]::NewGuid().ToString("N")
    $remoteTemp = "$releaseRootRemote/.upload-$Version-$uploadId"
    $remoteTarget = "$releaseRootRemote/$Version"

    if (-not $SkipUpload) {
        Invoke-Ssh "mkdir -p $(ConvertTo-BashSingleQuoted $releaseRootRemote); rm -rf $(ConvertTo-BashSingleQuoted $remoteTemp); mkdir -p $(ConvertTo-BashSingleQuoted $remoteTemp)" "upload_failed" | Out-Null
        try {
            Copy-ReleaseToRemote -ReleaseDir $releaseDir -RemoteTemp $remoteTemp
            Assert-RemoteRelease -RemoteDir $remoteTemp -ExpectedSha $localRelease.ZipSha -ExpectedSize $localRelease.ZipSize
            Publish-RemoteAtomic -RemoteTemp $remoteTemp -RemoteTarget $remoteTarget -AllowReplace ([bool]$Force)
            Assert-RemoteRelease -RemoteDir $remoteTarget -ExpectedSha $localRelease.ZipSha -ExpectedSize $localRelease.ZipSize
        }
        catch {
            try { Invoke-Ssh "rm -rf $(ConvertTo-BashSingleQuoted $remoteTemp)" "upload_failed" | Out-Null } catch {}
            throw
        }
    }
    else {
        Write-Step "SkipUpload ativo; pulando envio e publicacao remota."
    }

    if (-not $SkipUpload) {
        Test-PublicUrls $localRelease
    }

    if (-not $SkipImport) {
        Import-ReleaseInDjango $localRelease
    }
    else {
        Write-Step "SkipImport ativo; release nao importada no Django."
    }

    if ($PruneOldReleases) {
        Invoke-PruneOldReleases
    }

    if (-not $KeepLocalWork) {
        Write-Step "Artefatos locais preservados em $releaseDir"
    }

    Write-Host ""
    Write-Host "Release publicada com sucesso:"
    Write-Host "  Versao:      $Version"
    Write-Host "  Canal:       $Channel"
    Write-Host "  Rollout:     0%"
    Write-Host "  Pausada:     true"
    Write-Host "  Key ID:      $([string]$localRelease.VersionJson.signature_key_id)"
    Write-Host "  SHA256 ZIP:  $($localRelease.ZipSha)"
    Write-Host "  Tamanho:     $($localRelease.ZipSize) bytes"
    Write-Host "  Manifesto:   $($PublicBaseUrl.TrimEnd('/'))/releases/$Version/release-manifest.json"
    Write-Host "  Assinatura:  $($PublicBaseUrl.TrimEnd('/'))/releases/$Version/release-manifest.sig"
    Write-Host "  URL:         $($PublicBaseUrl.TrimEnd('/'))/releases/$Version/version.json"
    Write-Host ""
    Write-Host "Proximo passo: abra o painel de Releases, revise a release pausada e libere manualmente o rollout ou selecione-a no modal do endpoint."
    exit 0
}
catch {
    $exit = 1
    $code = "unexpected"
    if ($_.Exception.Data.Contains("ExitCode")) {
        $exit = [int]$_.Exception.Data["ExitCode"]
    }
    if ($_.Exception.Data.Contains("Code")) {
        $code = [string]$_.Exception.Data["Code"]
    }
    Write-Error ("Falha [{0}]: {1}" -f $code, $_.Exception.Message)
    exit $exit
}
