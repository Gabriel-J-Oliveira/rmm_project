[CmdletBinding()]
param(
    [string]$SourcePath = "\\192.168.104.120\controlsul\Comum\_Agents",
    [string]$InstallPath = "C:\RMM",
    [string]$ProgramDataPath = "C:\ProgramData\NightOwl",
    [switch]$RunAgentTest
)

$TaskName = "RMM-Agent-Heartbeat"
$ServiceName = "NightOwlAgent"
$warnings = 0
$errors = 0

function Write-Check {
    param([string]$Level, [string]$Message)
    Write-Host "[$Level] $Message"
    if ($Level -eq "WARN") { $script:warnings++ }
    if ($Level -eq "ERROR") { $script:errors++ }
}

function Read-VersionFromPath {
    param([string]$Path)
    $versionPath = Join-Path $Path "VERSION"
    if (Test-Path $versionPath) {
        return (Get-Content -Path $versionPath -Raw).Trim()
    }
    $manifestPath = Join-Path $Path "manifest.json"
    if (Test-Path $manifestPath) {
        try { return (Get-Content -Path $manifestPath -Raw | ConvertFrom-Json).version } catch { return "" }
    }
    return ""
}

$configPath = Join-Path $InstallPath "RmmAgent.config.ps1"
$agentPath = Join-Path $InstallPath "RmmAgent.ps1"
$manualValidationUiPath = Join-Path $InstallPath "NightOwlManualValidation.ps1"
$logoPath = Join-Path $InstallPath "assets\nightowl-logo.png"
$statePath = Join-Path $InstallPath "agent.state.json"
$logsPath = Join-Path $InstallPath "logs"
$serviceAgentPath = Join-Path $ProgramDataPath "Agent"
$serviceConfigPath = Join-Path $serviceAgentPath "RmmAgent.config.json"
$serviceScriptPath = Join-Path $serviceAgentPath "RmmAgentService.ps1"
$serviceStatePath = Join-Path $serviceAgentPath "agent.state.json"
$serviceLogsPath = Join-Path $ProgramDataPath "Logs"
$serviceJsonLogPath = Join-Path $serviceLogsPath "agent-service.jsonl"
$serviceInstallLogPath = Join-Path $serviceLogsPath "service-install.log"
$serviceStdoutLogPath = Join-Path $serviceLogsPath "service-stdout.log"
$serviceStderrLogPath = Join-Path $serviceLogsPath "service-stderr.log"

if (Test-Path $InstallPath) { Write-Check "OK" "Install path found: $InstallPath" } else { Write-Check "ERROR" "Install path missing: $InstallPath" }
if (Test-Path $agentPath) { Write-Check "OK" "Local agent found" } else { Write-Check "ERROR" "RmmAgent.ps1 missing" }
if (Test-Path $manualValidationUiPath) { Write-Check "OK" "Manual validation UI found" } else { Write-Check "WARN" "Manual validation UI missing" }
if (Test-Path $logoPath) { Write-Check "OK" "Night Owl logo asset found" } else { Write-Check "WARN" "Night Owl logo asset missing" }
if (Test-Path $configPath) { Write-Check "OK" "Config found" } else { Write-Check "ERROR" "Config missing" }
if (Test-Path $logsPath) { Write-Check "OK" "Logs directory found" } else { Write-Check "WARN" "Logs directory missing" }
if (Test-Path $statePath) { Write-Check "OK" "State file found" } else { Write-Check "WARN" "agent.state.json missing" }

$serverUrl = ""
$token = ""
if (Test-Path $configPath) {
    try {
        . $configPath
        $serverUrl = $RmmServerUrl
        $token = $AgentToken
        if ([string]::IsNullOrWhiteSpace($serverUrl)) { Write-Check "ERROR" "Server URL not configured" } else { Write-Check "OK" "server_url configured" }
        if ([string]::IsNullOrWhiteSpace($token) -or $token -eq "COLE_SEU_TOKEN_AQUI") { Write-Check "ERROR" "Agent token not configured" } else { Write-Check "OK" "token configured" }
    }
    catch {
        Write-Check "ERROR" "Could not load config: $($_.Exception.Message)"
    }
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $action = $task.Actions | Select-Object -First 1
    if ($action.Arguments -like "*$agentPath*") {
        Write-Check "OK" "Scheduled task found and points to local agent"
    }
    else {
        Write-Check "WARN" "Scheduled task found but command is: $($action.Arguments)"
    }
    try {
        $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Check "OK" "Task last run: $($taskInfo.LastRunTime)"
        Write-Check "OK" "Task last result: $($taskInfo.LastTaskResult)"
        Write-Check "OK" "Task next run: $($taskInfo.NextRunTime)"
    }
    catch {
        Write-Check "WARN" "Could not read scheduled task runtime info: $($_.Exception.Message)"
    }
}
else {
    Write-Check "ERROR" "Scheduled task missing"
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    Write-Check "OK" "Service installed: $ServiceName"
    Write-Check "OK" "Service status: $($service.Status)"
    try {
        $serviceCim = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
        Write-Check "OK" "Service startup type: $($serviceCim.StartMode)"
        Write-Check "OK" "Service account: $($serviceCim.StartName)"
        Write-Check "OK" "Service executable/arguments: $($serviceCim.PathName)"
    }
    catch {
        Write-Check "WARN" "Could not read service executable/startup details: $($_.Exception.Message)"
    }
    try {
        $delayedAutoStartPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName"
        $delayedAutoStart = (Get-ItemProperty -Path $delayedAutoStartPath -Name DelayedAutoStart -ErrorAction SilentlyContinue).DelayedAutoStart
        if ($null -ne $delayedAutoStart) {
            Write-Check "OK" "Service delayed auto start: $delayedAutoStart"
        }
    }
    catch {
        Write-Check "WARN" "Could not read delayed auto start flag: $($_.Exception.Message)"
    }
    if (Test-Path $serviceScriptPath) { Write-Check "OK" "Service script found" } else { Write-Check "ERROR" "Service script missing: $serviceScriptPath" }
    if (Test-Path $serviceConfigPath) {
        Write-Check "OK" "Service JSON config found"
        try {
            $serviceConfig = Get-Content -Path $serviceConfigPath -Raw | ConvertFrom-Json
            if ($serviceConfig.heartbeatUrl) { Write-Check "OK" "Service heartbeat configured: $($serviceConfig.heartbeatUrl)" } else { Write-Check "WARN" "Service heartbeat URL empty" }
            if ($serviceConfig.jobsPullUrl) { Write-Check "OK" "Jobs pull configured: $($serviceConfig.jobsPullUrl)" } else { Write-Check "WARN" "Jobs pull URL empty" }
            if ($serviceConfig.jobsResultUrl) { Write-Check "OK" "Jobs result configured: $($serviceConfig.jobsResultUrl)" } else { Write-Check "WARN" "Jobs result URL empty" }
            if ($serviceConfig.collectionEndpoints) {
                foreach ($property in $serviceConfig.collectionEndpoints.PSObject.Properties) {
                    if ($property.Value) {
                        Write-Check "OK" "Collection endpoint configured: $($property.Name)"
                    }
                    else {
                        Write-Check "WARN" "Collection endpoint empty: $($property.Name)"
                    }
                }
            }
            else {
                Write-Check "WARN" "Collection endpoints block missing"
            }
        }
        catch {
            Write-Check "WARN" "Could not read service JSON config details: $($_.Exception.Message)"
        }
    } else { Write-Check "WARN" "Service JSON config missing" }
    if (Test-Path $serviceLogsPath) { Write-Check "OK" "Service logs directory found" } else { Write-Check "WARN" "Service logs directory missing" }
    if (Test-Path $serviceJsonLogPath) { Write-Check "OK" "Service JSONL log found: $serviceJsonLogPath" } else { Write-Check "WARN" "Service JSONL log missing: $serviceJsonLogPath" }
    if (Test-Path $serviceInstallLogPath) { Write-Check "OK" "Service install log found" } else { Write-Check "WARN" "Service install log missing" }
    if (Test-Path $serviceStdoutLogPath) { Write-Check "OK" "Service stdout log found" } else { Write-Check "WARN" "Service stdout log missing" }
    if (Test-Path $serviceStderrLogPath) { Write-Check "OK" "Service stderr log found" } else { Write-Check "WARN" "Service stderr log missing" }
    if (Test-Path $serviceStatePath) {
        try {
            $serviceState = Get-Content -Path $serviceStatePath -Raw | ConvertFrom-Json
            Write-Check "OK" "Service last status: $($serviceState.lastStatus)"
            if ($serviceState.backoffUntil) {
                Write-Check "WARN" "Service backend backoff until: $($serviceState.backoffUntil)"
            }
        }
        catch {
            Write-Check "WARN" "Could not read service state file"
        }
    }
    else {
        Write-Check "WARN" "Service state file missing"
    }
}
else {
    Write-Check "WARN" "Service not installed: $ServiceName"
}

if ($serverUrl) {
    try {
        $baseUri = [Uri]$serverUrl
        $port = $baseUri.Port
        if ($port -lt 1) {
            if ($baseUri.Scheme -eq "https") { $port = 443 } else { $port = 80 }
        }
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($baseUri.Host, $port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(5000, $false)) {
            throw "TCP timeout to $($baseUri.Host):$port"
        }
        $client.EndConnect($async)
        $client.Close()
        Write-Check "OK" "Night Owl server reachable"
    }
    catch {
        Write-Check "WARN" "Night Owl server not reachable: $($_.Exception.Message)"
    }
}

if (Test-Path $SourcePath) {
    Write-Check "OK" "Update source reachable"
    if ((Test-Path (Join-Path $SourcePath "manifest.json")) -or (Test-Path (Join-Path $SourcePath "VERSION"))) {
        Write-Check "OK" "Package manifest or VERSION available"
    }
    else {
        Write-Check "WARN" "Package manifest/VERSION missing in SourcePath"
    }
    try {
        Get-Content -Path (Join-Path $SourcePath "RmmAgent.ps1") -TotalCount 1 -ErrorAction Stop | Out-Null
        Write-Check "OK" "Package files readable"
    }
    catch {
        Write-Check "ERROR" "Package files not readable from SourcePath: $($_.Exception.Message)"
    }
}
else {
    Write-Check "ERROR" "Update source not reachable: $SourcePath"
}

$localVersion = Read-VersionFromPath -Path $InstallPath
$centralVersion = Read-VersionFromPath -Path $SourcePath
if ($localVersion) { Write-Check "OK" "Local version: $localVersion" } else { Write-Check "WARN" "Local version unknown" }
if ($centralVersion) { Write-Check "OK" "Central version: $centralVersion" } else { Write-Check "WARN" "Central version unknown" }
if ($localVersion -and $centralVersion -and $localVersion -ne $centralVersion) {
    Write-Check "WARN" "Local version $localVersion differs from central version $centralVersion"
}
elseif ($localVersion -and $centralVersion) {
    Write-Check "OK" "Local version matches central version"
}

if ($statePath -and (Test-Path $statePath)) {
    try {
        $state = Get-Content -Path $statePath -Raw | ConvertFrom-Json
        Write-Check "OK" "State update_source: $($state.update_source)"
        if ($state.last_status) {
            Write-Check "OK" "Last agent status: $($state.last_status)"
        }
        if ($state.last_run_at) {
            Write-Check "OK" "Last run at: $($state.last_run_at)"
        }
        if ($state.last_success_at) {
            Write-Check "OK" "Last success at: $($state.last_success_at)"
        }
        if ($state.last_error) {
            Write-Check "WARN" "Last agent error: $($state.last_error)"
        }
        if ($state.last_update_status) {
            Write-Check "OK" "Last update status: $($state.last_update_status)"
        }
        if ($state.last_update_error) {
            Write-Check "WARN" "Last update error: $($state.last_update_error)"
        }
        if ($state.install_mode) {
            Write-Check "OK" "Install mode: $($state.install_mode)"
        }
        if ($state.enrollment_used) {
            Write-Check "OK" "Config was created by enrollment"
            if ($state.enrolled_at) {
                Write-Check "OK" "Enrolled at: $($state.enrolled_at)"
            }
            if ($state.enrollment_token_prefix) {
                Write-Check "OK" "Enrollment token prefix: $($state.enrollment_token_prefix)"
            }
            if ($state.machine_id) {
                Write-Check "OK" "Machine ID: $($state.machine_id)"
            }
            if ($state.enrollment_domain_validation) {
                Write-Check "OK" "Enrollment domain validation: $($state.enrollment_domain_validation)"
            }
            if ($state.manual_validation_used) {
                Write-Check "OK" "Manual validation was used during enrollment"
            }
        }
    }
    catch {
        Write-Check "WARN" "Could not read state file"
    }
}

if ($RunAgentTest -and (Test-Path $agentPath)) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $agentPath
    if ($LASTEXITCODE -eq 0) {
        Write-Check "OK" "Agent manual run succeeded"
    }
    else {
        Write-Check "ERROR" "Agent manual run failed with exit code $LASTEXITCODE"
    }
}

if ($errors -gt 0) { exit 2 }
if ($warnings -gt 0) { exit 1 }
exit 0
