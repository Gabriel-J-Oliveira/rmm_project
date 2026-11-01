Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$previousProgressPreference = $ProgressPreference
$ProgressPreference = 'SilentlyContinue'
$previousInstallerSilent = [Environment]::GetEnvironmentVariable('NIGHTOWL_INSTALLER_SILENT', 'Process')

$MetadataUrl = "__NIGHTOWL_DEPLOYMENT_METADATA_URL__"
$Stage = "start"
$TempRoot = Join-Path $env:TEMP "NightOwlDeployment"
$BootstrapLogPath = Join-Path $TempRoot "bootstrap.log"
$Token = $env:NIGHTOWL_DEPLOYMENT_TOKEN

function Protect-NightOwlLogValue([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }
    if ($Value -match "(?i)deploy_|enroll_|rmm_live_|bearer|authorization|token|secret|password|private") {
        return "[REDACTED]"
    }
    if ($Value.Length -gt 256) { return $Value.Substring(0, 256) }
    return $Value
}

function Write-NightOwlBootstrapLog([string]$Status, [hashtable]$Fields) {
    $mutex = $null; $locked = $false
    try {
        New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
        $record = [ordered]@{
            timestamp = (Get-Date).ToUniversalTime().ToString("o")
            stage = $Stage
            status = $Status
        }
        foreach ($key in $Fields.Keys) {
            $record[$key] = Protect-NightOwlLogValue ([string]$Fields[$key])
        }
        $mutex = New-Object System.Threading.Mutex($false, 'Local\NightOwl.DeploymentBootstrapLog')
        try { $locked = $mutex.WaitOne(5000) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { return }
        if ((Test-Path -LiteralPath $BootstrapLogPath -PathType Leaf) -and (Get-Item -LiteralPath $BootstrapLogPath).Length -ge 1048576) {
            Move-Item -LiteralPath $BootstrapLogPath -Destination ($BootstrapLogPath + '.1') -Force
        }
        $record | ConvertTo-Json -Depth 5 -Compress | Add-Content -LiteralPath $BootstrapLogPath -Encoding UTF8
    }
    catch { }
    finally {
        if ($locked) { $mutex.ReleaseMutex() }
        if ($mutex) { $mutex.Dispose() }
    }
}

function Write-NightOwlResult([string]$Status, [hashtable]$Fields) {
    # The frontend and bounded local log carry progress; errors are reported below.
}

function Assert-Windows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "BOOTSTRAP_WINDOWS_REQUIRED"
    }
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "BOOTSTRAP_ADMIN_REQUIRED"
    }
}

function Assert-Https([string]$Url) {
    $uri = [Uri]$Url
    if ($uri.Scheme -ne "https") {
        throw "BOOTSTRAP_HTTPS_REQUIRED"
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-VerifiedTrustBundle([string]$Url, [string]$Path, [string]$ExpectedSha) {
    if ($ExpectedSha -notmatch '^[a-fA-F0-9]{64}$') { throw "BOOTSTRAP_TRUST_SHA_MISMATCH" }
    if ((Test-Path -LiteralPath $Path -PathType Leaf) -and (Get-Sha256 $Path) -eq $ExpectedSha.ToLowerInvariant()) {
        return
    }
    $candidate = $Path + "." + [guid]::NewGuid().ToString("N") + ".partial"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $candidate -UseBasicParsing -ErrorAction Stop
        if ((Get-Sha256 $candidate) -ne $ExpectedSha.ToLowerInvariant()) { throw "BOOTSTRAP_TRUST_SHA_MISMATCH" }
        Move-Item -LiteralPath $candidate -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $candidate) { Remove-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue }
    }
}

function Invoke-JsonGet([string]$Url, [string]$DeploymentToken) {
    return Invoke-RestMethod -Method Get -Uri $Url -Headers @{ "X-NightOwl-Deployment-Token" = $DeploymentToken } -UseBasicParsing
}

function Invoke-JsonPost([string]$Url, [hashtable]$Headers, [hashtable]$Body) {
    $json = $Body | ConvertTo-Json -Depth 8
    return Invoke-RestMethod -Method Post -Uri $Url -Headers $Headers -Body $json -ContentType "application/json" -UseBasicParsing -TimeoutSec 5
}

function Get-OptionalJsonProperty($Object, [string]$Name) {
    if ($null -eq $Object) {
        return ""
    }
    if ($Object.PSObject.Properties.Name -contains $Name) {
        return [string]$Object.$Name
    }
    return ""
}

function Read-AgentConfig {
    $configPath = "C:\ProgramData\NightOwl\Config\agent.config.json"
    try {
        if (-not (Test-Path -LiteralPath $configPath -ErrorAction Stop)) {
            return $null
        }
        return Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-ExistingInstallationState([string]$Root, $Metadata) {
    $exe = Join-Path $Root 'AgentDotNet\NightOwl.Agent.Windows.exe'
    $svc = Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue
    $hasExe = Test-Path -LiteralPath $exe -PathType Leaf
    if (-not $svc -and -not $hasExe) { return 'absent' }
    if (-not $svc -or -not $hasExe) { return 'incomplete' }
    try {
        $config = Get-Content -Raw -LiteralPath (Join-Path $Root 'Config\agent.config.json') | ConvertFrom-Json
        $identity = Get-Content -Raw -LiteralPath (Join-Path $Root 'Identity\agent.identity.json') | ConvertFrom-Json
        $state = Get-Content -Raw -LiteralPath (Join-Path $Root 'State\agent.state.json') | ConvertFrom-Json
        $version = Get-Content -Raw -LiteralPath (Join-Path $Root 'AgentDotNet\agent.version.json') | ConvertFrom-Json
        $machine = Get-OptionalJsonProperty $config 'machineId'
        if ([string]::IsNullOrWhiteSpace($machine) -or [string]::IsNullOrWhiteSpace((Get-OptionalJsonProperty $config 'agentToken')) -or
            $machine -ne (Get-OptionalJsonProperty $identity 'machine_id') -or $machine -ne (Get-OptionalJsonProperty $state 'machine_id')) { return 'incomplete' }
        if ((Get-OptionalJsonProperty $version 'version') -ne [string]$Metadata.release.version -or
            (Get-OptionalJsonProperty $version 'packageSha256') -ne [string]$Metadata.release.sha256) { return 'different_version' }
        if ((Get-OptionalJsonProperty $config 'serverBaseUrl').TrimEnd('/') -ne ([string]$Metadata.server_url).TrimEnd('/')) { return 'unverified' }
        $heartbeat = [DateTimeOffset]::MinValue
        $raw = Get-OptionalJsonProperty $state 'lastHeartbeatAt'
        if ($svc.Status.ToString() -ne 'Running' -or -not [DateTimeOffset]::TryParse($raw, [ref]$heartbeat)) { return 'unverified' }
        $age = [DateTimeOffset]::UtcNow - $heartbeat.ToUniversalTime()
        if ($age.TotalSeconds -lt 0 -or $age.TotalMinutes -gt 10) { return 'unverified' }
        return 'healthy_compatible'
    } catch { return 'incomplete' }
}

function Send-DeploymentCompletion($Metadata, [string]$Status, [string]$ErrorCode, [string]$ErrorMessage) {
    $completionUrl = Get-OptionalJsonProperty -Object $Metadata -Name "completion_url"
    if ([string]::IsNullOrWhiteSpace($completionUrl)) {
        return [pscustomobject]@{ ok = $false; error = "completion_url_missing" }
    }
    $config = Read-AgentConfig
    $agentToken = Get-OptionalJsonProperty -Object $config -Name "agentToken"
    $machineId = Get-OptionalJsonProperty -Object $config -Name "machineId"
    if ([string]::IsNullOrWhiteSpace($machineId)) {
        $machineId = Get-OptionalJsonProperty -Object $config -Name "machine_id"
    }
    $headers = @{}
    if (-not [string]::IsNullOrWhiteSpace($agentToken)) {
        $headers["Authorization"] = "Bearer $agentToken"
    }
    elseif (-not [string]::IsNullOrWhiteSpace($Token)) {
        $headers["X-NightOwl-Deployment-Token"] = $Token
    }
    else {
        return [pscustomobject]@{ ok = $false; error = "completion_credentials_missing" }
    }
    $serviceStatus = "Unknown"
    try {
        $serviceStatus = (Get-Service -Name "NightOwlAgentDotNet" -ErrorAction Stop).Status.ToString()
    } catch {}
    $body = @{
        deployment_id = [string]$Metadata.deployment_id
        status = $Status
        machine_id = $machineId
        version = [string]$Metadata.release.version
        service_status = $serviceStatus
        health_check_confirmed = ($Status -in @('completed', 'pending') -and $serviceStatus -eq "Running")
        error_code = $ErrorCode
        error_message = $ErrorMessage
    }
    try {
        $response = Invoke-JsonPost -Url $completionUrl -Headers $headers -Body $body
        $state = Get-OptionalJsonProperty -Object $response -Name 'status'
        if ($Status -eq 'completed' -and $state -eq 'awaiting_heartbeat') {
            return [pscustomobject]@{ ok = $false; error = 'awaiting_heartbeat' }
        }
        if ($Status -eq 'pending' -and $state -eq 'completed') { return [pscustomobject]@{ ok = $true; error = 'completed' } }
        if ($state -ne $Status) { return [pscustomobject]@{ ok = $false; error = 'completion_response_invalid' } }
        return [pscustomobject]@{ ok = $true; error = "" }
    }
    catch {
        return [pscustomobject]@{ ok = $false; error = "completion_callback_failed" }
    }
}

try {
    $Stage = "preflight"
    Assert-Windows
    Assert-Administrator
    if ([string]::IsNullOrWhiteSpace($Token)) {
        throw "BOOTSTRAP_TOKEN_REQUIRED"
    }
    Assert-Https $MetadataUrl

    $Stage = "metadata"
    $metadata = Invoke-JsonGet -Url $MetadataUrl -DeploymentToken $Token
    Assert-Https ([string]$metadata.server_url)
    Assert-Https ([string]$metadata.release.installer_url)
    Assert-Https ([string]$metadata.release.package_url)
    Assert-Https ([string]$metadata.trusted_public_keys.url)

    $existingState = Get-ExistingInstallationState -Root 'C:\ProgramData\NightOwl' -Metadata $metadata
    Write-NightOwlBootstrapLog "existing_installation" @{ state = $existingState }
    if ($existingState -ne 'healthy_compatible') {
    $Stage = "download"
    New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
    $trustPath = Join-Path $TempRoot "release-public-keys.json"
    $installerPath = Join-Path $TempRoot "Install-NightOwlAgentDotNet.ps1"
    $expectedTrustSha = ([string]$metadata.trusted_public_keys.sha256).ToLowerInvariant()
    Get-VerifiedTrustBundle -Url ([string]$metadata.trusted_public_keys.url) -Path $trustPath -ExpectedSha $expectedTrustSha
    Invoke-WebRequest -Uri ([string]$metadata.release.installer_url) -OutFile $installerPath -UseBasicParsing

    $Stage = "install"
    $installArgs = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $installerPath,
        "-Install",
        "-InstallAsService",
        "-ServerUrl",
        ([string]$metadata.server_url),
        "-PackageUrl",
        ([string]$metadata.release.package_url),
        "-TrustedPublicKeysPath",
        $trustPath,
        "-ExpectedVersion",
        ([string]$metadata.release.version),
        "-ExpectedChannel",
        ([string]$metadata.release.channel),
        "-ExpectedPackageSha256",
        ([string]$metadata.release.sha256),
        "-ExpectedReleaseId",
        ([string]$metadata.release.id),
        "-RunCheck",
        "-NonInteractive"
    )
    $expectedGitCommit = Get-OptionalJsonProperty -Object $metadata.release -Name "git_commit"
    if (-not [string]::IsNullOrWhiteSpace($expectedGitCommit)) {
        $installArgs += @("-ExpectedGitCommit", $expectedGitCommit)
    }
    $env:NIGHTOWL_INSTALLER_ENROLLMENT_TOKEN = $Token
    $env:NIGHTOWL_INSTALLER_SILENT = '1'
    $installerErrorPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 can turn redirected native stderr into a
        # terminating error under Stop before LASTEXITCODE is read.
        $ErrorActionPreference = 'Continue'
        & powershell.exe @installArgs *> $null
        $installerExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $installerErrorPreference
        Remove-Item Env:\NIGHTOWL_INSTALLER_ENROLLMENT_TOKEN -ErrorAction SilentlyContinue
        [Environment]::SetEnvironmentVariable('NIGHTOWL_INSTALLER_SILENT', $previousInstallerSilent, 'Process')
    }
    if ($installerExitCode -ne 0) {
        throw "BOOTSTRAP_INSTALLER_FAILED:$installerExitCode"
    }
    }

    $Stage = "completed"
    $machineId = ""
    $version = [string]$metadata.release.version
    $config = Read-AgentConfig
    if ($null -ne $config) {
        $machineId = Get-OptionalJsonProperty -Object $config -Name "machineId"
        if ([string]::IsNullOrWhiteSpace($machineId)) {
            $machineId = Get-OptionalJsonProperty -Object $config -Name "machine_id"
        }
    }
    $serviceStatus = ""
    try {
        $serviceStatus = (Get-Service -Name "NightOwlAgentDotNet" -ErrorAction Stop).Status.ToString()
    } catch {
        $serviceStatus = "Unknown"
    }
    $localInstallStatus = if ($serviceStatus -eq 'Running') { 'completed' } else { 'unverified' }
    $timeoutSeconds = 30
    $configuredTimeout = 0
    if ([int]::TryParse($env:NIGHTOWL_DEPLOYMENT_CONFIRM_TIMEOUT_SECONDS, [ref]$configuredTimeout)) {
        $timeoutSeconds = [Math]::Max(0, [Math]::Min(300, $configuredTimeout))
    }
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    do {
        $confirmation = Send-DeploymentCompletion -Metadata $metadata -Status "completed" -ErrorCode "" -ErrorMessage ""
        if ($confirmation.ok -or $confirmation.error -ne 'awaiting_heartbeat' -or $deadline.Elapsed.TotalSeconds -ge $timeoutSeconds) { break }
        Start-Sleep -Seconds ([Math]::Min(3, [Math]::Max(1, $timeoutSeconds - [int]$deadline.Elapsed.TotalSeconds)))
    } while ($true)
    if (-not $confirmation.ok -and $confirmation.error -eq 'awaiting_heartbeat') {
        $timedOut = Send-DeploymentCompletion -Metadata $metadata -Status 'pending' -ErrorCode '' -ErrorMessage ''
        if ($timedOut.ok -and $timedOut.error -eq 'completed') { $confirmation = $timedOut }
    }
    $confirmationStatus = if ($confirmation.ok) { "completed" } elseif ($confirmation.error -eq 'awaiting_heartbeat') { 'pending' } else { 'failed' }
    if (-not $confirmation.ok) {
        Write-NightOwlBootstrapLog "warning" @{
            warning_code = "BOOTSTRAP_COMPLETION_CALLBACK_FAILED"
            warning_message = $confirmation.error
            installation_status = $localInstallStatus
            deployment_confirmation_status = "failed"
        }
    }
    $resultStatus = if ($confirmation.ok) { 'completed' } else { 'pending' }
    Write-NightOwlResult $resultStatus @{
        hostname = $env:COMPUTERNAME
        machine_id = $machineId
        version = $version
        service_status = $serviceStatus
        enrollment_status = "completed"
        installation_status = $localInstallStatus
        deployment_confirmation_status = $confirmationStatus
    }
    Write-NightOwlBootstrapLog $resultStatus @{
        hostname = $env:COMPUTERNAME
        machine_id = $machineId
        version = $version
        service_status = $serviceStatus
        deployment_confirmation_status = $confirmationStatus
    }
    $global:NightOwlDeploymentBootstrapExitCode = if ($confirmation.ok) { 0 } else { 2 }
} catch {
    $rawMessage = [string]$_.Exception.Message
    $candidateCode = ($rawMessage.Split(":")[0]).Trim().ToUpperInvariant()
    $errorCode = if ($candidateCode -match "^[A-Z0-9_]{1,80}$" -and $candidateCode -notmatch '(?i)DEPLOY_|ENROLL_|RMM_LIVE_|PASSWORD|SECRET') { $candidateCode } else {
        switch ($Stage) {
            "metadata" { "BOOTSTRAP_METADATA_REQUEST_FAILED"; break }
            "download" { "BOOTSTRAP_DOWNLOAD_FAILED"; break }
            "install" { "BOOTSTRAP_INSTALL_METADATA_INCOMPLETE"; break }
            "completed" { "BOOTSTRAP_CONFIRMATION_REPORT_FAILED"; break }
            default { "BOOTSTRAP_FAILED" }
        }
    }
    $errorType = $_.Exception.GetType().Name
    $safeMessages = @{
        "BOOTSTRAP_WINDOWS_REQUIRED" = "Este comando deve ser executado no Windows."
        "BOOTSTRAP_ADMIN_REQUIRED" = "Abra o PowerShell como administrador e execute novamente."
        "BOOTSTRAP_HTTPS_REQUIRED" = "O bootstrap exige URLs HTTPS validas."
        "BOOTSTRAP_TOKEN_REQUIRED" = "O token de deployment nao foi fornecido. Gere um novo deployment."
        "BOOTSTRAP_TRUST_SHA_MISMATCH" = "O trust bundle baixado nao corresponde ao hash publicado."
        "BOOTSTRAP_INSTALLER_FAILED" = "O instalador terminou com falha. Consulte o log local do instalador."
        "BOOTSTRAP_INSTALL_METADATA_INCOMPLETE" = "Os metadados obrigatorios da release estao incompletos. Revise a publicacao antes de tentar novamente."
        "BOOTSTRAP_CONFIRMATION_REPORT_FAILED" = "A instalacao terminou, mas o resultado nao pode ser confirmado. Nao repita sem revisar o endpoint."
        "BOOTSTRAP_FAILED" = "Falha no bootstrap. Consulte o codigo e os logs locais do instalador."
    }
    $message = if ($safeMessages.ContainsKey($errorCode)) { $safeMessages[$errorCode] } else { "Falha durante a etapa $Stage. Consulte o codigo e os logs locais do instalador." }
    $metadataVar = Get-Variable -Name metadata -Scope Local -ErrorAction SilentlyContinue
    if ($null -ne $metadataVar -and $null -ne $metadataVar.Value) {
        try {
            Send-DeploymentCompletion -Metadata $metadata -Status "failed" -ErrorCode $errorCode -ErrorMessage $message | Out-Null
        }
        catch {
            Write-NightOwlBootstrapLog "failed" @{
                error_code = "BOOTSTRAP_STATUS_REPORT_FAILED"
                error_message = 'completion_callback_failed'
            }
        }
    }
    Write-NightOwlResult "failed" @{
        stage = $Stage
        error_code = $errorCode
        error_type = $errorType
        error_message = $message
    }
    Write-NightOwlBootstrapLog "failed" @{
        error_code = $errorCode
        error_type = $errorType
        error_message = $message
    }
    Write-Host ("NightOwl: {0} Codigo: {1}. Log: {2}" -f $message, $errorCode, $BootstrapLogPath) -ForegroundColor Red
    $global:NightOwlDeploymentBootstrapExitCode = 1
} finally {
    Remove-Item Env:\NIGHTOWL_INSTALLER_ENROLLMENT_TOKEN -ErrorAction SilentlyContinue
    [Environment]::SetEnvironmentVariable('NIGHTOWL_INSTALLER_SILENT', $previousInstallerSilent, 'Process')
    Remove-Item Env:\NIGHTOWL_DEPLOYMENT_TOKEN -ErrorAction SilentlyContinue
    $ProgressPreference = $previousProgressPreference
}
