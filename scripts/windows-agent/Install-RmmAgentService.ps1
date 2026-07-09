[CmdletBinding()]
param(
    [string]$SourcePath = "\\192.168.104.120\controlsul\Comum\_Agents",
    [string]$BasePath = "C:\ProgramData\NightOwl",
    [string]$LegacyInstallPath = "C:\RMM",
    [string]$ServerUrl = "",
    [string]$AgentToken = "",
    [string]$NssmPath = "",
    [string]$WinswPath = "",
    [switch]$RunOnce,
    [switch]$DebugMode,
    [switch]$KeepScheduledTaskFallback
)

$ErrorActionPreference = "Stop"
$ServiceName = "NightOwlAgent"
$DisplayName = "NightOwl RMM Agent"
$AgentPath = Join-Path $BasePath "Agent"
$LogPath = Join-Path $BasePath "Logs"
$PackagesPath = Join-Path $BasePath "Packages"
$CachePath = Join-Path $BasePath "Cache"
$ConfigPath = Join-Path $AgentPath "RmmAgent.config.json"
$InstallLog = Join-Path $LogPath "service-install.log"

function Write-ServiceInstallLog {
    param([string]$Message, [string]$Level = "INFO")
    if (-not (Test-Path $LogPath)) {
        New-Item -Path $LogPath -ItemType Directory -Force | Out-Null
    }
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $InstallLog -Value "[$timestamp] [$Level] $Message"
    Write-Host "[$Level] $Message"
}

function Initialize-ServiceDirectories {
    foreach ($path in @($BasePath, $AgentPath, $LogPath, $PackagesPath, $CachePath)) {
        if (-not (Test-Path $path)) {
            New-Item -Path $path -ItemType Directory -Force | Out-Null
        }
    }
}

function Read-LegacyConfigValue {
    param([string]$Path, [string]$VariableName)
    if (-not (Test-Path $Path)) { return "" }
    $content = Get-Content -Path $Path -Raw
    $pattern = ('\${0}\s*=\s*["''](?<value>[^"'']+)["'']' -f [regex]::Escape($VariableName))
    if ($content -match $pattern) {
        return $Matches["value"]
    }
    return ""
}

function Copy-ServiceFile {
    param([string]$FileName, [switch]$Required)
    $source = Join-Path $SourcePath $FileName
    $destination = Join-Path $AgentPath $FileName
    if (-not (Test-Path $source)) {
        if ($Required) {
            throw "Required service package file missing: $source"
        }
        Write-ServiceInstallLog "Skipping missing package file: $source" "WARN"
        return
    }
    Copy-Item -Path $source -Destination $destination -Force
    Write-ServiceInstallLog "Copied $FileName to $AgentPath"
}

function Resolve-NssmPath {
    if (-not [string]::IsNullOrWhiteSpace($NssmPath) -and (Test-Path $NssmPath)) {
        return (Resolve-Path $NssmPath).Path
    }
    $common = @(
        (Join-Path $AgentPath "nssm.exe"),
        (Join-Path $SourcePath "nssm.exe"),
        (Join-Path $SourcePath "tools\nssm.exe")
    )
    foreach ($candidate in $common) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }
    $command = Get-Command "nssm.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return ""
}

function Copy-NssmToAgent {
    param([string]$ResolvedNssmPath)
    $destination = Join-Path $AgentPath "nssm.exe"
    if ([string]::IsNullOrWhiteSpace($ResolvedNssmPath) -or -not (Test-Path $ResolvedNssmPath)) {
        return ""
    }
    $resolvedFull = (Resolve-Path $ResolvedNssmPath).Path
    $destinationFull = [System.IO.Path]::GetFullPath($destination)
    if (-not [string]::Equals($resolvedFull, $destinationFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -Path $resolvedFull -Destination $destinationFull -Force
        Write-ServiceInstallLog "Copied NSSM to $destinationFull"
    }
    return $destinationFull
}

function Get-NightOwlApiUrl {
    param(
        [string]$Server,
        [string]$ApiPath
    )
    if ([string]::IsNullOrWhiteSpace($Server)) { return "" }
    $trimmed = $Server.TrimEnd("/")
    if ($trimmed -match "/api/agent/heartbeat$") {
        return ($trimmed -replace "/api/agent/heartbeat$", $ApiPath)
    }
    if ($trimmed -match "/api/agent/.+$") {
        return ($trimmed -replace "/api/agent/.+$", $ApiPath)
    }
    return ($trimmed + $ApiPath)
}

function Get-NightOwlBaseUrl {
    param([string]$Server)
    if ([string]::IsNullOrWhiteSpace($Server)) { return "" }
    $trimmed = $Server.TrimEnd("/")
    if ($trimmed -match "/api/agent/heartbeat$") {
        return ($trimmed -replace "/api/agent/heartbeat$", "")
    }
    if ($trimmed -match "/api/agent/.+$") {
        return ($trimmed -replace "/api/agent/.+$", "")
    }
    return $trimmed
}

function Set-JsonObjectValue {
    param(
        $Object,
        [string]$Name,
        $Value
    )
    if ($Object.PSObject.Properties[$Name]) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Repair-ServiceConfigEndpoints {
    param([string]$FallbackServer)
    if (-not (Test-Path $ConfigPath)) { return }
    try {
        $configObject = Get-Content -Path $ConfigPath -Raw | ConvertFrom-Json
        $server = $configObject.serverUrl
        if ([string]::IsNullOrWhiteSpace([string]$server)) { $server = $configObject.heartbeatUrl }
        if ([string]::IsNullOrWhiteSpace([string]$server)) { $server = $FallbackServer }
        if ([string]::IsNullOrWhiteSpace([string]$server)) { return }

        Set-JsonObjectValue -Object $configObject -Name "serverUrl" -Value (Get-NightOwlBaseUrl -Server $server)
        if ([string]::IsNullOrWhiteSpace([string]$configObject.heartbeatUrl)) {
            Set-JsonObjectValue -Object $configObject -Name "heartbeatUrl" -Value (Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/heartbeat/")
        }
        if ([string]::IsNullOrWhiteSpace([string]$configObject.jobsPullUrl)) {
            Set-JsonObjectValue -Object $configObject -Name "jobsPullUrl" -Value (Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/jobs/pull/")
        }
        if ([string]::IsNullOrWhiteSpace([string]$configObject.jobsResultUrl)) {
            Set-JsonObjectValue -Object $configObject -Name "jobsResultUrl" -Value (Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/jobs/result/")
        }
        if (-not $configObject.collectionEndpoints) {
            Set-JsonObjectValue -Object $configObject -Name "collectionEndpoints" -Value ([pscustomobject]@{})
        }
        $collectionApiPaths = @{
            systemInventoryUrl = "/api/agent/inventory/system/"
            networkInventoryUrl = "/api/agent/inventory/network/"
            hardwareInventoryUrl = "/api/agent/inventory/hardware/"
            diskInventoryUrl = "/api/agent/inventory/disks/"
            securityInventoryUrl = "/api/agent/inventory/security/"
            softwareInventoryUrl = "/api/agent/inventory/software/"
            fullInventoryUrl = "/api/agent/inventory/"
            patchStatusUrl = "/api/agent/inventory/patches/"
        }
        foreach ($endpointKey in $collectionApiPaths.Keys) {
            $currentValue = $null
            if ($configObject.collectionEndpoints.PSObject.Properties[$endpointKey]) {
                $currentValue = $configObject.collectionEndpoints.$endpointKey
            }
            if ([string]::IsNullOrWhiteSpace([string]$currentValue)) {
                Set-JsonObjectValue -Object $configObject.collectionEndpoints -Name $endpointKey -Value (Get-NightOwlApiUrl -Server $server -ApiPath $collectionApiPaths[$endpointKey])
            }
        }
        $configObject | ConvertTo-Json -Depth 8 | Set-Content -Path $ConfigPath -Encoding UTF8
        Write-ServiceInstallLog "Updated existing service config endpoints: $ConfigPath"
    }
    catch {
        Write-ServiceInstallLog "Could not repair service config endpoints: $($_.Exception.Message)" "WARN"
    }
}

function Save-ServiceConfig {
    $legacyConfig = Join-Path $LegacyInstallPath "RmmAgent.config.ps1"
    $server = $ServerUrl
    $token = $AgentToken
    if ([string]::IsNullOrWhiteSpace($server)) {
        $server = Read-LegacyConfigValue -Path $legacyConfig -VariableName "RmmServerUrl"
    }
    if ([string]::IsNullOrWhiteSpace($token)) {
        $token = Read-LegacyConfigValue -Path $legacyConfig -VariableName "AgentToken"
    }
    if ([string]::IsNullOrWhiteSpace($server)) {
        throw "ServerUrl is required or must exist in legacy config $legacyConfig."
    }
    if ([string]::IsNullOrWhiteSpace($token) -or $token -eq "COLE_SEU_TOKEN_AQUI") {
        throw "AgentToken is required or must exist in legacy config $legacyConfig."
    }

    if ((Test-Path $ConfigPath)) {
        Write-ServiceInstallLog "Preserving existing service config and repairing empty endpoints: $ConfigPath"
        Repair-ServiceConfigEndpoints -FallbackServer $server
        return
    }

    $version = "0.4.0-service"
    $versionPath = Join-Path $AgentPath "VERSION"
    if (Test-Path $versionPath) {
        $version = (Get-Content -Path $versionPath -Raw).Trim()
    }

    $baseUrl = Get-NightOwlBaseUrl -Server $server
    $heartbeatUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/heartbeat/"
    $config = [ordered]@{
        schemaVersion = 1
        serverUrl = $baseUrl
        heartbeatUrl = $heartbeatUrl
        jobsPullUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/jobs/pull/"
        jobsResultUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/jobs/result/"
        agentToken = $token
        installMode = "service"
        agentMode = "service"
        serviceName = $ServiceName
        installPath = $AgentPath
        logPath = $LogPath
        packagesPath = $PackagesPath
        cachePath = $CachePath
        agentVersion = $version
        intervals = [ordered]@{
            heartbeatSeconds = 300
            jobsSeconds = 45
            systemInventorySeconds = 21600
            networkInventorySeconds = 1800
            hardwareInventorySeconds = 21600
            diskSeconds = 900
            securitySeconds = 1800
            softwareSeconds = 3600
            fullInventorySeconds = 21600
            patchesSeconds = 43200
        }
        collectionEndpoints = [ordered]@{
            systemInventoryUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/inventory/system/"
            networkInventoryUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/inventory/network/"
            hardwareInventoryUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/inventory/hardware/"
            diskInventoryUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/inventory/disks/"
            securityInventoryUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/inventory/security/"
            softwareInventoryUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/inventory/software/"
            fullInventoryUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/inventory/"
            patchStatusUrl = Get-NightOwlApiUrl -Server $server -ApiPath "/api/agent/inventory/patches/"
        }
        jobs = [ordered]@{
            enabled = $true
            timeoutSeconds = 300
            maxStdoutChars = 6000
            maxStderrChars = 4000
            executedJobHistoryLimit = 200
            resultRetryLimit = 200
            allowedTypes = @(
                "force_inventory",
                "collect_disks",
                "collect_security",
                "collect_software",
                "ping",
                "collect_logs",
                "windows_update_scan"
            )
        }
        network = [ordered]@{
            timeoutSeconds = 30
            minBackoffSeconds = 30
            maxBackoffSeconds = 300
        }
    }
    $config | ConvertTo-Json -Depth 8 | Set-Content -Path $ConfigPath -Encoding UTF8
    Write-ServiceInstallLog "Created service config: $ConfigPath"
}

function Install-WithNssm {
    param([string]$Nssm)
    $serviceScript = Join-Path $AgentPath "RmmAgentService.ps1"
    $stdout = Join-Path $LogPath "service-stdout.log"
    $stderr = Join-Path $LogPath "service-stderr.log"
    $serviceArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$serviceScript`" -BasePath `"$BasePath`""

    Write-ServiceInstallLog "service.install.started: installing $ServiceName with NSSM $Nssm"
    Write-ServiceInstallLog "NSSM command target: powershell.exe $serviceArguments"

    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Write-ServiceInstallLog "Service already exists. Reconfiguring with NSSM: $ServiceName"
        & $Nssm stop $ServiceName | Out-Null
        & $Nssm remove $ServiceName confirm | Out-Null
    }

    & $Nssm install $ServiceName "powershell.exe" $serviceArguments
    if ($LASTEXITCODE -ne 0) { throw "NSSM failed to install service." }

    & $Nssm set $ServiceName DisplayName $DisplayName | Out-Null
    & $Nssm set $ServiceName Description "NightOwl RMM agent service loop." | Out-Null
    & $Nssm set $ServiceName AppDirectory $AgentPath | Out-Null
    & $Nssm set $ServiceName AppStdout $stdout | Out-Null
    & $Nssm set $ServiceName AppStderr $stderr | Out-Null
    & $Nssm set $ServiceName AppRotateFiles 1 | Out-Null
    & $Nssm set $ServiceName AppRotateOnline 1 | Out-Null
    & $Nssm set $ServiceName AppRotateBytes 10485760 | Out-Null
    & $Nssm set $ServiceName ObjectName LocalSystem | Out-Null
    & $Nssm set $ServiceName Start SERVICE_DELAYED_AUTO_START | Out-Null

    Start-Service -Name $ServiceName
    Write-ServiceInstallLog "service.install.completed: service installed and started with NSSM: $ServiceName"
}

try {
    Initialize-ServiceDirectories
    if (-not (Test-Path $SourcePath)) {
        throw "SourcePath not accessible: $SourcePath"
    }

    Copy-ServiceFile -FileName "RmmAgentService.ps1" -Required
    Copy-ServiceFile -FileName "Install-RmmAgentService.ps1"
    Copy-ServiceFile -FileName "Uninstall-RmmAgentService.ps1"
    Copy-ServiceFile -FileName "RmmAgent.config.json.example"
    Copy-ServiceFile -FileName "VERSION"
    Copy-ServiceFile -FileName "manifest.json"
    Copy-ServiceFile -FileName "README.md"

    Save-ServiceConfig

    $nssm = Resolve-NssmPath
    if ([string]::IsNullOrWhiteSpace($nssm)) {
        throw "NSSM was not found. Put nssm.exe in C:\ProgramData\NightOwl\Agent\nssm.exe, in the package root as nssm.exe, in the package tools\nssm.exe, in PATH, or pass -NssmPath. Service files/config were prepared, but the Windows service was not registered."
    }
    Write-ServiceInstallLog "Using NSSM: $nssm"
    $nssm = Copy-NssmToAgent -ResolvedNssmPath $nssm
    Write-ServiceInstallLog "Using NSSM local copy: $nssm"
    if (-not [string]::IsNullOrWhiteSpace($WinswPath)) {
        Write-ServiceInstallLog "WinSW path was provided, but this installer currently uses NSSM as the service wrapper." "WARN"
    }

    try {
        Install-WithNssm -Nssm $nssm
    }
    catch {
        Write-ServiceInstallLog "service.install.failed: $($_.Exception.Message)" "ERROR"
        throw
    }

    if ($RunOnce) {
        $serviceScript = Join-Path $AgentPath "RmmAgentService.ps1"
        $runOnceArgs = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $serviceScript,
            "-BasePath", $BasePath,
            "-RunOnce"
        )
        if ($DebugMode) {
            $runOnceArgs += "-DebugMode"
        }
        & powershell.exe @runOnceArgs
        Write-ServiceInstallLog "Service RunOnce completed with exit code $LASTEXITCODE"
    }

    if ($KeepScheduledTaskFallback) {
        Write-ServiceInstallLog "Scheduled task fallback was requested and will be preserved."
    }
    else {
        Write-ServiceInstallLog "Scheduled task fallback was not changed by this service installer."
    }

    Write-ServiceInstallLog "NightOwl service installation complete."
}
catch {
    Write-ServiceInstallLog "Service installation failed: $($_.Exception.Message)" "ERROR"
    exit 1
}
