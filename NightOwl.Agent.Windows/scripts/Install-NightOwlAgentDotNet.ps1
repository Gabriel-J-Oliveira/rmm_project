param(
    [string]$ServerUrl = "https://nightowl.controlsul.com.br",

    [string]$EnrollmentToken = "",
    [string]$ManualValidationToken = "",
    [string]$AgentToken = "",
    [string]$PackageUrl = "",
    [string]$TrustedPublicKeysPath = "",
    [string]$InstallPath = "",
    [string]$ServiceName = "NightOwlAgentDotNet",
    [string]$DisplayName = "NightOwl RMM Agent",
    [switch]$InstallAsService,
    [switch]$Force,
    [bool]$StartService = $true,
    [switch]$RunCheck,
    [bool]$KeepPowerShellAgent = $true,
    [switch]$DisablePowerShellAgent,
    [switch]$AllowInsecureTls,
    [switch]$NoGui,
    [switch]$NoTray,
    [switch]$StartTray,
    [switch]$DebugLog,
    [switch]$Install,
    [switch]$Repair,
    [switch]$Reinstall,
    [switch]$ForceRecovery,
    [switch]$TrustLocalPackage,
    [switch]$AllowReleaseBundledTrustForLab,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

function New-NightOwlPaths([string]$RequestedInstallPath) {
    $root = if ([string]::IsNullOrWhiteSpace($env:NIGHTOWL_HOME)) { "C:\ProgramData\NightOwl" } else { $env:NIGHTOWL_HOME }
    $install = if ([string]::IsNullOrWhiteSpace($RequestedInstallPath)) { Join-Path $root "AgentDotNet" } else { $RequestedInstallPath }
    $configDir = Join-Path $root "Config"
    $stateDir = Join-Path $root "State"
    $logsDir = Join-Path $root "Logs"
    $updatesDir = Join-Path $root "Updates"
    $trustDir = Join-Path $root "Trust"
    return [ordered]@{
        Root = $root
        Install = $install
        ConfigDir = $configDir
        ConfigPath = Join-Path $configDir "agent.config.json"
        LegacyConfigPath = Join-Path $install "agent.config.json"
        IdentityDir = Join-Path $root "Identity"
        IdentityPath = Join-Path (Join-Path $root "Identity") "agent.identity.json"
        StateDir = $stateDir
        StatePath = Join-Path $stateDir "agent.state.json"
        LegacyStatePath = Join-Path $install "agent-dotnet.state.json"
        PendingResultsPath = Join-Path $stateDir "pending-results"
        Trust = $trustDir
        TrustBundlePath = Join-Path $trustDir "release-public-keys.json"
        TrustBackups = Join-Path $trustDir "Backups"
        TrustDownloads = Join-Path $trustDir "Downloads"
        Logs = $logsDir
        AgentLog = Join-Path $logsDir "agent-dotnet.jsonl"
        InstallLog = Join-Path $logsDir "service-install.log"
        Packages = Join-Path $root "Packages"
        Cache = Join-Path $root "Cache"
        Updates = $updatesDir
        UpdatesDownloads = Join-Path $updatesDir "Downloads"
        UpdatesStaging = Join-Path $updatesDir "Staging"
        UpdatesBackup = Join-Path $updatesDir "Backup"
        UpdatesPending = Join-Path $updatesDir "Pending"
        UpdatesRunner = Join-Path $updatesDir "Runner"
        Diagnostics = Join-Path $root "Diagnostics"
    }
}

$script:NightOwlPaths = New-NightOwlPaths -RequestedInstallPath $InstallPath
$InstallPath = [string]$script:NightOwlPaths.Install
$script:UpdateMutex = $null
$script:UpdateMutexAcquired = $false
$script:Report = $null
$script:Operation = "install"
$script:LifecycleErrorCodes = @(
    "INSTALL_ADMIN_REQUIRED",
    "INSTALL_UPDATE_IN_PROGRESS",
    "INSTALL_DOWNLOAD_FAILED",
    "INSTALL_HASH_MISMATCH",
    "INSTALL_PACKAGE_INVALID",
    "INSTALL_SERVICE_CREATE_FAILED",
    "INSTALL_ENROLLMENT_FAILED",
    "INSTALL_HEALTHCHECK_FAILED",
    "REPAIR_UPDATE_IN_PROGRESS",
    "REPAIR_CONFIG_INVALID",
    "REPAIR_IDENTITY_INVALID",
    "REPAIR_IDENTITY_CONFLICT",
    "REPAIR_BINARY_MISSING",
    "REPAIR_BINARY_HASH_MISMATCH",
    "REPAIR_SERVICE_INVALID",
    "REPAIR_ACL_FAILED",
    "REPAIR_HEALTHCHECK_FAILED",
    "REPAIR_FORCE_RECOVERY_REQUIRED",
    "REINSTALL_UPDATE_IN_PROGRESS",
    "REINSTALL_IDENTITY_PRESERVED",
    "REINSTALL_REENROLLMENT_REQUIRED"
)

function Get-OperationName {
    $selected = @()
    if ($Install) { $selected += "install" }
    if ($Repair) { $selected += "repair" }
    if ($Reinstall) { $selected += "reinstall" }
    if ($selected.Count -gt 1) {
        throw "Use apenas um modo por execucao: -Install, -Repair ou -Reinstall."
    }
    if ($selected.Count -eq 0) { return "install" }
    return $selected[0]
}

function Get-OperationErrorCode([string]$Suffix) {
    switch ($script:Operation) {
        "repair" { return "REPAIR_$Suffix" }
        "reinstall" { return "REINSTALL_$Suffix" }
        default { return "INSTALL_$Suffix" }
    }
}

function New-OperationReport([string]$Operation) {
    return [ordered]@{
        operation = $Operation
        started_at = (Get-Date).ToUniversalTime().ToString("o")
        completed_at = $null
        status = "running"
        installed_version = ""
        previous_version = ""
        machine_id = ""
        identity_preserved = $false
        enrollment_performed = $false
        service_status = ""
        actions = New-Object System.Collections.ArrayList
        warnings = New-Object System.Collections.ArrayList
        error_code = ""
        error_message = ""
    }
}

function Add-ReportAction([string]$Action, $Metadata = @{}) {
    if ($null -eq $script:Report) { return }
    [void]$script:Report.actions.Add([ordered]@{
        timestamp = (Get-Date).ToUniversalTime().ToString("o")
        action = $Action
        metadata = $Metadata
    })
}

function Add-ReportWarning([string]$Code, [string]$Message, $Metadata = @{}) {
    if ($null -eq $script:Report) { return }
    [void]$script:Report.warnings.Add([ordered]@{
        timestamp = (Get-Date).ToUniversalTime().ToString("o")
        code = $Code
        message = $Message
        metadata = $Metadata
    })
}

function Protect-SecretValue([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    if ($Value.Length -le 8) { return "***" }
    return ("{0}...{1}" -f $Value.Substring(0, 4), $Value.Substring($Value.Length - 4))
}

function Write-OperationReport([string]$Status, [string]$ErrorCode = "", [string]$ErrorMessage = "") {
    if ($null -eq $script:Report) { return }
    try {
        $script:Report.completed_at = (Get-Date).ToUniversalTime().ToString("o")
        $script:Report.status = $Status
        $script:Report.error_code = $ErrorCode
        $script:Report.error_message = $ErrorMessage
        $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($service) {
            $script:Report.service_status = [string]$service.Status
        }
        $diagDir = [string]$script:NightOwlPaths.Diagnostics
        New-Item -ItemType Directory -Force -Path $diagDir | Out-Null
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
        $path = Join-Path $diagDir ("{0}-report-{1}.json" -f $script:Operation, $stamp)
        $script:Report | ConvertTo-Json -Depth 8 | Set-Content -Path $path -Encoding UTF8
        Write-InstallLog "operation.report.written" "Relatorio da operacao gravado." @{ operation = $script:Operation; path = $path; status = $Status; error_code = $ErrorCode }
        if ($NonInteractive) {
            $script:Report | ConvertTo-Json -Depth 8
        }
    }
    catch {
        Write-InstallLog "operation.report.failed" "Falha ao gravar relatorio." @{ operation = $script:Operation; error = $_.Exception.Message }
    }
}

function Write-Step($Status, $Message) {
    Write-Host ("[{0}] {1}" -f $Status, $Message)
}

function Write-InstallLog($EventType, $Message, $Metadata = @{}) {
    try {
        $logDir = [string]$script:NightOwlPaths.Logs
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $entry = [ordered]@{
            timestamp = (Get-Date).ToUniversalTime().ToString("o")
            event_type = $EventType
            message = $Message
            metadata = $Metadata
        }
        $entry | ConvertTo-Json -Depth 6 -Compress | Add-Content -Path ([string]$script:NightOwlPaths.InstallLog) -Encoding UTF8
    }
    catch {}
}

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "INSTALL_ADMIN_REQUIRED: Execute este instalador em um PowerShell como Administrador."
    }
}

function Acquire-UpdaterLockOrThrow {
    try {
        try {
            $script:UpdateMutex = New-Object System.Threading.Mutex($false, "Global\NightOwl.Agent.Update")
        }
        catch {
            $script:UpdateMutex = New-Object System.Threading.Mutex($false, "NightOwl.Agent.Update")
        }
        $script:UpdateMutexAcquired = $script:UpdateMutex.WaitOne([TimeSpan]::Zero)
        if (-not $script:UpdateMutexAcquired) {
            throw (Get-OperationErrorCode "UPDATE_IN_PROGRESS")
        }
        Add-ReportAction "updater.lock.acquired"
        Write-InstallLog "operation.update_lock.acquired" "Lock global do updater adquirido." @{ operation = $script:Operation }
    }
    catch {
        if ($_.Exception.Message -like "*UPDATE_IN_PROGRESS*") { throw }
        throw ("{0}: nao foi possivel adquirir lock global do updater. {1}" -f (Get-OperationErrorCode "UPDATE_IN_PROGRESS"), $_.Exception.Message)
    }
}

function Release-UpdaterLock {
    if ($script:UpdateMutexAcquired -and $script:UpdateMutex) {
        try { $script:UpdateMutex.ReleaseMutex() | Out-Null } catch {}
        $script:UpdateMutexAcquired = $false
    }
    if ($script:UpdateMutex) {
        try { $script:UpdateMutex.Dispose() } catch {}
        $script:UpdateMutex = $null
    }
}

function Get-UpdateStateInfo {
    $path = Join-Path ([string]$script:NightOwlPaths.StateDir) "update-state.json"
    if (-not (Test-Path $path)) { return $null }
    $state = Read-JsonFile $path
    if ($null -eq $state) {
        Add-ReportWarning "UPDATE_STATE_INVALID" "update-state.json existe, mas nao foi possivel ler JSON." @{ path = $path }
        Write-InstallLog "operation.update_state.invalid" "update-state.json invalido preservado." @{ operation = $script:Operation; path = $path }
        return [pscustomobject]@{ status = "invalid"; current_stage = "invalid"; update_id = ""; job_id = "" }
    }
    return $state
}

function Assert-NoActiveUpdate {
    $runningUpdater = Get-Process -Name "NightOwl.Agent.Updater" -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $PID }
    if ($runningUpdater) {
        $code = Get-OperationErrorCode "UPDATE_IN_PROGRESS"
        Write-InstallLog "operation.blocked.update_process" "Updater em execucao; operacao bloqueada." @{ operation = $script:Operation; process_count = @($runningUpdater).Count; error_code = $code }
        throw "${code}: NightOwl.Agent.Updater.exe esta em execucao."
    }

    $state = Get-UpdateStateInfo
    if ($null -eq $state) { return }

    $stage = [string]$state.current_stage
    $status = [string]$state.status
    $activeStages = @(
        "downloading", "validating", "staging", "creating_backup", "stopping_service",
        "replacing_files", "starting_service", "waiting_health_check",
        "rollback_required", "rollback_starting", "rollback_restoring_files",
        "rollback_stopping_service", "rollback_starting_service", "rollback_waiting_health_check"
    )
    if (($status -ieq "running") -or ($activeStages -contains $stage)) {
        $code = Get-OperationErrorCode "UPDATE_IN_PROGRESS"
        Write-InstallLog "operation.blocked.update_active" "Update ou rollback ativo; operacao bloqueada." @{ operation = $script:Operation; update_id = $state.update_id; job_id = $state.job_id; stage = $stage; status = $status; error_code = $code }
        throw "${code}: update/rollback ativo ($stage). Nao modificando binarios."
    }

    $rollbackRequired = $false
    try { $rollbackRequired = [bool]$state.rollback_required } catch {}
    if (($stage -ieq "rollback_failed" -or $rollbackRequired) -and $script:Operation -in @("repair", "reinstall") -and -not $ForceRecovery) {
        Write-InstallLog "operation.blocked.force_recovery_required" "Estado de rollback exige ForceRecovery." @{ operation = $script:Operation; update_id = $state.update_id; job_id = $state.job_id; stage = $stage; rollback_required = $rollbackRequired }
        throw "REPAIR_FORCE_RECOVERY_REQUIRED: estado de rollback detectado. Use -ForceRecovery -Force somente em recuperacao manual."
    }

    Add-ReportAction "update-state.preserved" @{ stage = $stage; status = $status; update_id = [string]$state.update_id }
}

function Assert-ForceRecoveryAllowed {
    if (-not $ForceRecovery) { return }
    if (-not $Force -and -not $NonInteractive) {
        $answer = Read-Host "ForceRecovery preserva evidencias do updater e corrige binarios sem alterar update-state. Digite NIGHTOWL para continuar"
        if ($answer -ne "NIGHTOWL") {
            throw "REPAIR_FORCE_RECOVERY_REQUIRED: confirmacao de ForceRecovery cancelada."
        }
    }
    elseif ($NonInteractive -and -not $Force) {
        throw "REPAIR_FORCE_RECOVERY_REQUIRED: use -Force junto com -NonInteractive para ForceRecovery."
    }
    Add-ReportWarning "FORCE_RECOVERY" "ForceRecovery habilitado para recuperacao manual." @{ operation = $script:Operation }
    Write-InstallLog "operation.force_recovery.enabled" "ForceRecovery habilitado." @{ operation = $script:Operation }
}

function Set-NightOwlSecureAcl([string[]]$Paths, [switch]$AllowUsersRead) {
    foreach ($path in $Paths) {
        try {
            if (-not (Test-Path $path)) { continue }
            & icacls.exe $path /inheritance:r | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "icacls /inheritance:r retornou $LASTEXITCODE" }
            & icacls.exe $path /remove:g "*S-1-1-0" "*S-1-5-11" "*S-1-5-32-545" | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "icacls /remove:g retornou $LASTEXITCODE" }
            if ($AllowUsersRead) {
                & icacls.exe $path /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "*S-1-5-32-545:(OI)(CI)(RX)" | Out-Null
            }
            else {
                & icacls.exe $path /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" | Out-Null
            }
            if ($LASTEXITCODE -ne 0) { throw "icacls /grant:r retornou $LASTEXITCODE" }
            Write-InstallLog "path.acl.applied" "ACL segura aplicada." @{ path = $path }
        }
        catch {
            Write-InstallLog "path.acl.failed" "Falha ao aplicar ACL." @{ path = $path; error = $_.Exception.Message }
        }
    }
}

function Normalize-ServerUrl([string]$Url) {
    $value = ($Url.Trim()).TrimEnd("/")
    if ($value.EndsWith("/api/agent/heartbeat")) {
        return $value.Substring(0, $value.Length - "/api/agent/heartbeat".Length).TrimEnd("/")
    }
    return $value
}

function Join-AgentUrl([string]$Base, [string]$Path) {
    return ("{0}/{1}" -f $Base.TrimEnd("/"), $Path.TrimStart("/"))
}

function Get-PackageUrl([string]$Base, [string]$ExplicitPackageUrl) {
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPackageUrl)) {
        return $ExplicitPackageUrl.Trim()
    }
    return Join-AgentUrl $Base "/downloads/nightowl-agent/NightOwl.Agent.Windows.zip"
}

function Get-UrlDirectory([string]$Url) {
    $idx = $Url.LastIndexOf("/")
    if ($idx -lt 0) { return $Url }
    return $Url.Substring(0, $idx)
}

function Enable-InsecureTlsForLab {
    if (-not $AllowInsecureTls) { return }
    Write-Step "WARN" "AllowInsecureTls ativo. Use apenas em laboratorio; producao deve usar certificado publico confiavel."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function Get-ChecksumFromManifest($Manifest, [string]$FileName) {
    if ($null -eq $Manifest) { return "" }
    if ($Manifest.PSObject.Properties.Name -contains $FileName) {
        return [string]$Manifest.$FileName
    }
    if ($Manifest.PSObject.Properties.Name -contains "files") {
        foreach ($item in @($Manifest.files)) {
            if ($item.name -eq $FileName -or $item.file -eq $FileName) {
                return [string]($item.sha256)
            }
        }
    }
    return ""
}

function Download-AgentPackage([string]$Url, [string]$WorkDir) {
    Enable-InsecureTlsForLab
    if (-not $AllowInsecureTls -and -not $Url.StartsWith("https://", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "INSTALL_PACKAGE_INSECURE_URL: download de pacote exige HTTPS."
    }
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
    $zipPath = Join-Path $WorkDir "NightOwl.Agent.Windows.zip"
    Write-Step "OK" ("Baixando pacote: {0}" -f $Url)
    Invoke-WebRequest -Uri $Url -OutFile $zipPath -UseBasicParsing -TimeoutSec 120
    if (-not (Test-Path $zipPath) -or (Get-Item $zipPath).Length -le 0) {
        throw "Download do pacote falhou ou retornou arquivo vazio."
    }

    $baseUrl = Get-UrlDirectory $Url
    $checksumsPath = Join-Path $WorkDir "checksums.json"
    $manifestPath = Join-Path $WorkDir "release-manifest.json"
    $signaturePath = Join-Path $WorkDir "release-manifest.sig"
    $releaseBundledTrustPath = Join-Path $WorkDir "release-public-keys.downloaded.json"
    Invoke-WebRequest -Uri ($baseUrl + "/checksums.json") -OutFile $checksumsPath -UseBasicParsing -TimeoutSec 30
    Invoke-WebRequest -Uri ($baseUrl + "/release-manifest.json") -OutFile $manifestPath -UseBasicParsing -TimeoutSec 30
    Invoke-WebRequest -Uri ($baseUrl + "/release-manifest.sig") -OutFile $signaturePath -UseBasicParsing -TimeoutSec 30
    if ($AllowReleaseBundledTrustForLab) {
        Invoke-WebRequest -Uri ($baseUrl + "/release-public-keys.json") -OutFile $releaseBundledTrustPath -UseBasicParsing -TimeoutSec 30
        Write-Step "WARN" "LAB INSEGURO: usando release-public-keys.json baixado da propria release."
    }

    $checksums = Read-JsonFile $checksumsPath
    $expected = Get-ChecksumFromManifest $checksums "NightOwl.Agent.Windows.zip"
    if ([string]::IsNullOrWhiteSpace($expected)) {
        throw "INSTALL_CHECKSUM_MISSING: checksums.json sem SHA256 do ZIP."
    }
    $actual = Get-FileSha256 $zipPath
    if ($actual -ne $expected.ToLowerInvariant()) {
        throw "INSTALL_CHECKSUM_INVALID: Checksum invalido para NightOwl.Agent.Windows.zip. Esperado $expected, obtido $actual."
    }
    Write-Step "OK" "Checksum do pacote validado"

    $extractPath = Join-Path $WorkDir "extracted"
    if (Test-Path $extractPath) { Remove-Item -Path $extractPath -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
    Assert-RequiredPackageFiles -SourceDir $extractPath -RequireTrust
    $trustedKeysPath = Resolve-TrustedPublicKeysForInstaller -ReleaseBundledTrustPath $releaseBundledTrustPath
    Assert-DownloadedPackageTrust -ExtractPath $extractPath -ZipPath $zipPath -ManifestPath $manifestPath -SignaturePath $signaturePath -TrustedKeysPath $trustedKeysPath
    $exe = Get-ChildItem -Path $extractPath -Filter "NightOwl.Agent.Windows.exe" -Recurse | Select-Object -First 1
    if (-not $exe) {
        throw "Pacote extraido sem NightOwl.Agent.Windows.exe."
    }
    return $exe.DirectoryName
}

function Resolve-TrustedPublicKeysForInstaller([string]$ReleaseBundledTrustPath) {
    if (-not [string]::IsNullOrWhiteSpace($TrustedPublicKeysPath)) {
        if (-not (Test-Path $TrustedPublicKeysPath)) {
            throw "INSTALL_TRUST_BOOTSTRAP_MISSING: TrustedPublicKeysPath nao encontrado."
        }
        Write-Step "OK" "Usando bundle de chaves publicas provisionado explicitamente."
        return $TrustedPublicKeysPath
    }

    $localTrust = [string]$script:NightOwlPaths.TrustBundlePath
    if (Test-Path $localTrust) {
        Write-Step "OK" "Usando bundle de chaves publicas ja instalado localmente."
        return $localTrust
    }

    if ($AllowReleaseBundledTrustForLab) {
        if (-not (Test-Path $ReleaseBundledTrustPath)) {
            throw "INSTALL_TRUST_BOOTSTRAP_MISSING: bundle da release nao foi baixado para modo lab."
        }
        Write-Step "WARN" "LAB INSEGURO: release-public-keys.json da propria release foi aceito por flag explicita."
        return $ReleaseBundledTrustPath
    }

    throw "INSTALL_TRUST_BOOTSTRAP_REQUIRED: instalacao por download exige trust local valido em C:\ProgramData\NightOwl\Trust ou -TrustedPublicKeysPath provisionado por canal confiavel."
}

function Assert-RsaPublicXmlHasNoPrivateParameters([string]$Xml, [string]$KeyId) {
    foreach ($privateElement in @("P", "Q", "DP", "DQ", "InverseQ", "D")) {
        if ($Xml -match ("<{0}>" -f [regex]::Escape($privateElement))) {
            throw "INSTALL_SIGNING_KEY_INVALID: chave publica $KeyId contem parametro privado $privateElement."
        }
    }
}

function New-RsaPssPublicKeyFromXmlText([string]$Xml, [string]$KeyId) {
    Assert-RsaPublicXmlHasNoPrivateParameters -Xml $Xml -KeyId $KeyId
    $legacyProvider = $null
    try {
        $legacyProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider
        $legacyProvider.PersistKeyInCsp = $false
        $legacyProvider.FromXmlString($Xml)
        $parameters = $legacyProvider.ExportParameters($false)
    }
    finally {
        if ($null -ne $legacyProvider) {
            $legacyProvider.PersistKeyInCsp = $false
            $legacyProvider.Clear()
            $legacyProvider.Dispose()
        }
    }
    $rsa = New-Object System.Security.Cryptography.RSACng
    $rsa.ImportParameters($parameters)
    return $rsa
}

function Get-TrustedReleaseKey([string]$TrustedKeysPath, [string]$KeyId) {
    if (-not (Test-Path $TrustedKeysPath)) {
        throw "INSTALL_TRUSTED_KEYS_MISSING: release-public-keys.json confiavel ausente."
    }
    $trusted = Read-JsonFile $TrustedKeysPath
    foreach ($key in @($trusted.keys)) {
        if ([string]$key.key_id -eq $KeyId) {
            if ([string]$key.algorithm -ne "RSA-PSS-SHA256") { throw "INSTALL_SIGNING_KEY_INVALID: algoritmo invalido para $KeyId." }
            if ([string]$key.status -ne "active") { throw "INSTALL_SIGNING_KEY_REVOKED: chave $KeyId nao esta ativa." }
            return [string]$key.public_key_xml
        }
    }
    throw "INSTALL_SIGNING_KEY_UNKNOWN: chave $KeyId nao encontrada no bundle confiavel."
}

function Assert-DownloadedPackageTrust([string]$ExtractPath, [string]$ZipPath, [string]$ManifestPath, [string]$SignaturePath, [string]$TrustedKeysPath) {
    if (-not (Test-Path $ManifestPath)) { throw "INSTALL_MANIFEST_MISSING: release-manifest.json ausente." }
    if (-not (Test-Path $SignaturePath)) { throw "INSTALL_SIGNATURE_MISSING: release-manifest.sig ausente." }
    $manifest = Read-JsonFile $ManifestPath
    $keyId = [string]$manifest.key_id
    if ([string]::IsNullOrWhiteSpace($keyId)) { throw "INSTALL_SIGNATURE_KEY_MISSING: manifest sem key_id." }
    $package = $manifest.package
    if ($null -eq $package) { throw "INSTALL_MANIFEST_INVALID: manifest sem package." }
    $zipSha = Get-FileSha256 $ZipPath
    if ([string]$package.sha256 -ne $zipSha) { throw "INSTALL_MANIFEST_PACKAGE_HASH_INVALID: SHA do pacote diverge do manifesto." }
    if ([long]$package.size -ne (Get-Item $ZipPath).Length) { throw "INSTALL_MANIFEST_PACKAGE_SIZE_INVALID: tamanho do pacote diverge do manifesto." }
    foreach ($entry in @($manifest.required_zip_entries)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$entry) -and -not (Test-Path (Join-Path $ExtractPath ([string]$entry)))) {
            throw "INSTALL_PACKAGE_INVALID: pacote sem arquivo obrigatorio $entry"
        }
    }
    $publicXml = Get-TrustedReleaseKey -TrustedKeysPath $TrustedKeysPath -KeyId $keyId
    $rsa = New-RsaPssPublicKeyFromXmlText -Xml $publicXml -KeyId $keyId
    try {
        $manifestBytes = [System.IO.File]::ReadAllBytes($ManifestPath)
        $signatureBytes = [Convert]::FromBase64String((Get-Content -Raw -Path $SignaturePath).Trim())
        $valid = $rsa.VerifyData(
            $manifestBytes,
            $signatureBytes,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pss
        )
        if (-not $valid) { throw "INSTALL_SIGNATURE_INVALID: assinatura RSA-PSS invalida." }
    }
    finally {
        $rsa.Dispose()
    }
    Write-Step "OK" "Manifesto e assinatura do pacote validados"
}

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    try {
        return Get-Content -Path $Path -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Test-JsonFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $true }
    try {
        Get-Content -Path $Path -Raw | ConvertFrom-Json | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Preserve-File([string]$Path, [string]$Reason) {
    if (-not (Test-Path $Path)) { return "" }
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
    $destination = "$Path.preserved-$stamp"
    Copy-Item -Path $Path -Destination $destination -Force
    Write-InstallLog "file.preserved" "Arquivo preservado antes de correcao." @{ path = $Path; preserved = $destination; reason = $Reason }
    Add-ReportWarning "FILE_PRESERVED" "Arquivo preservado antes de correcao." @{ path = $Path; preserved = $destination; reason = $Reason }
    return $destination
}

function Repair-JsonFileIfInvalid([string]$Path, [string]$ErrorCode) {
    if ((Test-Path $Path) -and -not (Test-JsonFile $Path)) {
        Preserve-File -Path $Path -Reason $ErrorCode | Out-Null
        Rename-Item -Path $Path -NewName ([System.IO.Path]::GetFileName($Path) + ".invalid") -Force
        Write-InstallLog "json.invalid.preserved" "JSON invalido preservado e removido do caminho ativo." @{ path = $Path; error_code = $ErrorCode }
        Add-ReportWarning $ErrorCode "JSON invalido preservado; sera recriado se houver fonte confiavel." @{ path = $Path }
    }
}

function Get-JsonProperty($Object, [string[]]$Names) {
    if ($null -eq $Object) { return "" }
    foreach ($name in $Names) {
        if ($Object.PSObject.Properties.Name -contains $name) {
            $value = $Object.$name
            if ($null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)) {
                return [string]$value
            }
        }
    }
    return ""
}

function Test-MachineId([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    if ($Value -ieq $env:COMPUTERNAME) { return $false }
    if ($Value -ieq "HOSTNAME" -or $Value -ieq "MACHINE_ID") { return $false }
    return $true
}

function Resolve-MachineId([string]$ConfigPath, [string]$StatePath) {
    $existingConfig = Read-JsonFile $ConfigPath
    $configMachineId = Get-JsonProperty $existingConfig @("machineId", "machine_id", "MachineId")
    if (Test-MachineId $configMachineId) {
        return @{ Value = $configMachineId; Source = "config" }
    }

    $candidates = @(
        @{ Path = $StatePath; Source = "dotnet_state" },
        @{ Path = (Join-Path ([string]$script:NightOwlPaths.Root) "Agent\agent.state.json"); Source = "powershell_state" },
        @{ Path = "C:\RMM\agent.state.json"; Source = "legacy_rmm_state" }
    )
    foreach ($candidate in $candidates) {
        $state = Read-JsonFile $candidate.Path
        $stateMachineId = Get-JsonProperty $state @("machine_id", "machineId", "MachineId", "agent_id", "agentId")
        if (Test-MachineId $stateMachineId) {
            return @{ Value = $stateMachineId; Source = $candidate.Source }
        }
    }

    try {
        $machineGuid = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Cryptography" -Name MachineGuid -ErrorAction Stop).MachineGuid
        if (Test-MachineId $machineGuid) {
            return @{ Value = $machineGuid; Source = "machine_guid" }
        }
    }
    catch {}

    return @{ Value = ([guid]::NewGuid().ToString()); Source = "generated" }
}

function Get-ComputerInfoLite {
    $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    $bios = Get-CimInstance Win32_BIOS -ErrorAction SilentlyContinue
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    return @{
        Hostname = $env:COMPUTERNAME.ToUpperInvariant()
        Domain = if ($cs.PartOfDomain) { ([string]$cs.Domain).ToLowerInvariant() } else { "" }
        SerialNumber = if ($bios.SerialNumber) { [string]$bios.SerialNumber } else { "" }
        OsName = if ($os.Caption) { [string]$os.Caption } else { "" }
    }
}

function Get-WebErrorPayload($ErrorRecord) {
    $responseText = ""
    try {
        $response = $ErrorRecord.Exception.Response
        if ($response) {
            $stream = $response.GetResponseStream()
            if ($stream) {
                $reader = New-Object System.IO.StreamReader($stream)
                $responseText = $reader.ReadToEnd()
                $reader.Dispose()
            }
        }
    }
    catch {}
    if ([string]::IsNullOrWhiteSpace($responseText) -and $ErrorRecord.ErrorDetails -and $ErrorRecord.ErrorDetails.Message) {
        $responseText = [string]$ErrorRecord.ErrorDetails.Message
    }
    if ([string]::IsNullOrWhiteSpace($responseText)) {
        return [pscustomobject]@{
            error = "request_failed"
            detail = $ErrorRecord.Exception.Message
            reason = ""
            raw = ""
        }
    }
    try {
        $parsed = $responseText | ConvertFrom-Json
        if ($parsed) {
            if ($parsed.PSObject.Properties.Name -notcontains "raw") {
                $parsed | Add-Member -NotePropertyName "raw" -NotePropertyValue $responseText
            }
            return $parsed
        }
    }
    catch {}
    return [pscustomobject]@{
        error = "request_failed"
        detail = $responseText
        reason = ""
        raw = $responseText
    }
}

function Invoke-EnrollmentRequest($BaseUrl, $EnrollmentTokenValue, $ManualTokenValue, $MachineId, $InstallPath) {
    $info = Get-ComputerInfoLite
    $body = @{
        machine_id = $MachineId
        hostname = $info.Hostname
        domain = $info.Domain
        serial_number = $info.SerialNumber
        fqdn = if ($info.Domain) { "$($info.Hostname).$($info.Domain)" } else { $info.Hostname }
        os_name = $info.OsName
        agent_version = "0.1.0.7"
        agent_mode = "dotnet-service"
        install_path = $InstallPath
        task_name = "NightOwlAgentDotNet"
    }
    if (-not [string]::IsNullOrWhiteSpace($EnrollmentTokenValue)) {
        $body["enrollment_token"] = $EnrollmentTokenValue
    }
    if (-not [string]::IsNullOrWhiteSpace($ManualTokenValue)) {
        $body["manual_validation_token"] = $ManualTokenValue
    }
    $url = Join-AgentUrl $BaseUrl "/api/agent/enroll/"
    $json = $body | ConvertTo-Json -Depth 5
    try {
        return Invoke-RestMethod -Method Post -Uri $url -Body $json -ContentType "application/json" -TimeoutSec 30
    }
    catch {
        $payload = Get-WebErrorPayload $_
        $message = if ($payload.detail) { [string]$payload.detail } else { $_.Exception.Message }
        $exception = New-Object System.Exception($message, $_.Exception)
        $exception.Data["nightowl_error"] = if ($payload.error) { [string]$payload.error } else { "request_failed" }
        $exception.Data["nightowl_reason"] = if ($payload.reason) { [string]$payload.reason } else { "" }
        $exception.Data["nightowl_detail"] = $message
        $exception.Data["nightowl_raw"] = if ($payload.raw) { [string]$payload.raw } else { "" }
        throw $exception
    }
}

function Show-ManualValidationDialog($ServerBase, $Hostname, $Domain) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "NightOwl Agent - Validacao manual"
    $form.Width = 560
    $form.Height = 320
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.BackColor = [System.Drawing.Color]::FromArgb(11, 15, 24)
    $form.ForeColor = [System.Drawing.Color]::White

    $title = New-Object System.Windows.Forms.Label
    $title.Text = "NightOwl Agent - Validacao manual"
    $title.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
    $title.Left = 24
    $title.Top = 20
    $title.Width = 490
    $title.Height = 28
    $form.Controls.Add($title)

    $message = New-Object System.Windows.Forms.Label
    $message.Text = "Esta maquina nao pertence ao dominio autorizado. Informe o token de validacao manual gerado no painel NightOwl."
    $message.Font = New-Object System.Drawing.Font("Segoe UI", 9)
    $message.Left = 24
    $message.Top = 58
    $message.Width = 490
    $message.Height = 42
    $form.Controls.Add($message)

    $meta = New-Object System.Windows.Forms.Label
    $meta.Text = "Hostname: $Hostname`r`nDominio detectado: $Domain`r`nServidor: $ServerBase"
    $meta.Font = New-Object System.Drawing.Font("Consolas", 8)
    $meta.Left = 24
    $meta.Top = 104
    $meta.Width = 490
    $meta.Height = 58
    $meta.ForeColor = [System.Drawing.Color]::FromArgb(178, 190, 214)
    $form.Controls.Add($meta)

    $tokenLabel = New-Object System.Windows.Forms.Label
    $tokenLabel.Text = "Token de validacao manual"
    $tokenLabel.Left = 24
    $tokenLabel.Top = 170
    $tokenLabel.Width = 220
    $tokenLabel.Height = 20
    $form.Controls.Add($tokenLabel)

    $tokenBox = New-Object System.Windows.Forms.TextBox
    $tokenBox.Left = 24
    $tokenBox.Top = 194
    $tokenBox.Width = 490
    $tokenBox.Height = 28
    $tokenBox.Font = New-Object System.Drawing.Font("Consolas", 10)
    $form.Controls.Add($tokenBox)

    $okButton = New-Object System.Windows.Forms.Button
    $okButton.Text = "Validar e instalar"
    $okButton.Left = 340
    $okButton.Top = 238
    $okButton.Width = 174
    $okButton.Height = 34
    $okButton.BackColor = [System.Drawing.Color]::FromArgb(38, 214, 126)
    $okButton.ForeColor = [System.Drawing.Color]::Black
    $okButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Controls.Add($okButton)

    $cancelButton = New-Object System.Windows.Forms.Button
    $cancelButton.Text = "Cancelar"
    $cancelButton.Left = 226
    $cancelButton.Top = 238
    $cancelButton.Width = 104
    $cancelButton.Height = 34
    $cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $form.Controls.Add($cancelButton)

    $form.AcceptButton = $okButton
    $form.CancelButton = $cancelButton
    $result = $form.ShowDialog()
    if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
        return ""
    }
    return $tokenBox.Text.Trim()
}

function Invoke-NightOwlEnrollment($BaseUrl, $EnrollmentTokenValue, $ManualTokenValue, $MachineId, $InstallPath, [switch]$NoGuiMode) {
    $info = Get-ComputerInfoLite
    Write-InstallLog "enrollment.auto.start" "Iniciando enrollment do agente." @{
        hostname = $info.Hostname
        domain = $info.Domain
        has_enrollment_token = -not [string]::IsNullOrWhiteSpace($EnrollmentTokenValue)
        has_manual_validation_token = -not [string]::IsNullOrWhiteSpace($ManualTokenValue)
    }
    try {
        $response = Invoke-EnrollmentRequest -BaseUrl $BaseUrl -EnrollmentTokenValue $EnrollmentTokenValue -ManualTokenValue $ManualTokenValue -MachineId $MachineId -InstallPath $InstallPath
        Write-InstallLog "enrollment.success" "Enrollment aprovado." @{ hostname = $info.Hostname; domain = $info.Domain }
        return $response
    }
    catch {
        $errorCode = [string]$_.Exception.Data["nightowl_error"]
        $reason = [string]$_.Exception.Data["nightowl_reason"]
        if ($errorCode -ne "manual_validation_required") {
            Write-InstallLog "enrollment.failed" "Enrollment falhou." @{ error = $errorCode; reason = $reason; detail = $_.Exception.Message }
            throw
        }

        Write-InstallLog "enrollment.manual.required" "Backend solicitou validacao manual." @{
            reason = $reason
            hostname = $info.Hostname
            domain = $info.Domain
        }

        $tokenToUse = $ManualTokenValue
        if ([string]::IsNullOrWhiteSpace($tokenToUse)) {
            if ($NoGuiMode) {
                $tokenToUse = Read-Host "Informe o token de validacao manual NightOwl"
            }
            else {
                $tokenToUse = Show-ManualValidationDialog -ServerBase $BaseUrl -Hostname $info.Hostname -Domain $info.Domain
            }
        }
        if ([string]::IsNullOrWhiteSpace($tokenToUse)) {
            throw "Validacao manual cancelada."
        }
        Write-InstallLog "enrollment.manual.retry" "Tentando enrollment com token manual." @{ hostname = $info.Hostname; domain = $info.Domain }
        try {
            $response = Invoke-EnrollmentRequest -BaseUrl $BaseUrl -EnrollmentTokenValue $EnrollmentTokenValue -ManualTokenValue $tokenToUse -MachineId $MachineId -InstallPath $InstallPath
            Write-InstallLog "enrollment.success" "Enrollment aprovado com validacao manual." @{ hostname = $info.Hostname; domain = $info.Domain; manual_validation_used = $true }
            return $response
        }
        catch {
            Write-InstallLog "enrollment.failed" "Enrollment manual falhou." @{
                error = [string]$_.Exception.Data["nightowl_error"]
                reason = [string]$_.Exception.Data["nightowl_reason"]
                detail = $_.Exception.Message
            }
            throw
        }
    }
}

function Save-AgentConfig($Path, $Config) {
    $Config | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

function Get-ConfigAgentToken($Config) {
    return Get-JsonProperty $Config @("agentToken", "agent_token", "AgentToken")
}

function Get-ConfigServerUrl($Config) {
    return Get-JsonProperty $Config @("serverBaseUrl", "server_base_url", "ServerBaseUrl")
}

function Read-AgentVersion([string]$InstallDir, $Config) {
    $versionFile = Read-JsonFile (Join-Path $InstallDir "agent.version.json")
    $version = Get-JsonProperty $versionFile @("version")
    if (-not [string]::IsNullOrWhiteSpace($version)) { return $version }
    $configVersion = Get-JsonProperty $Config @("agentVersion", "agent_version")
    if (-not [string]::IsNullOrWhiteSpace($configVersion)) { return $configVersion }
    return ""
}

function Assert-RequiredPackageFiles([string]$SourceDir, [switch]$RequireTrust) {
    $required = @(
        "NightOwl.Agent.Windows.exe",
        "NightOwl.Agent.Tray.exe",
        "NightOwl.Agent.Updater.exe",
        "NightOwl.Agent.Uninstaller.exe",
        "NightOwl.Agent.Diagnostics.exe",
        "agent.version.json",
        "assets\icons\NightOwl.ico"
    )
    if ($RequireTrust) {
        $required += @("release-public-keys.json", "release-trust-roots.json")
    }
    foreach ($relative in $required) {
        $path = Join-Path $SourceDir $relative
        if (-not (Test-Path $path)) {
            throw "INSTALL_PACKAGE_INVALID: pacote sem arquivo obrigatorio $relative"
        }
    }
}

function Copy-AgentBinaries([string]$SourceDir, [string]$DestinationDir) {
    Assert-RequiredPackageFiles -SourceDir $SourceDir -RequireTrust:(-not $TrustLocalPackage)
    $protectedNames = @(
        "agent.config.json",
        "agent.identity.json",
        "agent.state.json",
        "agent-dotnet.state.json",
        "update-state.json"
    )
    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
    foreach ($item in Get-ChildItem -Path $SourceDir -Force) {
        if ($protectedNames -contains $item.Name) {
            Write-InstallLog "binary.copy.protected_skipped" "Arquivo persistente ignorado durante copia de binarios." @{ name = $item.Name }
            continue
        }
        if ($item.Name -match "\.preserved-" -or $item.Name -in @("Config", "Identity", "State", "Logs", "Diagnostics", "Updates")) {
            continue
        }
        Copy-Item -Path $item.FullName -Destination $DestinationDir -Recurse -Force -Exclude @("*.pdb")
    }
    Add-ReportAction "binaries.copied" @{ source = $SourceDir; destination = $DestinationDir }
}

function Stop-TrayIfExists {
    Get-Process -Name "NightOwl.Agent.Tray" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

function Test-AgentLifecycleHealth([string]$Name, [string]$ConfigPath, [string]$IdentityPath, [string]$ExpectedMachineId, [string]$ExePath) {
    $errors = @()
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service) {
        $errors += "service_missing"
    }
    elseif ($service.Status -ne "Running") {
        $errors += "service_not_running:$($service.Status)"
    }
    if (-not (Test-Path $ExePath)) { $errors += "agent_exe_missing" }
    if (-not (Test-JsonFile $ConfigPath)) { $errors += "config_invalid" }
    if (-not (Test-JsonFile $IdentityPath)) { $errors += "identity_invalid" }
    $identity = Read-JsonFile $IdentityPath
    $identityMachineId = Get-JsonProperty $identity @("machine_id", "machineId")
    if ((Test-MachineId $ExpectedMachineId) -and (Test-MachineId $identityMachineId) -and $identityMachineId -ne $ExpectedMachineId) {
        $errors += "machine_id_mismatch"
    }
    return $errors
}

function Write-StateMachineId($Path, $MachineId) {
    $state = Read-JsonFile $Path
    if ($null -eq $state) {
        $state = [pscustomobject]@{}
    }
    if ($state.PSObject.Properties.Name -notcontains "machine_id") {
        $state | Add-Member -NotePropertyName "machine_id" -NotePropertyValue $MachineId
    }
    else {
        $state.machine_id = $MachineId
    }
    $state | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

function Stop-ServiceIfExists([string]$Name) {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne "Stopped") {
        Stop-Service -Name $Name -Force -ErrorAction SilentlyContinue
        $service.WaitForStatus("Stopped", "00:00:20")
    }
}

function Install-OrUpdateService([string]$Name, [string]$Display, [string]$ExePath) {
    Write-InstallLog "service.install.started" "Instalando ou atualizando servico .NET." @{
        service_name = $Name
        executable = $ExePath
    }
    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    try {
        if ($existing) {
            Stop-ServiceIfExists $Name
            sc.exe config $Name binPath= "`"$ExePath`"" start= delayed-auto obj= LocalSystem | Out-Null
            sc.exe description $Name "NightOwl RMM monitoring and management agent." | Out-Null
        }
        else {
            New-Service -Name $Name -DisplayName $Display -BinaryPathName "`"$ExePath`"" -StartupType Automatic -Description "NightOwl RMM monitoring and management agent." | Out-Null
            sc.exe config $Name start= delayed-auto | Out-Null
        }
        sc.exe failure $Name reset= 86400 actions= restart/60000/restart/120000/restart/300000 | Out-Null
        Write-InstallLog "service.install.completed" "Servico .NET configurado." @{
            service_name = $Name
            executable = $ExePath
            startup = "delayed-auto"
            account = "LocalSystem"
        }
    }
    catch {
        Write-InstallLog "service.install.failed" "Falha ao configurar servico .NET." @{
            service_name = $Name
            error = $_.Exception.Message
        }
        throw
    }
}

function Install-OrUpdateTrayTask([string]$TrayExePath) {
    if (-not (Test-Path $TrayExePath)) {
        Write-Step "WARN" "Tray app nao encontrado; tarefa de bandeja nao criada: $TrayExePath"
        Write-InstallLog "tray.install.skipped" "Tray app nao encontrado." @{ tray_exe = $TrayExePath }
        return
    }

    $taskName = "NightOwl Agent Tray"
    Write-InstallLog "tray.install.started" "Configurando tarefa agendada da bandeja." @{
        task_name = $taskName
        tray_exe = $TrayExePath
    }
    try {
        $taskCommand = "`"$TrayExePath`""
        $result = schtasks.exe /Create /TN $taskName /SC ONLOGON /TR $taskCommand /RU INTERACTIVE /RL LIMITED /F 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "schtasks.exe falhou: $result"
        }
        Write-Step "OK" "Tarefa de bandeja criada: $taskName"
        Write-InstallLog "tray.install.completed" "Tarefa agendada da bandeja criada." @{
            task_name = $taskName
            tray_exe = $TrayExePath
            trigger = "ONLOGON"
        }
    }
    catch {
        Write-InstallLog "tray.install.failed" "Falha ao criar tarefa da bandeja." @{
            task_name = $taskName
            tray_exe = $TrayExePath
            error = $_.Exception.Message
        }
        Write-Step "WARN" ("Nao foi possivel criar a tarefa de bandeja: {0}" -f $_.Exception.Message)
    }
}

function Start-TrayIfInteractive([string]$TrayExePath, [switch]$ForceStart) {
    if (-not (Test-Path $TrayExePath)) {
        return
    }
    if (-not $ForceStart -and [Environment]::UserInteractive -ne $true) {
        Write-Step "OK" "NightOwl Agent instalado. O icone sera exibido no proximo logon do usuario."
        Write-InstallLog "tray.start.deferred" "Tray app sera iniciado no proximo logon do usuario." @{ tray_exe = $TrayExePath }
        return
    }
    try {
        $existing = Get-Process -Name "NightOwl.Agent.Tray" -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Step "OK" "Tray app ja esta em execucao"
            return
        }
        Start-Process -FilePath $TrayExePath -WorkingDirectory (Split-Path -Parent $TrayExePath) | Out-Null
        Write-Step "OK" "Tray app iniciado"
        Write-InstallLog "tray.started" "Tray app iniciado pelo instalador." @{ tray_exe = $TrayExePath }
    }
    catch {
        Write-Step "WARN" ("Nao foi possivel iniciar o tray app: {0}" -f $_.Exception.Message)
        Write-InstallLog "tray.start.failed" "Falha ao iniciar tray app." @{ tray_exe = $TrayExePath; error = $_.Exception.Message }
    }
}

function Test-RecentHeartbeat([string]$LogPath) {
    if (-not (Test-Path $LogPath)) { return $false }
    $since = (Get-Date).ToUniversalTime().AddMinutes(-10)
    $lines = Get-Content -Path $LogPath -Tail 300 -ErrorAction SilentlyContinue
    foreach ($line in $lines) {
        try {
            $record = $line | ConvertFrom-Json
            if ($record.event_type -eq "heartbeat.sent" -and ([datetime]$record.timestamp) -ge $since) {
                return $true
            }
        }
        catch {}
    }
    return $false
}

$script:Operation = Get-OperationName
$script:Report = New-OperationReport -Operation $script:Operation
try {
Assert-Elevated
Acquire-UpdaterLockOrThrow
Assert-ForceRecoveryAllowed
Assert-NoActiveUpdate

if ($NonInteractive) {
    $NoGui = $true
}

$serverBase = Normalize-ServerUrl $ServerUrl
if ([string]::IsNullOrWhiteSpace($serverBase)) {
    throw "ServerUrl e obrigatorio."
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourcePath = $scriptRoot
$configPath = [string]$script:NightOwlPaths.ConfigPath
$legacyConfigPath = [string]$script:NightOwlPaths.LegacyConfigPath
$statePath = [string]$script:NightOwlPaths.StatePath
$legacyStatePath = [string]$script:NightOwlPaths.LegacyStatePath
$logPath = [string]$script:NightOwlPaths.AgentLog
$enrollResponse = $null
$existingInstallation = Test-Path (Join-Path $InstallPath "NightOwl.Agent.Windows.exe")
Write-InstallLog ("operation.{0}.started" -f $script:Operation) "Operacao do agente iniciada." @{
    operation = $script:Operation
    server_url = $serverBase
    install_path = $InstallPath
    existing_installation = $existingInstallation
    force_recovery = [bool]$ForceRecovery
}
Add-ReportAction "operation.started" @{ server_url = $serverBase; install_path = $InstallPath; existing_installation = $existingInstallation }

$directories = @(
    [string]$script:NightOwlPaths.Root,
    $InstallPath,
    [string]$script:NightOwlPaths.ConfigDir,
    [string]$script:NightOwlPaths.IdentityDir,
    [string]$script:NightOwlPaths.StateDir,
    [string]$script:NightOwlPaths.PendingResultsPath,
    [string]$script:NightOwlPaths.Trust,
    [string]$script:NightOwlPaths.Logs,
    [string]$script:NightOwlPaths.TrustBackups,
    [string]$script:NightOwlPaths.TrustDownloads,
    [string]$script:NightOwlPaths.Packages,
    [string]$script:NightOwlPaths.Cache,
    [string]$script:NightOwlPaths.Updates,
    [string]$script:NightOwlPaths.UpdatesDownloads,
    [string]$script:NightOwlPaths.UpdatesStaging,
    [string]$script:NightOwlPaths.UpdatesBackup,
    [string]$script:NightOwlPaths.UpdatesPending,
    [string]$script:NightOwlPaths.UpdatesRunner,
    [string]$script:NightOwlPaths.Diagnostics
)
foreach ($dir in $directories) {
    $existed = Test-Path $dir
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    if (-not $existed) {
        Write-InstallLog "path.directory.created" "Diretorio criado." @{ path = $dir }
        Add-ReportAction "directory.created" @{ path = $dir }
    }
}
Set-NightOwlSecureAcl -Paths @(
    [string]$script:NightOwlPaths.Root,
    $InstallPath,
    [string]$script:NightOwlPaths.ConfigDir,
    [string]$script:NightOwlPaths.IdentityDir,
    [string]$script:NightOwlPaths.StateDir,
    [string]$script:NightOwlPaths.PendingResultsPath,
    [string]$script:NightOwlPaths.Trust,
    [string]$script:NightOwlPaths.Logs
) -AllowUsersRead
Set-NightOwlSecureAcl -Paths @(
    [string]$script:NightOwlPaths.Updates,
    [string]$script:NightOwlPaths.UpdatesDownloads,
    [string]$script:NightOwlPaths.UpdatesStaging,
    [string]$script:NightOwlPaths.UpdatesBackup,
    [string]$script:NightOwlPaths.UpdatesPending,
    [string]$script:NightOwlPaths.UpdatesRunner,
    [string]$script:NightOwlPaths.Diagnostics,
    [string]$script:NightOwlPaths.TrustBackups,
    [string]$script:NightOwlPaths.TrustDownloads,
    [string]$script:NightOwlPaths.Packages,
    [string]$script:NightOwlPaths.Cache
)

$configInvalidCode = if ($script:Operation -eq "repair") { "REPAIR_CONFIG_INVALID" } else { "INSTALL_CONFIG_INVALID" }
$identityInvalidCode = if ($script:Operation -eq "repair") { "REPAIR_IDENTITY_INVALID" } else { "INSTALL_IDENTITY_INVALID" }
Repair-JsonFileIfInvalid -Path $configPath -ErrorCode $configInvalidCode
Repair-JsonFileIfInvalid -Path ([string]$script:NightOwlPaths.IdentityPath) -ErrorCode $identityInvalidCode

$localExe = Join-Path $sourcePath "NightOwl.Agent.Windows.exe"
$tempPackageDir = $null
if (-not (Test-Path $localExe) -or -not [string]::IsNullOrWhiteSpace($PackageUrl)) {
    $resolvedPackageUrl = Get-PackageUrl -Base $serverBase -ExplicitPackageUrl $PackageUrl
    $tempPackageDir = Join-Path $env:TEMP ("NightOwlAgentPackage-" + [guid]::NewGuid().ToString("N"))
    $sourcePath = Download-AgentPackage -Url $resolvedPackageUrl -WorkDir $tempPackageDir
    Write-Step "OK" "Modo download HTTPS ativo"
}
else {
    Write-Step "OK" "Modo local/offline ativo"
}

$preservedConfig = Read-JsonFile $configPath
if ($null -eq $preservedConfig -and (Test-Path $legacyConfigPath)) {
    Copy-Item -Path $legacyConfigPath -Destination $configPath -Force
    Write-InstallLog "path.file.migrated" "Config legado copiado para Config." @{ source = $legacyConfigPath; destination = $configPath }
    $preservedConfig = Read-JsonFile $configPath
}
elseif (Test-Path $legacyConfigPath) {
    Write-InstallLog "path.legacy.preserved" "Config legado preservado; Config ja existe." @{ legacy_path = $legacyConfigPath; config_path = $configPath }
}

$existingToken = Get-ConfigAgentToken $preservedConfig
$existingServer = Get-ConfigServerUrl $preservedConfig
if (-not [string]::IsNullOrWhiteSpace($existingServer) -and [string]::IsNullOrWhiteSpace($ServerUrl)) {
    $serverBase = Normalize-ServerUrl $existingServer
}

if ((-not (Test-Path $statePath)) -and (Test-Path $legacyStatePath)) {
    Copy-Item -Path $legacyStatePath -Destination $statePath -Force
    Write-InstallLog "path.file.migrated" "State legado copiado para State." @{ source = $legacyStatePath; destination = $statePath }
}

$identity = Resolve-MachineId -ConfigPath $configPath -StatePath $statePath
$machineId = $identity.Value
$identitySource = $identity.Source
$existingIdentity = Read-JsonFile ([string]$script:NightOwlPaths.IdentityPath)
$existingIdentityMachineId = Get-JsonProperty $existingIdentity @("machine_id", "machineId")
if ((Test-MachineId $machineId) -and (Test-MachineId $existingIdentityMachineId) -and $existingIdentityMachineId -ne $machineId) {
    Preserve-File -Path ([string]$script:NightOwlPaths.IdentityPath) -Reason "REPAIR_IDENTITY_CONFLICT" | Out-Null
    Write-InstallLog "identity.conflict" "Conflito entre Config/State e Identity; Config/State sera fonte de verdade." @{
        config_or_state_machine_id = (Protect-SecretValue $machineId)
        identity_machine_id = (Protect-SecretValue $existingIdentityMachineId)
    }
    Add-ReportWarning "REPAIR_IDENTITY_CONFLICT" "Conflito de machine_id; Config/State preservado como fonte operacional." @{}
}
$script:Report.machine_id = Protect-SecretValue $machineId
$script:Report.identity_preserved = Test-MachineId $machineId

if (-not [string]::IsNullOrWhiteSpace($EnrollmentToken) -and $EnrollmentToken.StartsWith("rmm_live_")) {
    Write-Step "WARN" "EnrollmentToken parece ser agent token legado/dev; usando como AgentToken."
    $AgentToken = $EnrollmentToken
}
elseif ([string]::IsNullOrWhiteSpace($AgentToken) -and -not [string]::IsNullOrWhiteSpace($existingToken) -and (Test-MachineId $machineId)) {
    $AgentToken = $existingToken
    Write-Step "OK" "Identidade existente preservada; enrollment nao sera executado"
    Write-InstallLog "identity.preserved" "Token e machine_id existentes preservados." @{
        machine_id = (Protect-SecretValue $machineId)
        operation = $script:Operation
    }
    Add-ReportAction "identity.preserved" @{ source = $identitySource }
}
elseif ([string]::IsNullOrWhiteSpace($AgentToken)) {
    Write-Step "OK" "Executando enrollment no servidor NightOwl"
    $enrollResponse = Invoke-NightOwlEnrollment -BaseUrl $serverBase -EnrollmentTokenValue $EnrollmentToken -ManualTokenValue $ManualValidationToken -MachineId $machineId -InstallPath $InstallPath -NoGuiMode:$NoGui
    $script:Report.enrollment_performed = $true
    if ($enrollResponse.agent_token) {
        $AgentToken = [string]$enrollResponse.agent_token
    }
    if ($enrollResponse.machine_id -and -not (Test-MachineId $machineId)) {
        $machineId = [string]$enrollResponse.machine_id
        $identitySource = "enrollment"
    }
    if ($enrollResponse.config) {
        Write-Step "OK" "Config de intervalos recebida do servidor"
    }
}

if ([string]::IsNullOrWhiteSpace($AgentToken)) {
    throw "AgentToken nao configurado. Verifique o enrollment token ou informe -AgentToken em modo legado/dev."
}

if ($InstallAsService) {
    Stop-ServiceIfExists $ServiceName
    Stop-TrayIfExists
}

$previousVersion = Read-AgentVersion -InstallDir $InstallPath -Config $preservedConfig
Copy-AgentBinaries -SourceDir $sourcePath -DestinationDir $InstallPath
$exePath = Join-Path $InstallPath "NightOwl.Agent.Windows.exe"
if (-not (Test-Path $exePath)) {
    throw "Executavel do agente nao encontrado apos copia: $exePath"
}
$trayExePath = Join-Path $InstallPath "NightOwl.Agent.Tray.exe"
$iconPath = Join-Path $InstallPath "assets\icons\NightOwl.ico"
Write-Step "OK" "Arquivos copiados"
if (-not (Test-Path $trayExePath)) {
    Write-Step "WARN" "NightOwl.Agent.Tray.exe nao encontrado no pacote"
}
if (-not (Test-Path $iconPath)) {
    Write-Step "WARN" "NightOwl.ico nao encontrado no pacote em assets\\icons"
    Write-InstallLog "tray.icon.missing" "Icone NightOwl nao encontrado no caminho esperado." @{ icon_path = $iconPath }
}

$packageVersionFile = Read-JsonFile (Join-Path $sourcePath "agent.version.json")
$packageVersion = Get-JsonProperty $packageVersionFile @("version")
if ([string]::IsNullOrWhiteSpace($packageVersion)) {
    $packageVersion = if ($preservedConfig.agentVersion) { [string]$preservedConfig.agentVersion } else { "0.1.0.7" }
}
$script:Report.previous_version = $previousVersion
$script:Report.installed_version = $packageVersion

$existingConfig = $preservedConfig
if ($null -eq $existingConfig) {
    $existingConfig = [pscustomobject]@{}
}

$config = [ordered]@{
    agentToken = $AgentToken
    machineId = $machineId
    agentVersion = $packageVersion
    serverBaseUrl = $serverBase
    heartbeatUrl = Join-AgentUrl $serverBase "/api/agent/heartbeat/"
    collectUrl = Join-AgentUrl $serverBase "/api/agent/collect/"
    jobsPullUrl = Join-AgentUrl $serverBase "/api/agent/jobs/pull/"
    jobsResultUrl = Join-AgentUrl $serverBase "/api/agent/jobs/result/"
    intervals = [ordered]@{
        heartbeatSeconds = if ($enrollResponse.config.heartbeat_seconds) { [int]$enrollResponse.config.heartbeat_seconds } else { 300 }
        collectSeconds = if ($enrollResponse.config.collect_seconds) { [int]$enrollResponse.config.collect_seconds } else { 3600 }
        jobsSeconds = if ($enrollResponse.config.jobs_seconds) { [int]$enrollResponse.config.jobs_seconds } else { 10 }
    }
    logPath = $logPath
    statePath = $statePath
    pendingResultsPath = [string]$script:NightOwlPaths.PendingResultsPath
    installPath = $InstallPath
    packagesPath = [string]$script:NightOwlPaths.Packages
    cachePath = [string]$script:NightOwlPaths.Cache
    jobsPath = [string]$script:NightOwlPaths.StateDir
    allowedJobTypes = @("ping", "collect_logs", "collect_disks", "collect_software", "collect_security", "windows_update_scan", "force_inventory", "update_agent", "update_trusted_release_keys", "restart_agent")
}
Save-AgentConfig -Path $configPath -Config $config
Write-StateMachineId -Path $statePath -MachineId $machineId
$identityInfo = [ordered]@{
    machine_id = $machineId
    source = $identitySource
    updated_at = (Get-Date).ToUniversalTime().ToString("o")
}
$identityInfo | ConvertTo-Json -Depth 5 | Set-Content -Path ([string]$script:NightOwlPaths.IdentityPath) -Encoding UTF8
$versionInfo = [ordered]@{
    version = $packageVersion
    installedAt = (Get-Date).ToUniversalTime().ToString("o")
    channel = "stable"
    packageSha256 = ""
    updatedBy = "installer"
}
$versionInfo | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $InstallPath "agent.version.json") -Encoding UTF8
Write-Step "OK" "Configuracao atualizada"
Write-Step "OK" ("Machine ID: {0} ({1})" -f $machineId, $identitySource)

if ($DisablePowerShellAgent) {
    $legacyTask = Get-ScheduledTask -TaskName "RMM-Agent-Heartbeat" -ErrorAction SilentlyContinue
    if ($legacyTask) {
        Disable-ScheduledTask -TaskName "RMM-Agent-Heartbeat" | Out-Null
        Write-Step "OK" "Tarefa PowerShell legada desabilitada"
    }
}
elseif ($KeepPowerShellAgent) {
    Write-Step "OK" "Agente PowerShell legado preservado"
}

if ($InstallAsService) {
    Install-OrUpdateService -Name $ServiceName -Display $DisplayName -ExePath $exePath
    Write-Step "OK" "Servico instalado"
    if ($StartService) {
        Start-Service -Name $ServiceName
        Start-Sleep -Seconds 3
        Write-Step "OK" "Servico iniciado"
    }
}

if (-not $NoTray) {
    Install-OrUpdateTrayTask -TrayExePath $trayExePath
    Start-TrayIfInteractive -TrayExePath $trayExePath -ForceStart:$StartTray
}
else {
    Write-Step "OK" "Tray app nao configurado por opcao -NoTray"
}

if ($RunCheck) {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service) {
        Write-Step "OK" ("Service status: {0}" -f $service.Status)
        $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if ($serviceInfo) {
            Write-Step "OK" ("Service startup type: {0}" -f $serviceInfo.StartMode)
            Write-Step "OK" ("Service account: {0}" -f $serviceInfo.StartName)
            Write-Step "OK" ("Service executable: {0}" -f $serviceInfo.PathName)
        }
    }
    else {
        Write-Step "WARN" "Servico nao encontrado"
    }
    if (Test-Path $configPath) { Write-Step "OK" "Config existe" } else { Write-Step "FAIL" "Config ausente" }
    if (-not $NoTray) {
        if (Test-Path $trayExePath) { Write-Step "OK" "Tray app existe" } else { Write-Step "WARN" "Tray app ausente" }
        if (Test-Path $iconPath) { Write-Step "OK" "NightOwl.ico existe em assets\\icons" } else { Write-Step "WARN" "NightOwl.ico ausente em assets\\icons" }
        $trayTask = Get-ScheduledTask -TaskName "NightOwl Agent Tray" -ErrorAction SilentlyContinue
        if ($trayTask) { Write-Step "OK" "Tray task instalada: NightOwl Agent Tray" } else { Write-Step "WARN" "Tray task nao encontrada" }
    }
    if ([string]::IsNullOrWhiteSpace($AgentToken) -or $AgentToken -eq "TOKEN") { Write-Step "FAIL" "Token invalido/placeholder" } else { Write-Step "OK" "Token configurado" }
    if (Test-Path $logPath) { Write-Step "OK" "Log existe" } else { New-Item -ItemType File -Force -Path $logPath | Out-Null; Write-Step "OK" "Log criado" }
    try {
        Invoke-WebRequest -Method Head -Uri (Join-AgentUrl $serverBase "/api/agent/heartbeat/") -TimeoutSec 10 | Out-Null
        Write-Step "OK" "Endpoint heartbeat acessivel"
    }
    catch {
        Write-Step "WARN" "Heartbeat nao validado via HEAD; o endpoint pode aceitar apenas POST"
    }
    if (Test-RecentHeartbeat $logPath) {
        Write-Step "OK" "Heartbeat recente encontrado no log"
    }
    else {
        Write-Step "WARN" "Heartbeat recente ainda nao encontrado; aguarde o ciclo do servico"
    }
}

$healthErrors = Test-AgentLifecycleHealth -Name $ServiceName -ConfigPath $configPath -IdentityPath ([string]$script:NightOwlPaths.IdentityPath) -ExpectedMachineId $machineId -ExePath $exePath
if ($healthErrors.Count -gt 0) {
    $healthCode = switch ($script:Operation) {
        "repair" { "REPAIR_HEALTHCHECK_FAILED" }
        "reinstall" { "REPAIR_HEALTHCHECK_FAILED" }
        default { "INSTALL_HEALTHCHECK_FAILED" }
    }
    Add-ReportWarning $healthCode "Health check local encontrou pendencias." @{ errors = $healthErrors }
    Write-InstallLog "operation.healthcheck.warning" "Health check local encontrou pendencias." @{ operation = $script:Operation; errors = $healthErrors; error_code = $healthCode }
}
else {
    Add-ReportAction "healthcheck.ok" @{ service_name = $ServiceName; version = $packageVersion }
    Write-InstallLog "operation.healthcheck.ok" "Health check local concluido." @{ operation = $script:Operation; service_name = $ServiceName; version = $packageVersion }
}

if ($tempPackageDir -and (Test-Path $tempPackageDir)) {
    Remove-Item -Path $tempPackageDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-OperationReport -Status "completed"
Write-InstallLog ("operation.{0}.completed" -f $script:Operation) "Operacao do agente concluida." @{ operation = $script:Operation; version = $packageVersion; machine_id = (Protect-SecretValue $machineId) }
Write-Step "OK" ("Operacao concluida: {0}" -f $script:Operation)
}
catch {
    $message = $_.Exception.Message
    $code = if ($message -match "^([A-Z0-9_]+):") { $matches[1] } else { Get-OperationErrorCode "UNEXPECTED_ERROR" }
    Write-InstallLog ("operation.{0}.failed" -f $script:Operation) "Operacao do agente falhou." @{ operation = $script:Operation; error_code = $code; error = $message }
    Write-OperationReport -Status "failed" -ErrorCode $code -ErrorMessage $message
    throw
}
finally {
    Release-UpdaterLock
    if ($tempPackageDir -and (Test-Path $tempPackageDir)) {
        Remove-Item -Path $tempPackageDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
