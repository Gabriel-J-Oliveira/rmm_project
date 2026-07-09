[CmdletBinding()]
param(
    [string]$BasePath = "C:\ProgramData\NightOwl",
    [string]$ConfigPath = "",
    [string]$LegacyConfigPath = "C:\RMM\RmmAgent.config.ps1",
    [switch]$RunOnce,
    [switch]$RunJobsOnce,
    [switch]$DebugMode
)

$ErrorActionPreference = "Stop"

$AgentPath = Join-Path $BasePath "Agent"
$LogPath = Join-Path $BasePath "Logs"
$PackagesPath = Join-Path $BasePath "Packages"
$CachePath = Join-Path $BasePath "Cache"
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $AgentPath "RmmAgent.config.json"
}
$StatePath = Join-Path $AgentPath "agent.state.json"
$ServiceName = "NightOwlAgent"
$LogFile = Join-Path $LogPath "agent-service.jsonl"

function Initialize-AgentDirectories {
    foreach ($path in @($BasePath, $AgentPath, $LogPath, $PackagesPath, $CachePath)) {
        if (-not (Test-Path $path)) {
            New-Item -Path $path -ItemType Directory -Force | Out-Null
        }
    }
}

function ConvertTo-Hashtable {
    param($InputObject)

    if ($null -eq $InputObject) { return $null }
    if ($InputObject -is [System.Collections.IDictionary]) {
        $result = @{}
        foreach ($key in $InputObject.Keys) {
            $result[$key] = ConvertTo-Hashtable -InputObject $InputObject[$key]
        }
        return $result
    }
    if ($InputObject -is [System.Collections.IEnumerable] -and $InputObject -isnot [string]) {
        $items = @()
        foreach ($item in $InputObject) {
            $items += ConvertTo-Hashtable -InputObject $item
        }
        return $items
    }
    if ($InputObject.PSObject.Properties.Count -gt 0 -and $InputObject -isnot [string]) {
        $result = @{}
        foreach ($property in $InputObject.PSObject.Properties) {
            $result[$property.Name] = ConvertTo-Hashtable -InputObject $property.Value
        }
        return $result
    }
    return $InputObject
}

function Write-AgentLog {
    param(
        [string]$Level = "INFO",
        [string]$Event,
        [string]$Message,
        [hashtable]$Data = @{}
    )

    $entry = [ordered]@{
        timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        level = $Level
        service = $ServiceName
        event = $Event
        message = $Message
        data = $Data
    }
    $line = $entry | ConvertTo-Json -Depth 8 -Compress
    Add-Content -Path $LogFile -Value $line -Encoding UTF8

    if ($DebugMode -or $RunOnce -or $Level -in @("WARN", "ERROR")) {
        Write-Host "[$Level] $Event - $Message"
    }
}

function Get-AgentVersion {
    $versionPath = Join-Path $AgentPath "VERSION"
    if (Test-Path $versionPath) {
        return (Get-Content -Path $versionPath -Raw).Trim()
    }
    $legacyVersionPath = "C:\RMM\VERSION"
    if (Test-Path $legacyVersionPath) {
        return (Get-Content -Path $legacyVersionPath -Raw).Trim()
    }
    return "0.4.0-service"
}

function Get-DefaultConfig {
    return @{
        schemaVersion = 1
        serverUrl = ""
        heartbeatUrl = ""
        jobsPullUrl = ""
        jobsResultUrl = ""
        agentToken = ""
        installMode = "service"
        agentMode = "service"
        serviceName = $ServiceName
        installPath = $AgentPath
        logPath = $LogPath
        packagesPath = $PackagesPath
        cachePath = $CachePath
        agentVersion = Get-AgentVersion
        intervals = @{
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
        collectionEndpoints = @{
            systemInventoryUrl = ""
            networkInventoryUrl = ""
            hardwareInventoryUrl = ""
            diskInventoryUrl = ""
            securityInventoryUrl = ""
            softwareInventoryUrl = ""
            fullInventoryUrl = ""
            patchStatusUrl = ""
        }
        jobs = @{
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
        network = @{
            timeoutSeconds = 30
            minBackoffSeconds = 30
            maxBackoffSeconds = 300
        }
    }
}

function Read-LegacyConfig {
    if (-not (Test-Path $LegacyConfigPath)) {
        return @{}
    }

    $content = Get-Content -Path $LegacyConfigPath -Raw
    $legacy = @{}
    if ($content -match '\$RmmServerUrl\s*=\s*["''](?<value>[^"'']+)["'']') {
        $legacy["serverUrl"] = $Matches["value"]
        $legacy["heartbeatUrl"] = $Matches["value"]
    }
    if ($content -match '\$AgentToken\s*=\s*["''](?<value>[^"'']+)["'']') {
        $legacy["agentToken"] = $Matches["value"]
    }
    return $legacy
}

function Save-AgentConfig {
    param([hashtable]$Config)
    $safeConfig = $Config.Clone()
    $safeConfig | ConvertTo-Json -Depth 8 | Set-Content -Path $ConfigPath -Encoding UTF8
}

function Read-AgentConfig {
    $config = Get-DefaultConfig

    if (Test-Path $ConfigPath) {
        try {
            $jsonConfig = ConvertTo-Hashtable -InputObject (Get-Content -Path $ConfigPath -Raw | ConvertFrom-Json)
            foreach ($key in $jsonConfig.Keys) {
                $config[$key] = $jsonConfig[$key]
            }
        }
        catch {
            Write-AgentLog -Level "WARN" -Event "config.invalid" -Message "Could not parse JSON config, falling back to defaults." -Data @{ path = $ConfigPath; error = $_.Exception.Message }
        }
    }
    else {
        $legacy = Read-LegacyConfig
        foreach ($key in $legacy.Keys) {
            $config[$key] = $legacy[$key]
        }
        Save-AgentConfig -Config $config
        Write-AgentLog -Event "config.created" -Message "Created service JSON config." -Data @{ path = $ConfigPath; from_legacy = (Test-Path $LegacyConfigPath) }
    }

    if (-not $config.ContainsKey("intervals") -or $null -eq $config["intervals"]) {
        $config["intervals"] = (Get-DefaultConfig)["intervals"]
    }
    else {
        $defaultIntervals = (Get-DefaultConfig)["intervals"]
        foreach ($intervalKey in $defaultIntervals.Keys) {
            if (-not $config["intervals"].ContainsKey($intervalKey) -or $null -eq $config["intervals"][$intervalKey]) {
                $config["intervals"][$intervalKey] = $defaultIntervals[$intervalKey]
            }
        }
    }
    if (-not $config.ContainsKey("collectionEndpoints") -or $null -eq $config["collectionEndpoints"]) {
        $config["collectionEndpoints"] = (Get-DefaultConfig)["collectionEndpoints"]
    }
    else {
        $defaultEndpoints = (Get-DefaultConfig)["collectionEndpoints"]
        foreach ($endpointKey in $defaultEndpoints.Keys) {
            if (-not $config["collectionEndpoints"].ContainsKey($endpointKey)) {
                $config["collectionEndpoints"][$endpointKey] = $defaultEndpoints[$endpointKey]
            }
        }
    }
    if (-not $config.ContainsKey("jobs") -or $null -eq $config["jobs"]) {
        $config["jobs"] = (Get-DefaultConfig)["jobs"]
    }
    else {
        $defaultJobs = (Get-DefaultConfig)["jobs"]
        foreach ($jobKey in $defaultJobs.Keys) {
            if (-not $config["jobs"].ContainsKey($jobKey) -or $null -eq $config["jobs"][$jobKey]) {
                $config["jobs"][$jobKey] = $defaultJobs[$jobKey]
            }
        }
    }
    if (-not $config.ContainsKey("network") -or $null -eq $config["network"]) {
        $config["network"] = (Get-DefaultConfig)["network"]
    }
    else {
        $defaultNetwork = (Get-DefaultConfig)["network"]
        foreach ($networkKey in $defaultNetwork.Keys) {
            if (-not $config["network"].ContainsKey($networkKey) -or $null -eq $config["network"][$networkKey]) {
                $config["network"][$networkKey] = $defaultNetwork[$networkKey]
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace([string]$config["agentVersion"])) {
        $config["agentVersion"] = Get-AgentVersion
    }
    if ([string]::IsNullOrWhiteSpace([string]$config["heartbeatUrl"])) {
        $config["heartbeatUrl"] = Resolve-AgentApiUrl -Config $config -ConfiguredUrl "" -ApiPath "/api/agent/heartbeat/"
    }
    if ([string]::IsNullOrWhiteSpace([string]$config["jobsPullUrl"])) {
        $config["jobsPullUrl"] = Resolve-AgentApiUrl -Config $config -ConfiguredUrl "" -ApiPath "/api/agent/jobs/pull/"
    }
    if ([string]::IsNullOrWhiteSpace([string]$config["jobsResultUrl"])) {
        $config["jobsResultUrl"] = Resolve-AgentApiUrl -Config $config -ConfiguredUrl "" -ApiPath "/api/agent/jobs/result/"
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
        if ([string]::IsNullOrWhiteSpace([string]$config["collectionEndpoints"][$endpointKey])) {
            $config["collectionEndpoints"][$endpointKey] = Resolve-AgentApiUrl -Config $config -ConfiguredUrl "" -ApiPath $collectionApiPaths[$endpointKey]
        }
    }
    return $config
}

function Read-AgentState {
    if (-not (Test-Path $StatePath)) {
        return @{
            schemaVersion = 1
            cycles = @{}
            lastCollections = @{}
            executedJobs = @{}
            pendingJobResults = @()
            failureCount = 0
            backoffUntil = $null
            lastStatus = "never_run"
        }
    }

    try {
        $state = ConvertTo-Hashtable -InputObject (Get-Content -Path $StatePath -Raw | ConvertFrom-Json)
        if (-not $state.ContainsKey("cycles") -or $null -eq $state["cycles"]) {
            $state["cycles"] = @{}
        }
        if (-not $state.ContainsKey("failureCount")) {
            $state["failureCount"] = 0
        }
        if (-not $state.ContainsKey("lastCollections") -or $null -eq $state["lastCollections"]) {
            $state["lastCollections"] = @{}
        }
        if (-not $state.ContainsKey("executedJobs") -or $null -eq $state["executedJobs"]) {
            $state["executedJobs"] = @{}
        }
        if (-not $state.ContainsKey("pendingJobResults") -or $null -eq $state["pendingJobResults"]) {
            $state["pendingJobResults"] = @()
        }
        return $state
    }
    catch {
        Write-AgentLog -Level "WARN" -Event "state.invalid" -Message "Could not parse state file, creating a new state." -Data @{ path = $StatePath; error = $_.Exception.Message }
        return @{
            schemaVersion = 1
            cycles = @{}
            lastCollections = @{}
            executedJobs = @{}
            pendingJobResults = @()
            failureCount = 0
            backoffUntil = $null
            lastStatus = "state_reset"
        }
    }
}

function Save-AgentState {
    param([hashtable]$State)
    $State["updatedAt"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $State | ConvertTo-Json -Depth 10 | Set-Content -Path $StatePath -Encoding UTF8
}

function ConvertTo-DateTimeUtc {
    param($Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    try { return ([datetime]$Value).ToUniversalTime() } catch { return $null }
}

function Test-BackendBackoff {
    param([hashtable]$State)
    $backoffUntil = ConvertTo-DateTimeUtc -Value $State["backoffUntil"]
    if ($null -eq $backoffUntil) { return $false }
    return ((Get-Date).ToUniversalTime() -lt $backoffUntil)
}

function Test-CycleDue {
    param(
        [hashtable]$State,
        [string]$CycleName,
        [int]$IntervalSeconds
    )

    if ($RunOnce) { return $true }
    if (-not $State["cycles"].ContainsKey($CycleName)) { return $true }
    $cycle = $State["cycles"][$CycleName]
    $lastRunAt = ConvertTo-DateTimeUtc -Value $cycle["lastRunAt"]
    if ($null -eq $lastRunAt) { return $true }
    return ((Get-Date).ToUniversalTime() -ge $lastRunAt.AddSeconds($IntervalSeconds))
}

function Set-CycleResult {
    param(
        [hashtable]$State,
        [string]$CycleName,
        [string]$Status,
        [string]$ErrorMessage = "",
        [hashtable]$Metadata = @{}
    )

    $cycleState = @{
        lastRunAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        status = $Status
        error = $ErrorMessage
    }
    if ($Metadata.Count -gt 0) {
        $cycleState["metadata"] = $Metadata
    }
    $State["cycles"][$CycleName] = $cycleState
    if ($Status -eq "success") {
        if (-not $State.ContainsKey("lastCollections") -or $null -eq $State["lastCollections"]) {
            $State["lastCollections"] = @{}
        }
        $State["lastCollections"][$CycleName] = $cycleState["lastRunAt"]
    }
}

function Resolve-HeartbeatUrl {
    param([hashtable]$Config)
    $url = [string]$Config["heartbeatUrl"]
    if ([string]::IsNullOrWhiteSpace($url)) {
        $url = [string]$Config["serverUrl"]
    }
    if ([string]::IsNullOrWhiteSpace($url)) {
        return ""
    }
    if ($url -match "/api/agent/heartbeat/?$") {
        return $url
    }
    return ($url.TrimEnd("/") + "/api/agent/heartbeat/")
}

function Get-PrimaryIps {
    try {
        return @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -ne "127.0.0.1" } |
            Select-Object -ExpandProperty IPAddress -First 8)
    }
    catch {
        try {
            return @([System.Net.Dns]::GetHostAddresses($env:COMPUTERNAME) |
                Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } |
                ForEach-Object { $_.IPAddressToString })
        }
        catch {
            return @()
        }
    }
}

function Get-LightHeartbeatPayload {
    param([hashtable]$Config)

    $domain = ""
    try { $domain = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).Domain } catch { $domain = $env:USERDOMAIN }
    $osName = ""
    $osVersion = ""
    $osBuild = ""
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $osName = [string]$os.Caption
        $osVersion = [string]$os.Version
        $osBuild = [string]$os.BuildNumber
    }
    catch { }

    return @{
        schema_version = 1
        hostname = $env:COMPUTERNAME
        domain = $domain
        logged_user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        ips = @(Get-PrimaryIps)
        os = @{
            name = $osName
            version = $osVersion
            build = $osBuild
        }
        agent = @{
            version = [string]$Config["agentVersion"]
            mode = "service"
            install_mode = [string]$Config["installMode"]
            install_path = $AgentPath
            legacy_install_path = "C:\RMM"
            config_path = $ConfigPath
            log_path = $LogPath
            log_file = $LogFile
            service_name = $ServiceName
            service_status = "Running"
            service_start_type = "AutomaticDelayedStart"
            service_account = "LocalSystem"
            heartbeat_url = Resolve-HeartbeatUrl -Config $Config
            jobs_pull_url = [string]$Config["jobsPullUrl"]
            jobs_result_url = [string]$Config["jobsResultUrl"]
            collection_endpoints = $Config["collectionEndpoints"]
            task_name = $ServiceName
            runtime = "powershell-service"
            runtime_version = $PSVersionTable.PSVersion.ToString()
            last_status = "running"
        }
        heartbeat_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
}

function Get-UtcTimestamp {
    return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function New-CollectionResult {
    param([string]$Type)
    return @{
        schema_version = 1
        type = $Type
        status = "ok"
        collected_at = Get-UtcTimestamp
        hostname = $env:COMPUTERNAME
        errors = @()
    }
}

function Add-CollectionError {
    param(
        [hashtable]$Result,
        [string]$Area,
        [System.Management.Automation.ErrorRecord]$ErrorRecord
    )
    $message = $ErrorRecord.Exception.Message
    $Result["status"] = "partial"
    $Result["errors"] += @{
        area = $Area
        message = $message
    }
    Write-AgentLog -Level "WARN" -Event "collection.partial_failure" -Message "Inventory collection returned partial data." -Data @{
        type = $Result["type"]
        area = $Area
        error = $message
    }
}

function Convert-CimDateToUtcString {
    param($Value)
    if ($null -eq $Value) { return $null }
    try {
        if ($Value -is [datetime]) {
            return $Value.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        }
        return ([System.Management.ManagementDateTimeConverter]::ToDateTime([string]$Value)).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    catch {
        try {
            return ([datetime]$Value).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        }
        catch {
            return $null
        }
    }
}

function Convert-CimDateToDateTime {
    param($Value)
    if ($null -eq $Value) { return $null }
    try {
        if ($Value -is [datetime]) { return $Value }
        return [System.Management.ManagementDateTimeConverter]::ToDateTime([string]$Value)
    }
    catch {
        try { return [datetime]$Value } catch { return $null }
    }
}

function Get-NightOwlSystemInventory {
    $result = New-CollectionResult -Type "system"
    try {
        $computer = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
        $result["domain"] = [string]$computer.Domain
        $result["workgroup"] = if ($computer.PartOfDomain) { "" } else { [string]$computer.Workgroup }
        $result["manufacturer"] = [string]$computer.Manufacturer
        $result["model"] = [string]$computer.Model
        $result["fqdn"] = if ($computer.PartOfDomain -and $computer.Domain) { "$($env:COMPUTERNAME).$($computer.Domain)" } else { $env:COMPUTERNAME }
    }
    catch {
        Add-CollectionError -Result $result -Area "computer_system" -ErrorRecord $_
    }

    try {
        $bios = Get-CimInstance Win32_BIOS -ErrorAction Stop
        $result["serial_number"] = [string]$bios.SerialNumber
    }
    catch {
        Add-CollectionError -Result $result -Area "bios_serial" -ErrorRecord $_
    }

    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $lastBoot = Convert-CimDateToDateTime -Value $os.LastBootUpTime
        $uptime = if ($null -ne $lastBoot) { [int64]((Get-Date) - $lastBoot).TotalSeconds } else { $null }
        $result["os"] = @{
            name = [string]$os.Caption
            version = [string]$os.Version
            build = [string]$os.BuildNumber
            architecture = [string]$os.OSArchitecture
        }
        $result["last_boot_at"] = if ($null -ne $lastBoot) { $lastBoot.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") } else { $null }
        $result["uptime_seconds"] = $uptime
    }
    catch {
        Add-CollectionError -Result $result -Area "operating_system" -ErrorRecord $_
    }

    try {
        $result["timezone"] = (Get-TimeZone).Id
    }
    catch {
        Add-CollectionError -Result $result -Area "timezone" -ErrorRecord $_
    }

    try {
        $result["language"] = [System.Globalization.CultureInfo]::InstalledUICulture.Name
    }
    catch {
        Add-CollectionError -Result $result -Area "language" -ErrorRecord $_
    }

    try {
        $result["logged_user"] = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    }
    catch {
        $result["logged_user"] = ""
    }

    return $result
}

function Get-NightOwlNetworkInventory {
    $result = New-CollectionResult -Type "network"
    $adapters = @()
    try {
        $netConfigs = @(Get-NetIPConfiguration -ErrorAction Stop | Where-Object { $_.NetAdapter.Status -eq "Up" })
        foreach ($config in $netConfigs) {
            $adapter = $config.NetAdapter
            $ipv4 = @($config.IPv4Address | ForEach-Object { $_.IPAddress })
            $ipv6 = @($config.IPv6Address | ForEach-Object { $_.IPAddress })
            $gateways = @($config.IPv4DefaultGateway | ForEach-Object { $_.NextHop })
            $dnsServers = @()
            try {
                $dnsServers = @((Get-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction Stop).ServerAddresses)
            }
            catch { }

            $adapters += @{
                name = [string]$adapter.Name
                interface_description = [string]$adapter.InterfaceDescription
                interface_index = [int]$adapter.ifIndex
                interface_type = [string]$adapter.NdisPhysicalMedium
                mac_address = [string]$adapter.MacAddress
                status = [string]$adapter.Status
                link_speed = [string]$adapter.LinkSpeed
                ipv4 = $ipv4
                ipv6 = $ipv6
                gateway = $gateways
                dns = $dnsServers
            }
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "net_ip_configuration" -ErrorRecord $_
    }

    $activeIps = @()
    foreach ($adapterItem in $adapters) {
        $activeIps += @($adapterItem.ipv4 | Where-Object { $_ -and $_ -notlike "169.254.*" -and $_ -ne "127.0.0.1" })
    }
    $result["adapters"] = $adapters
    $result["ips"] = $activeIps
    $result["primary_ip"] = @($activeIps | Select-Object -First 1)[0]
    $result["mac_addresses"] = @($adapters | ForEach-Object { $_.mac_address } | Where-Object { $_ })
    $result["gateways"] = @($adapters | ForEach-Object { $_.gateway } | Where-Object { $_ } | Select-Object -Unique)
    $result["dns_servers"] = @($adapters | ForEach-Object { $_.dns } | Where-Object { $_ } | Select-Object -Unique)
    return $result
}

function Get-NightOwlDiskInventory {
    $result = New-CollectionResult -Type "disk"
    $disks = @()
    try {
        $logicalDisks = @(Get-CimInstance Win32_LogicalDisk -ErrorAction Stop | Where-Object { $_.DriveType -in @(2, 3, 4) })
        foreach ($disk in $logicalDisks) {
            $size = [int64]($disk.Size)
            $free = [int64]($disk.FreeSpace)
            $usedPercent = $null
            if ($size -gt 0) {
                $usedPercent = [math]::Round((($size - $free) / $size) * 100, 2)
            }
            $disks += @{
                letter = [string]$disk.DeviceID
                filesystem = [string]$disk.FileSystem
                total_bytes = $size
                free_bytes = $free
                used_percent = $usedPercent
                drive_type = [int]$disk.DriveType
                volume_name = [string]$disk.VolumeName
                collected_at = $result["collected_at"]
            }
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "logical_disk" -ErrorRecord $_
    }
    $result["disks"] = $disks
    return $result
}

function Get-NightOwlHardwareInventory {
    $result = New-CollectionResult -Type "hardware"
    try {
        $cpu = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1
        $result["cpu"] = @{
            name = [string]$cpu.Name
            manufacturer = [string]$cpu.Manufacturer
            cores = [int]$cpu.NumberOfCores
            logical_processors = [int]$cpu.NumberOfLogicalProcessors
            max_clock_mhz = [int]$cpu.MaxClockSpeed
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "cpu" -ErrorRecord $_
    }

    try {
        $computer = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
        $result["memory_total_bytes"] = [int64]$computer.TotalPhysicalMemory
    }
    catch {
        Add-CollectionError -Result $result -Area "memory" -ErrorRecord $_
    }

    try {
        $bios = Get-CimInstance Win32_BIOS -ErrorAction Stop
        $result["bios"] = @{
            manufacturer = [string]$bios.Manufacturer
            version = [string]$bios.SMBIOSBIOSVersion
            serial_number = [string]$bios.SerialNumber
            release_date = Convert-CimDateToUtcString -Value $bios.ReleaseDate
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "bios" -ErrorRecord $_
    }

    try {
        if (Get-Command Get-Tpm -ErrorAction SilentlyContinue) {
            $tpm = Get-Tpm
            $result["tpm"] = @{
                present = [bool]$tpm.TpmPresent
                ready = [bool]$tpm.TpmReady
                enabled = [bool]$tpm.TpmEnabled
                activated = [bool]$tpm.TpmActivated
            }
        }
        else {
            $result["tpm"] = @{ present = $null; ready = $null; source = "Get-Tpm unavailable" }
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "tpm" -ErrorRecord $_
    }

    try {
        $battery = @(Get-CimInstance Win32_Battery -ErrorAction Stop)
        $result["battery"] = @($battery | ForEach-Object {
            @{
                name = [string]$_.Name
                status = [string]$_.Status
                estimated_charge_remaining = $_.EstimatedChargeRemaining
                estimated_run_time = $_.EstimatedRunTime
            }
        })
    }
    catch {
        $result["battery"] = @()
    }
    return $result
}

function Get-NightOwlSoftwareInventory {
    $result = New-CollectionResult -Type "software"
    $software = @()
    $registryRoots = @(
        @{ path = "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall"; architecture = "x64" },
        @{ path = "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"; architecture = "x86" }
    )

    foreach ($root in $registryRoots) {
        try {
            if (-not (Test-Path $root.path)) { continue }
            $keys = @(Get-ChildItem -Path $root.path -ErrorAction Stop)
            foreach ($key in $keys) {
                try {
                    $item = Get-ItemProperty -Path $key.PSPath -ErrorAction Stop
                    if ([string]::IsNullOrWhiteSpace([string]$item.DisplayName)) { continue }
                    $software += @{
                        name = [string]$item.DisplayName
                        version = [string]$item.DisplayVersion
                        publisher = [string]$item.Publisher
                        install_date = [string]$item.InstallDate
                        uninstall_string = [string]$item.UninstallString
                        registry_key = [string]$key.Name
                        architecture = [string]$root.architecture
                        detected_at = $result["collected_at"]
                    }
                }
                catch {
                    Add-CollectionError -Result $result -Area "software_registry_item" -ErrorRecord $_
                }
            }
        }
        catch {
            Add-CollectionError -Result $result -Area "software_registry_root" -ErrorRecord $_
        }
    }

    $deduped = @{}
    foreach ($app in $software) {
        $key = ("{0}|{1}|{2}|{3}" -f $app.name, $app.version, $app.publisher, $app.architecture).ToLowerInvariant()
        if (-not $deduped.ContainsKey($key)) {
            $deduped[$key] = $app
        }
    }
    $result["installed_software"] = @($deduped.Values)
    $result["count"] = @($result["installed_software"]).Count
    return $result
}

function Get-NightOwlSecurityInventory {
    param([hashtable]$SoftwareInventory = $null)

    $result = New-CollectionResult -Type "security"
    $signals = @()

    try {
        if (Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue) {
            $mp = Get-MpComputerStatus
            $result["defender"] = @{
                available = $true
                antivirus_enabled = [bool]$mp.AntivirusEnabled
                antispyware_enabled = [bool]$mp.AntispywareEnabled
                real_time_protection_enabled = [bool]$mp.RealTimeProtectionEnabled
                signatures_age_days = $mp.AntivirusSignatureAge
                engine_version = [string]$mp.AMEngineVersion
                product_version = [string]$mp.AMProductVersion
            }
            if (-not $mp.AntivirusEnabled -or -not $mp.RealTimeProtectionEnabled) { $signals += "defender_disabled" }
            if ($mp.AntivirusSignatureAge -gt 7) { $signals += "defender_signature_old" }
        }
        else {
            $result["defender"] = @{ available = $false; status = "not_available" }
            $signals += "defender_unknown"
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "defender" -ErrorRecord $_
        $signals += "defender_unknown"
    }

    try {
        $avProducts = @(Get-CimInstance -Namespace "root\SecurityCenter2" -ClassName AntiVirusProduct -ErrorAction Stop)
        $result["antivirus_products"] = @($avProducts | ForEach-Object {
            @{
                name = [string]$_.displayName
                instance_guid = [string]$_.instanceGuid
                path = [string]$_.pathToSignedProductExe
                state = [string]$_.productState
            }
        })
        if (@($avProducts).Count -eq 0) { $signals += "av_not_detected" }
    }
    catch {
        Add-CollectionError -Result $result -Area "security_center_av" -ErrorRecord $_
        $result["antivirus_products"] = @()
    }

    try {
        if (Get-Command Get-NetFirewallProfile -ErrorAction SilentlyContinue) {
            $profiles = @(Get-NetFirewallProfile)
            $result["firewall"] = @($profiles | ForEach-Object {
                @{
                    name = [string]$_.Name
                    enabled = [bool]$_.Enabled
                    default_inbound_action = [string]$_.DefaultInboundAction
                    default_outbound_action = [string]$_.DefaultOutboundAction
                }
            })
            if (@($profiles | Where-Object { -not $_.Enabled }).Count -gt 0) { $signals += "firewall_disabled" }
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "firewall" -ErrorRecord $_
    }

    try {
        if (Get-Command Get-BitLockerVolume -ErrorAction SilentlyContinue) {
            $volumes = @(Get-BitLockerVolume)
            $result["bitlocker"] = @($volumes | ForEach-Object {
                @{
                    mount_point = [string]$_.MountPoint
                    volume_status = [string]$_.VolumeStatus
                    protection_status = [string]$_.ProtectionStatus
                    encryption_percentage = $_.EncryptionPercentage
                }
            })
        }
        else {
            $result["bitlocker"] = @{ available = $false; status = "not_available" }
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "bitlocker" -ErrorRecord $_
    }

    try {
        $remoteToolPatterns = @("AnyDesk", "TeamViewer", "Supremo", "RustDesk", "UltraVNC", "TightVNC", "RealVNC", "Chrome Remote Desktop", "ScreenConnect", "ConnectWise")
        if ($null -eq $SoftwareInventory) {
            $SoftwareInventory = Get-NightOwlSoftwareInventory
        }
        $installed = @($SoftwareInventory["installed_software"])
        $remoteTools = @()
        foreach ($app in $installed) {
            foreach ($pattern in $remoteToolPatterns) {
                if ($app.name -like "*$pattern*") {
                    $remoteTools += $app
                    break
                }
            }
        }
        $result["remote_access_tools"] = $remoteTools
        if (@($remoteTools).Count -gt 0) { $signals += "remote_access_detected" }
    }
    catch {
        Add-CollectionError -Result $result -Area "remote_access_tools" -ErrorRecord $_
    }

    try {
        if (Get-Command Get-LocalGroupMember -ErrorAction SilentlyContinue) {
            $adminGroupNames = @("Administrators", "Administradores")
            $admins = @()
            foreach ($adminGroupName in $adminGroupNames) {
                try {
                    $admins = @(Get-LocalGroupMember -Group $adminGroupName -ErrorAction Stop)
                    if ($admins.Count -gt 0) { break }
                }
                catch { }
            }
            $result["local_administrators"] = @($admins | ForEach-Object {
                @{
                    name = [string]$_.Name
                    object_class = [string]$_.ObjectClass
                    principal_source = [string]$_.PrincipalSource
                }
            })
        }
        else {
            $result["local_administrators"] = @()
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "local_administrators" -ErrorRecord $_
    }

    $result["signals"] = @($signals | Select-Object -Unique)
    if ($signals -contains "defender_disabled" -or $signals -contains "av_not_detected") {
        $result["overall_status"] = "critical"
    }
    elseif ($signals.Count -gt 0 -or $result["status"] -eq "partial") {
        $result["overall_status"] = "warning"
    }
    else {
        $result["overall_status"] = "ok"
    }
    return $result
}

function Test-PendingReboot {
    $paths = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
        "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager"
    )
    foreach ($path in $paths) {
        try {
            if ($path -like "*Session Manager") {
                $value = (Get-ItemProperty -Path $path -Name PendingFileRenameOperations -ErrorAction SilentlyContinue).PendingFileRenameOperations
                if ($value) { return $true }
            }
            elseif (Test-Path $path) {
                return $true
            }
        }
        catch { }
    }
    return $false
}

function Get-NightOwlPatchStatus {
    $result = New-CollectionResult -Type "patches"
    try {
        $result["reboot_pending"] = Test-PendingReboot
    }
    catch {
        Add-CollectionError -Result $result -Area "pending_reboot" -ErrorRecord $_
    }

    try {
        $detectPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\Results\Detect"
        if (Test-Path $detectPath) {
            $autoUpdate = Get-ItemProperty -Path $detectPath -ErrorAction Stop
            $result["last_windows_update_check"] = [string]$autoUpdate.LastSuccessTime
        }
        else {
            $result["last_windows_update_check"] = $null
        }
    }
    catch {
        Add-CollectionError -Result $result -Area "windows_update_last_check" -ErrorRecord $_
    }

    try {
        $session = New-Object -ComObject Microsoft.Update.Session
        $searcher = $session.CreateUpdateSearcher()
        $searchResult = $searcher.Search("IsInstalled=0 and Type='Software'")
        $result["pending_updates_count"] = [int]$searchResult.Updates.Count
        $updates = @()
        for ($i = 0; $i -lt [Math]::Min($searchResult.Updates.Count, 20); $i++) {
            $update = $searchResult.Updates.Item($i)
            $updates += @{
                title = [string]$update.Title
                is_downloaded = [bool]$update.IsDownloaded
                reboot_required = [bool]$update.RebootRequired
            }
        }
        $result["pending_updates_sample"] = $updates
    }
    catch {
        Add-CollectionError -Result $result -Area "windows_update_pending" -ErrorRecord $_
        if (-not $result.ContainsKey("pending_updates_count")) {
            $result["pending_updates_count"] = $null
        }
    }
    return $result
}

function Get-NightOwlFullInventory {
    $software = Get-NightOwlSoftwareInventory
    $payload = @{
        schema_version = 1
        type = "full_inventory"
        status = "ok"
        collected_at = Get-UtcTimestamp
        hostname = $env:COMPUTERNAME
        system = Get-NightOwlSystemInventory
        network = Get-NightOwlNetworkInventory
        hardware = Get-NightOwlHardwareInventory
        disk = Get-NightOwlDiskInventory
        software = $software
        security = Get-NightOwlSecurityInventory -SoftwareInventory $software
        patches = Get-NightOwlPatchStatus
    }
    foreach ($section in @("system", "network", "hardware", "disk", "software", "security", "patches")) {
        if ($payload[$section] -and $payload[$section]["status"] -eq "partial") {
            $payload["status"] = "partial"
            break
        }
    }
    return $payload
}

function Resolve-CollectionUrl {
    param(
        [hashtable]$Config,
        [string]$EndpointKey
    )
    if (-not $Config.ContainsKey("collectionEndpoints") -or $null -eq $Config["collectionEndpoints"]) { return "" }
    if (-not $Config["collectionEndpoints"].ContainsKey($EndpointKey)) { return "" }
    return [string]$Config["collectionEndpoints"][$EndpointKey]
}

function Invoke-CollectionUpload {
    param(
        [hashtable]$Config,
        [string]$EndpointKey,
        [hashtable]$Payload
    )
    $url = Resolve-CollectionUrl -Config $Config -EndpointKey $EndpointKey
    if ([string]::IsNullOrWhiteSpace($url)) {
        Write-AgentLog -Event "collection.ready" -Message "Collection completed locally; no upload endpoint configured." -Data @{
            type = [string]$Payload["type"]
            endpoint_key = $EndpointKey
            status = [string]$Payload["status"]
        }
        return @{ uploaded = $false; reason = "endpoint_not_configured" }
    }

    $token = [string]$Config["agentToken"]
    if ([string]::IsNullOrWhiteSpace($token) -or $token -eq "COLE_SEU_TOKEN_AQUI") {
        throw "Agent token is not configured."
    }
    $timeout = [int]$Config["network"]["timeoutSeconds"]
    if ($timeout -lt 5) { $timeout = 30 }

    $headers = @{ Authorization = "Bearer $token" }
    $response = Invoke-RestMethod -Method Post -Uri $url -Headers $headers -Body ($Payload | ConvertTo-Json -Depth 20) -ContentType "application/json" -TimeoutSec $timeout
    Write-AgentLog -Event "collection.uploaded" -Message "Collection uploaded successfully." -Data @{
        type = [string]$Payload["type"]
        endpoint_key = $EndpointKey
        url = $url
    }
    return @{ uploaded = $true; response = $response }
}

function Invoke-CollectionCycle {
    param(
        [hashtable]$Config,
        [hashtable]$State,
        [string]$CycleName,
        [string]$EndpointKey,
        [scriptblock]$Collector
    )

    $payload = & $Collector
    $upload = Invoke-CollectionUpload -Config $Config -EndpointKey $EndpointKey -Payload $payload
    $metadata = @{
        type = [string]$payload["type"]
        collection_status = [string]$payload["status"]
        collected_at = [string]$payload["collected_at"]
        uploaded = [bool]$upload.uploaded
    }
    if ($payload.ContainsKey("count")) {
        $metadata["count"] = $payload["count"]
    }
    Set-CycleResult -State $State -CycleName $CycleName -Status "success" -Metadata $metadata
}

function Resolve-AgentApiUrl {
    param(
        [hashtable]$Config,
        [string]$ConfiguredUrl,
        [string]$ApiPath
    )

    if (-not [string]::IsNullOrWhiteSpace($ConfiguredUrl)) {
        return $ConfiguredUrl
    }

    $base = [string]$Config["serverUrl"]
    if ([string]::IsNullOrWhiteSpace($base)) {
        $base = [string]$Config["heartbeatUrl"]
    }
    if ([string]::IsNullOrWhiteSpace($base)) {
        return ""
    }

    $trimmed = $base.TrimEnd("/")
    if ($trimmed -match "/api/agent/heartbeat$") {
        return ($trimmed -replace "/api/agent/heartbeat$", $ApiPath)
    }
    if ($trimmed -match "/api/agent/.+$") {
        return ($trimmed -replace "/api/agent/.+$", $ApiPath)
    }
    return ($trimmed + $ApiPath)
}

function Limit-NightOwlText {
    param(
        [string]$Text,
        [int]$MaxChars
    )
    if ($null -eq $Text) { return "" }
    if ($MaxChars -lt 100) { $MaxChars = 100 }
    if ($Text.Length -le $MaxChars) { return $Text }
    return ($Text.Substring(0, $MaxChars) + "`n...[truncated]")
}

function Get-NightOwlJobValue {
    param(
        [hashtable]$Job,
        [string[]]$Keys,
        $Default = $null
    )
    foreach ($key in $Keys) {
        if ($Job.ContainsKey($key) -and $null -ne $Job[$key] -and -not [string]::IsNullOrWhiteSpace([string]$Job[$key])) {
            return $Job[$key]
        }
    }
    return $Default
}

function Get-NightOwlJobId {
    param([hashtable]$Job)
    return [string](Get-NightOwlJobValue -Job $Job -Keys @("id", "job_id", "uuid", "pk") -Default "")
}

function Get-NightOwlJobType {
    param([hashtable]$Job)
    return [string](Get-NightOwlJobValue -Job $Job -Keys @("type", "job_type", "command", "action") -Default "")
}

function Get-NightOwlJobPayload {
    param([hashtable]$Job)
    foreach ($key in @("payload", "params", "parameters", "data")) {
        if ($Job.ContainsKey($key) -and $null -ne $Job[$key]) {
            if ($Job[$key] -is [hashtable]) { return $Job[$key] }
            return ConvertTo-Hashtable -InputObject $Job[$key]
        }
    }
    return @{}
}

function Test-NightOwlJobExpired {
    param([hashtable]$Job)
    $expiresAt = Get-NightOwlJobValue -Job $Job -Keys @("expires_at", "expiresAt", "deadline_at") -Default $null
    $expiresAtUtc = ConvertTo-DateTimeUtc -Value $expiresAt
    if ($null -eq $expiresAtUtc) { return $false }
    return ((Get-Date).ToUniversalTime() -gt $expiresAtUtc)
}

function Test-NightOwlJobAlreadyExecuted {
    param(
        [hashtable]$State,
        [string]$JobId
    )
    if ([string]::IsNullOrWhiteSpace($JobId)) { return $false }
    if (-not $State.ContainsKey("executedJobs") -or $null -eq $State["executedJobs"]) {
        $State["executedJobs"] = @{}
    }
    return $State["executedJobs"].ContainsKey($JobId)
}

function Add-NightOwlExecutedJob {
    param(
        [hashtable]$Config,
        [hashtable]$State,
        [string]$JobId,
        [string]$Status
    )
    if ([string]::IsNullOrWhiteSpace($JobId)) { return }
    if (-not $State.ContainsKey("executedJobs") -or $null -eq $State["executedJobs"]) {
        $State["executedJobs"] = @{}
    }
    $State["executedJobs"][$JobId] = @{
        status = $Status
        finishedAt = Get-UtcTimestamp
    }

    $limit = [int]$Config["jobs"]["executedJobHistoryLimit"]
    if ($limit -lt 20) { $limit = 200 }
    if ($State["executedJobs"].Count -gt $limit) {
        $ordered = @($State["executedJobs"].GetEnumerator() | Sort-Object { $_.Value.finishedAt } -Descending | Select-Object -First $limit)
        $trimmed = @{}
        foreach ($item in $ordered) { $trimmed[$item.Key] = $item.Value }
        $State["executedJobs"] = $trimmed
    }
}

function Add-NightOwlPendingJobResult {
    param(
        [hashtable]$Config,
        [hashtable]$State,
        [hashtable]$Result
    )
    $pending = @($State["pendingJobResults"])
    $jobId = [string]$Result["job_id"]
    $pending = @($pending | Where-Object { [string]($_["job_id"]) -ne $jobId })
    $pending += $Result
    $limit = [int]$Config["jobs"]["resultRetryLimit"]
    if ($limit -lt 20) { $limit = 200 }
    if ($pending.Count -gt $limit) {
        $pending = @($pending | Select-Object -Last $limit)
    }
    $State["pendingJobResults"] = $pending
}

function Invoke-NightOwlJobPull {
    param([hashtable]$Config)

    $url = Resolve-AgentApiUrl -Config $Config -ConfiguredUrl ([string]$Config["jobsPullUrl"]) -ApiPath "/api/agent/jobs/pull/"
    $token = [string]$Config["agentToken"]
    if ([string]::IsNullOrWhiteSpace($url)) {
        throw "Jobs pull URL is not configured."
    }
    if ([string]::IsNullOrWhiteSpace($token) -or $token -eq "COLE_SEU_TOKEN_AQUI") {
        throw "Agent token is not configured."
    }

    $timeout = [int]$Config["network"]["timeoutSeconds"]
    if ($timeout -lt 5) { $timeout = 30 }
    $headers = @{ Authorization = "Bearer $token" }
    $response = Invoke-RestMethod -Method Get -Uri $url -Headers $headers -TimeoutSec $timeout
    $normalized = ConvertTo-Hashtable -InputObject $response

    if ($normalized -is [array]) {
        return @($normalized)
    }
    if ($normalized -is [hashtable] -and $normalized.ContainsKey("jobs")) {
        return @($normalized["jobs"])
    }
    if ($normalized -is [hashtable] -and $normalized.ContainsKey("results")) {
        return @($normalized["results"])
    }
    if ($normalized -is [hashtable] -and $normalized.ContainsKey("id")) {
        return @($normalized)
    }
    return @()
}

function Send-NightOwlJobResult {
    param(
        [hashtable]$Config,
        [hashtable]$Result
    )

    $url = Resolve-AgentApiUrl -Config $Config -ConfiguredUrl ([string]$Config["jobsResultUrl"]) -ApiPath "/api/agent/jobs/result/"
    $token = [string]$Config["agentToken"]
    if ([string]::IsNullOrWhiteSpace($url)) {
        throw "Jobs result URL is not configured."
    }
    if ([string]::IsNullOrWhiteSpace($token) -or $token -eq "COLE_SEU_TOKEN_AQUI") {
        throw "Agent token is not configured."
    }

    $timeout = [int]$Config["network"]["timeoutSeconds"]
    if ($timeout -lt 5) { $timeout = 30 }
    $headers = @{ Authorization = "Bearer $token" }
    $response = Invoke-RestMethod -Method Post -Uri $url -Headers $headers -Body ($Result | ConvertTo-Json -Depth 20) -ContentType "application/json" -TimeoutSec $timeout
    Write-AgentLog -Event "job.result_sent" -Message "Job result sent to backend." -Data @{
        job_id = [string]$Result["job_id"]
        status = [string]$Result["status"]
    }
    return $response
}

function Retry-NightOwlPendingJobResults {
    param(
        [hashtable]$Config,
        [hashtable]$State
    )
    $pending = @($State["pendingJobResults"])
    if ($pending.Count -eq 0) { return }

    $remaining = @()
    foreach ($result in $pending) {
        try {
            Send-NightOwlJobResult -Config $Config -Result (ConvertTo-Hashtable -InputObject $result) | Out-Null
        }
        catch {
            $remaining += $result
            Write-AgentLog -Level "WARN" -Event "job.result_send_failed" -Message "Could not retry pending job result." -Data @{
                job_id = [string]$result["job_id"]
                error = $_.Exception.Message
            }
        }
    }
    $State["pendingJobResults"] = $remaining
}

function New-NightOwlJobResult {
    param(
        [hashtable]$Job,
        [string]$Status,
        [datetime]$StartedAt,
        [datetime]$FinishedAt,
        [int]$ExitCode,
        [string]$Stdout = "",
        [string]$Stderr = "",
        [hashtable]$Result = @{},
        [string]$ErrorMessage = ""
    )
    $jobId = Get-NightOwlJobId -Job $Job
    $jobType = Get-NightOwlJobType -Job $Job
    $duration = [math]::Round(($FinishedAt - $StartedAt).TotalSeconds, 3)
    return @{
        schema_version = 1
        job_id = $jobId
        job_type = $jobType
        status = $Status
        started_at = $StartedAt.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        finished_at = $FinishedAt.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        duration_seconds = $duration
        exit_code = $ExitCode
        stdout = $Stdout
        stderr = $Stderr
        result = $Result
        error_message = $ErrorMessage
        agent = @{
            hostname = $env:COMPUTERNAME
            service_name = $ServiceName
            version = Get-AgentVersion
        }
    }
}

function Invoke-NightOwlAllowedJob {
    param(
        [hashtable]$Config,
        [hashtable]$Job
    )

    $jobType = Get-NightOwlJobType -Job $Job
    $payload = Get-NightOwlJobPayload -Job $Job
    $stdout = ""
    $stderr = ""
    $result = @{}

    switch ($jobType) {
        "force_inventory" {
            $result = Get-NightOwlFullInventory
            $stdout = "Full inventory collected locally."
        }
        "collect_disks" {
            $result = Get-NightOwlDiskInventory
            $stdout = "Disk inventory collected locally."
        }
        "collect_security" {
            $result = Get-NightOwlSecurityInventory
            $stdout = "Security inventory collected locally."
        }
        "collect_software" {
            $result = Get-NightOwlSoftwareInventory
            $stdout = "Software inventory collected locally."
        }
        "windows_update_scan" {
            $result = Get-NightOwlPatchStatus
            $stdout = "Windows Update read-only scan collected locally."
        }
        "ping" {
            $target = [string](Get-NightOwlJobValue -Job $payload -Keys @("target", "host", "hostname", "ip") -Default "")
            if ([string]::IsNullOrWhiteSpace($target)) { $target = "127.0.0.1" }
            $count = 2
            if ($payload.ContainsKey("count")) {
                try { $count = [Math]::Min([Math]::Max([int]$payload["count"], 1), 5) } catch { $count = 2 }
            }
            $pingOutput = @(Test-Connection -ComputerName $target -Count $count -ErrorAction Stop)
            $result = @{
                target = $target
                count = $pingOutput.Count
                average_ms = if ($pingOutput.Count -gt 0) { [math]::Round(($pingOutput | Measure-Object ResponseTime -Average).Average, 2) } else { $null }
                replies = @($pingOutput | ForEach-Object {
                    $responseTime = $null
                    if ($_.PSObject.Properties["ResponseTime"]) { $responseTime = $_.ResponseTime }
                    elseif ($_.PSObject.Properties["Latency"]) { $responseTime = $_.Latency }
                    @{
                        address = [string]$_.Address
                        response_time_ms = $responseTime
                        status = [string]$_.StatusCode
                    }
                })
            }
            $stdout = "Ping completed for $target."
        }
        "collect_logs" {
            $lines = 80
            if ($payload.ContainsKey("lines")) {
                try { $lines = [Math]::Min([Math]::Max([int]$payload["lines"], 10), 300) } catch { $lines = 80 }
            }
            $logTail = @()
            if (Test-Path $LogFile) {
                $logTail = @(Get-Content -Path $LogFile -Tail $lines -ErrorAction Stop)
            }
            $result = @{
                log_file = $LogFile
                lines = $logTail
                line_count = $logTail.Count
            }
            $stdout = "Collected last $($logTail.Count) service log lines."
        }
        default {
            throw "Job type is not implemented by this agent: $jobType"
        }
    }

    $maxStdout = [int]$Config["jobs"]["maxStdoutChars"]
    $maxStderr = [int]$Config["jobs"]["maxStderrChars"]
    return @{
        stdout = Limit-NightOwlText -Text $stdout -MaxChars $maxStdout
        stderr = Limit-NightOwlText -Text $stderr -MaxChars $maxStderr
        result = $result
    }
}

function Invoke-NightOwlJob {
    param(
        [hashtable]$Config,
        [hashtable]$State,
        [hashtable]$Job
    )

    $jobId = Get-NightOwlJobId -Job $Job
    $jobType = Get-NightOwlJobType -Job $Job
    $startedAt = (Get-Date).ToUniversalTime()

    if ([string]::IsNullOrWhiteSpace($jobId)) {
        throw "Received job without id."
    }

    Write-AgentLog -Event "job.started" -Message "Job execution started." -Data @{ job_id = $jobId; job_type = $jobType }

    if (Test-NightOwlJobExpired -Job $Job) {
        $finishedAt = (Get-Date).ToUniversalTime()
        return New-NightOwlJobResult -Job $Job -Status "expired" -StartedAt $startedAt -FinishedAt $finishedAt -ExitCode 3 -Stdout "" -Stderr "Job expired before execution." -Result @{} -ErrorMessage "Job expired before execution."
    }

    $allowedTypes = @($Config["jobs"]["allowedTypes"])
    if ($allowedTypes -notcontains $jobType) {
        $finishedAt = (Get-Date).ToUniversalTime()
        return New-NightOwlJobResult -Job $Job -Status "failed" -StartedAt $startedAt -FinishedAt $finishedAt -ExitCode 2 -Stdout "" -Stderr "Job type is not allowed." -Result @{} -ErrorMessage "Job type is not allowed by agent config."
    }

    try {
        $execution = Invoke-NightOwlAllowedJob -Config $Config -Job $Job
        $finishedAt = (Get-Date).ToUniversalTime()
        $duration = ($finishedAt - $startedAt).TotalSeconds
        $timeout = [int]$Config["jobs"]["timeoutSeconds"]
        if ($timeout -gt 0 -and $duration -gt $timeout) {
            return New-NightOwlJobResult -Job $Job -Status "failed" -StartedAt $startedAt -FinishedAt $finishedAt -ExitCode 124 -Stdout $execution.stdout -Stderr "Job exceeded timeout after completion." -Result $execution.result -ErrorMessage "Job exceeded configured timeout."
        }
        return New-NightOwlJobResult -Job $Job -Status "completed" -StartedAt $startedAt -FinishedAt $finishedAt -ExitCode 0 -Stdout $execution.stdout -Stderr $execution.stderr -Result $execution.result
    }
    catch {
        $finishedAt = (Get-Date).ToUniversalTime()
        $stderr = Limit-NightOwlText -Text $_.Exception.Message -MaxChars ([int]$Config["jobs"]["maxStderrChars"])
        return New-NightOwlJobResult -Job $Job -Status "failed" -StartedAt $startedAt -FinishedAt $finishedAt -ExitCode 1 -Stdout "" -Stderr $stderr -Result @{} -ErrorMessage $_.Exception.Message
    }
}

function Invoke-NightOwlJobCycle {
    param(
        [hashtable]$Config,
        [hashtable]$State
    )

    if (-not [bool]$Config["jobs"]["enabled"]) {
        Write-AgentLog -Event "job.disabled" -Message "Job pull is disabled by config."
        return @{ pulled = 0; executed = 0; skipped = 0; failed = 0 }
    }

    Retry-NightOwlPendingJobResults -Config $Config -State $State

    $jobs = @(Invoke-NightOwlJobPull -Config $Config)
    $summary = @{ pulled = $jobs.Count; executed = 0; skipped = 0; failed = 0 }
    foreach ($rawJob in $jobs) {
        $job = ConvertTo-Hashtable -InputObject $rawJob
        $jobId = Get-NightOwlJobId -Job $job
        $jobType = Get-NightOwlJobType -Job $job
        if ([string]::IsNullOrWhiteSpace($jobId)) {
            $summary.skipped++
            Write-AgentLog -Level "WARN" -Event "job.skipped" -Message "Received job without id." -Data @{ job_type = $jobType }
            continue
        }

        if (Test-NightOwlJobAlreadyExecuted -State $State -JobId $jobId) {
            $summary.skipped++
            Write-AgentLog -Event "job.skipped" -Message "Job already executed by this agent state." -Data @{ job_id = $jobId; job_type = $jobType }
            continue
        }

        Write-AgentLog -Event "job.received" -Message "Job received from backend." -Data @{ job_id = $jobId; job_type = $jobType }
        $result = Invoke-NightOwlJob -Config $Config -State $State -Job $job
        Add-NightOwlExecutedJob -Config $Config -State $State -JobId $jobId -Status ([string]$result["status"])
        if ($result["status"] -eq "completed") {
            $summary.executed++
            Write-AgentLog -Event "job.completed" -Message "Job completed." -Data @{ job_id = $jobId; job_type = $jobType; duration_seconds = $result["duration_seconds"] }
        }
        elseif ($result["status"] -eq "expired") {
            $summary.skipped++
            Write-AgentLog -Level "WARN" -Event "job.failed" -Message "Job expired before execution." -Data @{ job_id = $jobId; job_type = $jobType }
        }
        else {
            $summary.failed++
            Write-AgentLog -Level "WARN" -Event "job.failed" -Message "Job failed." -Data @{ job_id = $jobId; job_type = $jobType; error = $result["error_message"] }
        }

        try {
            Send-NightOwlJobResult -Config $Config -Result $result | Out-Null
        }
        catch {
            Add-NightOwlPendingJobResult -Config $Config -State $State -Result $result
            Write-AgentLog -Level "WARN" -Event "job.result_send_failed" -Message "Job result queued locally for retry." -Data @{
                job_id = $jobId
                job_type = $jobType
                error = $_.Exception.Message
            }
        }
    }
    return $summary
}

function Invoke-HeartbeatCycle {
    param([hashtable]$Config)

    $url = Resolve-HeartbeatUrl -Config $Config
    $token = [string]$Config["agentToken"]
    if ([string]::IsNullOrWhiteSpace($url)) {
        throw "Heartbeat URL is not configured."
    }
    if ([string]::IsNullOrWhiteSpace($token) -or $token -eq "COLE_SEU_TOKEN_AQUI") {
        throw "Agent token is not configured."
    }

    $payload = Get-LightHeartbeatPayload -Config $Config
    $headers = @{ Authorization = "Bearer $token" }
    $timeout = [int]$Config["network"]["timeoutSeconds"]
    if ($timeout -lt 5) { $timeout = 30 }

    $response = Invoke-RestMethod -Method Post -Uri $url -Headers $headers -Body ($payload | ConvertTo-Json -Depth 8) -ContentType "application/json" -TimeoutSec $timeout
    Write-AgentLog -Event "heartbeat.sent" -Message "Heartbeat sent successfully." -Data @{
        url = $url
        machine_id = [string]$response.machine_id
        snapshot_id = [string]$response.snapshot_id
    }
}

function Invoke-PlaceholderCycle {
    param(
        [string]$CycleName,
        [string]$Message
    )
    Write-AgentLog -Event "cycle.skipped" -Message $Message -Data @{ cycle = $CycleName; reason = "prepared_for_future_agent_phase" }
}

function Register-NetworkSuccess {
    param([hashtable]$State)
    $State["failureCount"] = 0
    $State["backoffUntil"] = $null
    $State["lastStatus"] = "online"
    $State["lastSuccessAt"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Register-NetworkFailure {
    param(
        [hashtable]$Config,
        [hashtable]$State,
        [string]$ErrorMessage
    )
    $failureCount = [int]$State["failureCount"] + 1
    $State["failureCount"] = $failureCount
    $minBackoff = [int]$Config["network"]["minBackoffSeconds"]
    $maxBackoff = [int]$Config["network"]["maxBackoffSeconds"]
    if ($minBackoff -lt 5) { $minBackoff = 30 }
    if ($maxBackoff -lt $minBackoff) { $maxBackoff = 300 }
    $seconds = [Math]::Min($maxBackoff, $minBackoff * [Math]::Pow(2, [Math]::Min($failureCount - 1, 4)))
    $State["backoffUntil"] = (Get-Date).ToUniversalTime().AddSeconds($seconds).ToString("yyyy-MM-ddTHH:mm:ssZ")
    $State["lastStatus"] = "backend_unavailable"
    $State["lastErrorAt"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $State["lastError"] = $ErrorMessage
    Write-AgentLog -Level "WARN" -Event "backend.backoff" -Message "Backend unavailable, backing off." -Data @{ seconds = $seconds; failure_count = $failureCount; error = $ErrorMessage }
}

function Invoke-AgentIteration {
    param(
        [hashtable]$Config,
        [hashtable]$State
    )

    $intervals = $Config["intervals"]
    if ((-not $RunJobsOnce) -and (Test-CycleDue -State $State -CycleName "heartbeat" -IntervalSeconds ([int]$intervals["heartbeatSeconds"]))) {
        if (Test-BackendBackoff -State $State) {
            Write-AgentLog -Event "heartbeat.backoff_skip" -Message "Heartbeat skipped during backend backoff." -Data @{ backoff_until = $State["backoffUntil"] }
        }
        else {
            try {
                Invoke-HeartbeatCycle -Config $Config
                Set-CycleResult -State $State -CycleName "heartbeat" -Status "success"
                Register-NetworkSuccess -State $State
            }
            catch {
                Set-CycleResult -State $State -CycleName "heartbeat" -Status "failed" -ErrorMessage $_.Exception.Message
                Register-NetworkFailure -Config $Config -State $State -ErrorMessage $_.Exception.Message
            }
        }
    }

    if ($RunJobsOnce -or (Test-CycleDue -State $State -CycleName "jobs" -IntervalSeconds ([int]$intervals["jobsSeconds"]))) {
        try {
            $jobSummary = Invoke-NightOwlJobCycle -Config $Config -State $State
            Set-CycleResult -State $State -CycleName "jobs" -Status "success" -Metadata @{
                pulled = $jobSummary.pulled
                executed = $jobSummary.executed
                skipped = $jobSummary.skipped
                failed = $jobSummary.failed
            }
        }
        catch {
            Set-CycleResult -State $State -CycleName "jobs" -Status "failed" -ErrorMessage $_.Exception.Message
            Write-AgentLog -Level "WARN" -Event "cycle.failed" -Message "Job pull cycle failed without stopping the service." -Data @{
                cycle = "jobs"
                error = $_.Exception.Message
            }
        }
    }

    if ($RunJobsOnce) {
        return
    }

    $cycles = @(
        @{
            name = "system_inventory"
            interval = [int]$intervals["systemInventorySeconds"]
            endpoint = "systemInventoryUrl"
            collector = { Get-NightOwlSystemInventory }
        },
        @{
            name = "network_inventory"
            interval = [int]$intervals["networkInventorySeconds"]
            endpoint = "networkInventoryUrl"
            collector = { Get-NightOwlNetworkInventory }
        },
        @{
            name = "hardware_inventory"
            interval = [int]$intervals["hardwareInventorySeconds"]
            endpoint = "hardwareInventoryUrl"
            collector = { Get-NightOwlHardwareInventory }
        },
        @{
            name = "disk"
            interval = [int]$intervals["diskSeconds"]
            endpoint = "diskInventoryUrl"
            collector = { Get-NightOwlDiskInventory }
        },
        @{
            name = "security"
            interval = [int]$intervals["securitySeconds"]
            endpoint = "securityInventoryUrl"
            collector = { Get-NightOwlSecurityInventory }
        },
        @{
            name = "software"
            interval = [int]$intervals["softwareSeconds"]
            endpoint = "softwareInventoryUrl"
            collector = { Get-NightOwlSoftwareInventory }
        },
        @{
            name = "full_inventory"
            interval = [int]$intervals["fullInventorySeconds"]
            endpoint = "fullInventoryUrl"
            collector = { Get-NightOwlFullInventory }
        },
        @{
            name = "patches"
            interval = [int]$intervals["patchesSeconds"]
            endpoint = "patchStatusUrl"
            collector = { Get-NightOwlPatchStatus }
        }
    )

    foreach ($cycle in $cycles) {
        if (Test-CycleDue -State $State -CycleName $cycle.name -IntervalSeconds $cycle.interval) {
            try {
                Invoke-CollectionCycle -Config $Config -State $State -CycleName $cycle.name -EndpointKey $cycle.endpoint -Collector $cycle.collector
            }
            catch {
                Set-CycleResult -State $State -CycleName $cycle.name -Status "failed" -ErrorMessage $_.Exception.Message
                Write-AgentLog -Level "WARN" -Event "cycle.failed" -Message "Agent cycle failed without stopping the service." -Data @{
                    cycle = [string]$cycle.name
                    error = $_.Exception.Message
                }
            }
        }
    }
}

Initialize-AgentDirectories
Write-AgentLog -Event "service.starting" -Message "NightOwl service agent starting." -Data @{ base_path = $BasePath; run_once = [bool]$RunOnce; debug = [bool]$DebugMode }

try {
    $config = Read-AgentConfig
    $state = Read-AgentState
    $state["serviceName"] = $ServiceName
    $state["agentMode"] = "service"
    $state["basePath"] = $BasePath

    do {
        Invoke-AgentIteration -Config $config -State $state
        Save-AgentState -State $state

        if ($RunOnce -or $RunJobsOnce) {
            break
        }

        Start-Sleep -Seconds 10
        $config = Read-AgentConfig
        $state = Read-AgentState
    } while ($true)
}
catch {
    Write-AgentLog -Level "ERROR" -Event "service.fatal" -Message "Service loop stopped by fatal error." -Data @{ error = $_.Exception.Message }
    throw
}
finally {
    Write-AgentLog -Event "service.stopped" -Message "NightOwl service agent stopped." -Data @{ run_once = [bool]$RunOnce }
}
