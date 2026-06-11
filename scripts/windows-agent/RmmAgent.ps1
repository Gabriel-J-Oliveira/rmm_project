[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$AgentRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $AgentRoot "RmmAgent.config.ps1"
$LogDirectory = "C:\RMM\logs"
$LogPath = Join-Path $LogDirectory "agent.log"
$LastPayloadPath = Join-Path $LogDirectory "last_payload.json"
$LastResponsePath = Join-Path $LogDirectory "last_response.json"
$StatePath = Join-Path $AgentRoot "agent.state.json"
$VersionPath = Join-Path $AgentRoot "VERSION"
$AgentVersionFallback = "0.1.0"
$AgentMode = "scheduled_task"
$AgentInstallPath = "C:\RMM"
$AgentTaskName = "RMM-Agent-Heartbeat"
$AgentRuntime = "powershell"
$AgentRuntimeVersion = $PSVersionTable.PSVersion.ToString()
$AgentUpdateSource = "\\192.168.104.120\controlsul\Comum\_Agents"

function Get-RmmAgentVersion {
    if (Test-Path $VersionPath) {
        $version = (Get-Content -Path $VersionPath -Raw).Trim()
        if (-not [string]::IsNullOrWhiteSpace($version)) {
            return $version
        }
    }

    return $AgentVersionFallback
}

function Read-RmmState {
    if (-not (Test-Path $StatePath)) {
        return @{}
    }

    try {
        $stateObject = Get-Content -Path $StatePath -Raw | ConvertFrom-Json
        $state = @{}
        foreach ($property in $stateObject.PSObject.Properties) {
            $state[$property.Name] = $property.Value
        }
        return $state
    }
    catch {
        Write-RmmLog -Level "WARN" -Message "Failed to read state file: $($_.Exception.Message)"
        return @{}
    }
}

function Write-RmmState {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    try {
        $State | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding UTF8
    }
    catch {
        Write-RmmLog -Level "WARN" -Message "Failed to write state file: $($_.Exception.Message)"
    }
}

function Set-RmmStateDefaults {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$LocalVersion
    )

    $State["update_source"] = $AgentUpdateSource
    $State["package_manifest_path"] = Join-Path $AgentUpdateSource "manifest.json"
    $State["local_version"] = $LocalVersion

    foreach ($key in @(
        "last_run_at",
        "last_success_at",
        "last_error_at",
        "last_error",
        "last_status",
        "last_machine_id",
        "last_snapshot_id",
        "last_update_check_at",
        "last_update_status",
        "last_update_error"
    )) {
        if (-not $State.ContainsKey($key)) {
            $State[$key] = $null
        }
    }

    return $State
}

function Write-RmmLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level = "INFO"
    )

    if (-not (Test-Path $LogDirectory)) {
        New-Item -Path $LogDirectory -ItemType Directory -Force | Out-Null
    }

    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $LogPath -Value "[$timestamp] [$Level] $Message"
}

function Invoke-RmmSafe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [scriptblock]$ScriptBlock,

        [Parameter(Mandatory = $true)]
        $DefaultValue
    )

    try {
        return & $ScriptBlock
    }
    catch {
        Write-RmmLog -Level "WARN" -Message "$Name collection failed: $($_.Exception.Message)"
        return $DefaultValue
    }
}

function Convert-RmmText {
    param(
        [Parameter(Mandatory = $false)]
        $Value
    )

    if ($null -eq $Value) {
        return ""
    }

    return ([string]$Value).Replace([char]0x00A0, [char]0x0020)
}

function Get-RegistryInstalledSoftware {
    $registryPaths = @(
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )

    $items = foreach ($path in $registryPaths) {
        if (Test-Path $path) {
            Get-ItemProperty -Path $path -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -and -not $_.SystemComponent } |
                ForEach-Object {
                    [PSCustomObject]@{
                        name = Convert-RmmText $_.DisplayName
                        version = Convert-RmmText $_.DisplayVersion
                        publisher = Convert-RmmText $_.Publisher
                    }
                }
        }
    }

    $items |
        Sort-Object name, version, publisher -Unique |
        Select-Object -First 1000
}

function Convert-RmmLastBootTime {
    param(
        [Parameter(Mandatory = $false)]
        $LastBootUpTime
    )

    if ($null -eq $LastBootUpTime) {
        throw "LastBootUpTime is empty."
    }

    if ($LastBootUpTime -is [DateTime]) {
        return $LastBootUpTime
    }

    $lastBootText = [string]$LastBootUpTime
    if ([string]::IsNullOrWhiteSpace($lastBootText)) {
        throw "LastBootUpTime is empty."
    }

    return [System.Management.ManagementDateTimeConverter]::ToDateTime($lastBootText)
}

function Get-UptimeSeconds {
    param(
        [Parameter(Mandatory = $true)]
        $OperatingSystem
    )

    try {
        $lastBoot = Convert-RmmLastBootTime -LastBootUpTime $OperatingSystem.LastBootUpTime
        return [Int64]([DateTime]::Now - $lastBoot).TotalSeconds
    }
    catch {
        Write-RmmLog -Level "WARN" -Message "Uptime collection failed: $($_.Exception.Message)"
        return 0
    }
}

function Get-DefenderStatus {
    if (-not (Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue)) {
        return @{
            available = $false
            reason = "Get-MpComputerStatus not available"
        }
    }

    try {
        $status = Get-MpComputerStatus
        return @{
            available = $true
            enabled = [bool]$status.AntivirusEnabled
            real_time_protection_enabled = [bool]$status.RealTimeProtectionEnabled
            antispyware_enabled = [bool]$status.AntispywareEnabled
            antivirus_signature_last_updated = if ($status.AntivirusSignatureLastUpdated) { $status.AntivirusSignatureLastUpdated.ToUniversalTime().ToString("o") } else { $null }
            engine_version = [string]$status.AMEngineVersion
            product_status = [string]$status.ProductStatus
        }
    }
    catch {
        return @{
            available = $false
            reason = $_.Exception.Message
        }
    }
}

function Get-ActiveIPv4Addresses {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -ne "127.0.0.1" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.PrefixOrigin -ne "WellKnown" -and
            $_.AddressState -eq "Preferred"
        } |
        Select-Object -ExpandProperty IPAddress -Unique
}

function Get-LoggedUser {
    $computerSystem = Get-CimInstance -ClassName Win32_ComputerSystem
    if ($computerSystem.UserName) {
        return [string]$computerSystem.UserName
    }

    return ""
}

function Read-RmmHttpResponseBody {
    param(
        [Parameter(Mandatory = $false)]
        $Response
    )

    if ($null -eq $Response) {
        return ""
    }

    try {
        $stream = $Response.GetResponseStream()
        if ($null -eq $stream) {
            return ""
        }

        $reader = New-Object System.IO.StreamReader($stream)
        try {
            return $reader.ReadToEnd()
        }
        finally {
            $reader.Close()
        }
    }
    catch {
        return "Failed to read HTTP response body: $($_.Exception.Message)"
    }
}

try {
    if (-not (Test-Path $ConfigPath)) {
        throw "Configuration file not found: $ConfigPath. Copy RmmAgent.config.example.ps1 to RmmAgent.config.ps1 and set the token."
    }

    . $ConfigPath

    if ([string]::IsNullOrWhiteSpace($RmmServerUrl)) {
        throw "RmmServerUrl is not configured."
    }

    if ([string]::IsNullOrWhiteSpace($AgentToken) -or $AgentToken -eq "COLE_SEU_TOKEN_AQUI") {
        throw "AgentToken is not configured."
    }

    $AgentVersion = Get-RmmAgentVersion
    $agentState = Read-RmmState
    $agentState = Set-RmmStateDefaults -State $agentState -LocalVersion $AgentVersion
    $agentState["last_run_at"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $agentState["last_status"] = "running"
    Write-RmmState -State $agentState

    $computerSystem = Invoke-RmmSafe -Name "Computer system" -DefaultValue ([PSCustomObject]@{}) -ScriptBlock {
        Get-CimInstance -ClassName Win32_ComputerSystem
    }
    $operatingSystem = Invoke-RmmSafe -Name "Operating system" -DefaultValue ([PSCustomObject]@{}) -ScriptBlock {
        Get-CimInstance -ClassName Win32_OperatingSystem
    }
    $processor = Invoke-RmmSafe -Name "Processor" -DefaultValue ([PSCustomObject]@{}) -ScriptBlock {
        Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
    }
    $bios = Invoke-RmmSafe -Name "BIOS" -DefaultValue ([PSCustomObject]@{}) -ScriptBlock {
        Get-CimInstance -ClassName Win32_BIOS
    }
    $disks = Invoke-RmmSafe -Name "Local disks" -DefaultValue @() -ScriptBlock {
        Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DriveType=3" |
        ForEach-Object {
            @{
                name = [string]$_.DeviceID
                size_bytes = if ($null -ne $_.Size) { [Int64]$_.Size } else { 0 }
                free_bytes = if ($null -ne $_.FreeSpace) { [Int64]$_.FreeSpace } else { 0 }
            }
        }
    }

    $uptimeSeconds = Get-UptimeSeconds -OperatingSystem $operatingSystem

    $domain = ""
    if ($computerSystem.PartOfDomain) {
        $domain = [string]$computerSystem.Domain
    }

    $payload = @{
        schema_version = 1
        hostname = [string]$env:COMPUTERNAME
        domain = $domain
        logged_user = Invoke-RmmSafe -Name "Logged user" -DefaultValue "" -ScriptBlock { Get-LoggedUser }
        ips = @(Invoke-RmmSafe -Name "IPv4 addresses" -DefaultValue @() -ScriptBlock { Get-ActiveIPv4Addresses })
        os = @{
            name = [string]$operatingSystem.Caption
            version = [string]$operatingSystem.Version
            build = [string]$operatingSystem.BuildNumber
        }
        hardware = @{
            cpu = [string]$processor.Name
            memory_total_bytes = [Int64]$computerSystem.TotalPhysicalMemory
            manufacturer = [string]$computerSystem.Manufacturer
            model = [string]$computerSystem.Model
            serial_number = [string]$bios.SerialNumber
        }
        disks = @($disks)
        uptime_seconds = $uptimeSeconds
        installed_software = @(Invoke-RmmSafe -Name "Installed software" -DefaultValue @() -ScriptBlock { Get-RegistryInstalledSoftware })
        defender_status = Invoke-RmmSafe -Name "Windows Defender" -DefaultValue @{ available = $false; reason = "Collection failed" } -ScriptBlock { Get-DefenderStatus }
        agent = @{
            version = $AgentVersion
            mode = $AgentMode
            install_path = $AgentInstallPath
            task_name = $AgentTaskName
            runtime = $AgentRuntime
            runtime_version = $AgentRuntimeVersion
            update_source = $AgentUpdateSource
            last_status = [string]$agentState["last_update_status"]
        }
        heartbeat_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }

    $headers = @{
        Authorization = "Bearer $AgentToken"
    }

    $json = $payload | ConvertTo-Json -Depth 10 -Compress
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($json)

    if (-not (Test-Path $LogDirectory)) {
        New-Item -Path $LogDirectory -ItemType Directory -Force | Out-Null
    }
    Set-Content -Path $LastPayloadPath -Value $json -Encoding UTF8

    try {
        $response = Invoke-RestMethod -Uri $RmmServerUrl -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -Body $bodyBytes
        $response | ConvertTo-Json -Depth 6 | Set-Content -Path $LastResponsePath -Encoding UTF8
    }
    catch {
        $httpResponse = $_.Exception.Response
        if ($null -ne $httpResponse) {
            $statusCode = [int]$httpResponse.StatusCode
            $statusDescription = [string]$httpResponse.StatusDescription
            $responseBody = Read-RmmHttpResponseBody -Response $httpResponse

            Write-RmmLog -Level "ERROR" -Message "HTTP request failed. status_code=$statusCode status_description=$statusDescription"
            Write-RmmLog -Level "ERROR" -Message "HTTP response body: $responseBody"
            Write-RmmLog -Level "ERROR" -Message "Last payload saved to: $LastPayloadPath"
        }
        else {
            Write-RmmLog -Level "ERROR" -Message "HTTP request failed without response object: $($_.Exception.Message)"
            Write-RmmLog -Level "ERROR" -Message "Last payload saved to: $LastPayloadPath"
        }

        throw
    }

    $agentState["last_status"] = "success"
    $agentState["last_success_at"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $agentState["last_error"] = ""
    $agentState["last_error_at"] = $null
    $agentState["last_machine_id"] = [string]$response.machine_id
    $agentState["last_snapshot_id"] = [string]$response.snapshot_id
    Write-RmmState -State $agentState

    Write-RmmLog -Level "INFO" -Message "Heartbeat sent successfully. machine_id=$($response.machine_id) snapshot_id=$($response.snapshot_id)"
    exit 0
}
catch {
    $failedVersion = Get-RmmAgentVersion
    $failedState = Read-RmmState
    $failedState = Set-RmmStateDefaults -State $failedState -LocalVersion $failedVersion
    $failedState["last_status"] = "failed"
    $failedState["last_error_at"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $failedState["last_error"] = $_.Exception.Message
    Write-RmmState -State $failedState
    Write-RmmLog -Level "ERROR" -Message "Heartbeat failed: $($_.Exception.Message)"
    exit 1
}
