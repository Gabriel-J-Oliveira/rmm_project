<#
.SYNOPSIS
Runs the Night Owl access inventory agent for AD and file-server ACL collection.

.DESCRIPTION
Reads config.json, sends a heartbeat to Night Owl, optionally collects Active
Directory inventory, collects NTFS ACLs by invoking Export-FileServerAcl.ps1 for
each configured target, and posts each payload to the access_inventory Agent API.

Compatible with Windows PowerShell 5.1.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'config.json')
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$AgentVersion = '0.3.0'
$script:LogPath = $null
$script:HadFailure = $false

function Write-AgentLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [Parameter(Mandatory = $false)]
        [ValidateSet('INFO', 'WARN', 'ERROR')]
        [string]$Level = 'INFO'
    )

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$timestamp] [$Level] $Message"
    Write-Host $line

    if ($script:LogPath) {
        $logDirectory = Split-Path -Parent $script:LogPath
        if (-not [string]::IsNullOrWhiteSpace($logDirectory) -and -not (Test-Path -LiteralPath $logDirectory)) {
            New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        }
        Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
    }
}

function Fail-Agent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [Parameter(Mandatory = $false)]
        [int]$ExitCode = 1
    )

    Write-AgentLog -Level 'ERROR' -Message $Message
    exit $ExitCode
}

function Read-AgentConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Config file not found: $Path"
    }

    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "Could not read or parse config JSON '$Path': $($_.Exception.Message)"
    }
}

function Get-ConfigValue {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Object,

        [Parameter(Mandatory = $true)]
        [string[]]$Names,

        [Parameter(Mandatory = $false)]
        [object]$Default = $null
    )

    foreach ($name in $Names) {
        if ($Object.PSObject.Properties.Name -contains $name) {
            $value = $Object.$name
            if ($null -eq $value) {
                continue
            }

            if ($value -is [string]) {
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
                continue
            }

            if ($value -is [System.Array]) {
                return $value
            }

            if ($value -is [System.Collections.IEnumerable] -and -not ($value -is [string])) {
                return $value
            }

            if ($value -is [bool] -or $value -is [int] -or $value -is [long] -or $value -is [double] -or $value -is [decimal]) {
                return $value
            }

            if ($value -is [psobject]) {
                return $value
            }

            return $value
        }
    }

    return $Default
}

function Get-RequiredConfigValue {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Object,

        [Parameter(Mandatory = $true)]
        [string[]]$Names,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $value = Get-ConfigValue -Object $Object -Names $Names
    if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) {
        throw "Missing required config value: $Label"
    }
    return ([string]$value).Trim()
}

function Join-BaseUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return ($BaseUrl.TrimEnd('/') + '/' + $Path.TrimStart('/'))
}

function Assert-HttpsBaseUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl
    )

    try {
        $uri = [Uri]$BaseUrl
    }
    catch {
        throw "Invalid server_url '$BaseUrl': $($_.Exception.Message)"
    }

    if ($uri.Scheme -ne 'https') {
        throw "server_url must use HTTPS. Current value: $BaseUrl"
    }

    return $uri
}

function Test-NightowlDns {
    param(
        [Parameter(Mandatory = $true)]
        [Uri]$BaseUri
    )

    try {
        [System.Net.Dns]::GetHostEntry($BaseUri.Host) | Out-Null
    }
    catch {
        throw "DNS resolution failed for '$($BaseUri.Host)': $($_.Exception.Message)"
    }
}

function Get-FriendlyWebError {
    param(
        [Parameter(Mandatory = $true)]
        [System.Management.Automation.ErrorRecord]$ErrorRecord,

        [Parameter(Mandatory = $true)]
        [string]$EndpointLabel
    )

    $exception = $ErrorRecord.Exception
    $message = $exception.Message
    $responseBody = ''

    if ($exception.Response -and $exception.Response.StatusCode) {
        $statusCode = [int]$exception.Response.StatusCode
        try {
            $stream = $exception.Response.GetResponseStream()
            if ($stream) {
                $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
                $responseBody = $reader.ReadToEnd()
                $reader.Close()
            }
        }
        catch {
            $responseBody = ''
        }

        if (-not [string]::IsNullOrWhiteSpace($responseBody)) {
            $message = "$message Response body: $responseBody"
        }

        if ($statusCode -eq 401 -or $statusCode -eq 403) {
            return "$EndpointLabel rejected the token or permission: HTTP $statusCode. $responseBody"
        }
        return "$EndpointLabel returned HTTP ${statusCode}: $message"
    }

    if ($message -match 'trust relationship|certificate|SSL|TLS|Could not establish secure channel') {
        return "$EndpointLabel TLS/certificate validation failed: $message"
    }

    if ($message -match 'remote name could not be resolved|NameResolutionFailure') {
        return "$EndpointLabel DNS resolution failed: $message"
    }

    if ($message -match 'Unable to connect|actively refused|timed out|timeout') {
        return "$EndpointLabel connection failed or timed out: $message"
    }

    return "$EndpointLabel request failed: $message"
}

function New-NightowlHeaders {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    return @{
        'X-Nightowl-Agent-Token' = $Token
    }
}

function Invoke-NightowlPostJsonBody {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$Token,

        [Parameter(Mandatory = $true)]
        [string]$JsonBody,

        [Parameter(Mandatory = $true)]
        [string]$EndpointLabel,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutSec
    )

    $headers = New-NightowlHeaders -Token $Token

    try {
        return Invoke-RestMethod -Uri $Uri -Method Post -Headers $headers -Body $JsonBody -ContentType 'application/json' -TimeoutSec $TimeoutSec
    }
    catch {
        throw (Get-FriendlyWebError -ErrorRecord $_ -EndpointLabel $EndpointLabel)
    }
}

function Invoke-NightowlPostJsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$Token,

        [Parameter(Mandatory = $true)]
        [string]$PayloadPath,

        [Parameter(Mandatory = $true)]
        [string]$EndpointLabel,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutSec
    )

    if (-not (Test-Path -LiteralPath $PayloadPath -PathType Leaf)) {
        throw "$EndpointLabel payload file not found: $PayloadPath"
    }

    $headers = New-NightowlHeaders -Token $Token

    try {
        return Invoke-RestMethod -Uri $Uri -Method Post -Headers $headers -ContentType 'application/json' -InFile $PayloadPath -TimeoutSec $TimeoutSec
    }
    catch {
        throw (Get-FriendlyWebError -ErrorRecord $_ -EndpointLabel $EndpointLabel)
    }
}

function Get-FileAclTargets {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Config
    )

    if ($Config.PSObject.Properties.Name -contains 'file_acl_targets') {
        $targets = $Config.file_acl_targets
        if ($null -eq $targets) {
            return @()
        }

        $targetList = @($targets)
        return $targetList
    }

    $legacyFileServer = Get-ConfigValue -Object $Config -Names @('FileServerName')
    $legacyShare = Get-ConfigValue -Object $Config -Names @('ShareName')
    $legacyUnc = Get-ConfigValue -Object $Config -Names @('UncPath', 'path', 'unc_path')
    if ($legacyFileServer -and $legacyShare -and $legacyUnc) {
        return @([pscustomobject]@{
            name = $legacyShare
            file_server_name = $legacyFileServer
            share_name = $legacyShare
            unc_path = $legacyUnc
            max_depth = Get-ConfigValue -Object $Config -Names @('MaxDepth') -Default -1
            include_inherited = Get-ConfigValue -Object $Config -Names @('IncludeInherited') -Default $false
            verbose_log = Get-ConfigValue -Object $Config -Names @('VerboseLog') -Default $false
            export_json_path = Get-ConfigValue -Object $Config -Names @('ExportJsonPath')
        })
    }

    throw "Missing required config value: file_acl_targets"
}

function Test-AdInventoryEnabled {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Config
    )

    if (-not ($Config.PSObject.Properties.Name -contains 'ad_inventory')) {
        return $false
    }

    if ($null -eq $Config.ad_inventory) {
        return $false
    }

    $enabled = Get-ConfigValue -Object $Config.ad_inventory -Names @('enabled') -Default $false
    return [bool]$enabled
}

function New-TempPayloadPath {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Config,

        [Parameter(Mandatory = $true)]
        [string]$TargetName
    )

    $tempDirectory = Get-ConfigValue -Object $Config -Names @('temp_directory', 'TempDirectory') -Default (Join-Path $env:TEMP 'NightowlAccessInventory')
    if (-not (Test-Path -LiteralPath $tempDirectory)) {
        New-Item -ItemType Directory -Path $tempDirectory -Force | Out-Null
    }

    $safeTargetName = ($TargetName -replace '[^a-zA-Z0-9_.-]', '_')
    return (Join-Path $tempDirectory ("file_acl_{0}_{1:yyyyMMdd_HHmmss}.json" -f $safeTargetName, (Get-Date)))
}

function New-TempAdInventoryPath {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Config
    )

    $tempDirectory = Get-ConfigValue -Object $Config -Names @('temp_directory', 'TempDirectory') -Default (Join-Path $env:TEMP 'NightowlAccessInventory')
    if (-not (Test-Path -LiteralPath $tempDirectory)) {
        New-Item -ItemType Directory -Path $tempDirectory -Force | Out-Null
    }

    return (Join-Path $tempDirectory ("ad_inventory_{0:yyyyMMdd_HHmmss}.json" -f (Get-Date)))
}

function Invoke-AdInventoryExport {
    param(
        [Parameter(Mandatory = $true)]
        [object]$AdConfig,

        [Parameter(Mandatory = $true)]
        [string]$ExportPath
    )

    $exportScript = Join-Path $PSScriptRoot 'Export-AdInventory.ps1'
    if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
        throw "AD export script not found: $exportScript"
    }

    $exportParams = @{
        OutputPath = $ExportPath
    }

    $domain = Get-ConfigValue -Object $AdConfig -Names @('domain') -Default ''
    if (-not [string]::IsNullOrWhiteSpace([string]$domain)) {
        $exportParams.Domain = [string]$domain
    }

    $searchBase = Get-ConfigValue -Object $AdConfig -Names @('search_base') -Default ''
    if (-not [string]::IsNullOrWhiteSpace([string]$searchBase)) {
        $exportParams.SearchBase = [string]$searchBase
    }

    $includeDisabledUsers = Get-ConfigValue -Object $AdConfig -Names @('include_disabled_users') -Default $false
    if ([bool]$includeDisabledUsers) {
        $exportParams.IncludeDisabledUsers = $true
    }

    $collectGroupMemberships = Get-ConfigValue -Object $AdConfig -Names @('collect_group_memberships') -Default $true
    if (-not [bool]$collectGroupMemberships) {
        $exportParams.SkipGroupMemberships = $true
    }

    $verboseSkippedMembers = Get-ConfigValue -Object $AdConfig -Names @('verbose_skipped_members') -Default $false
    if ([bool]$verboseSkippedMembers) {
        $exportParams.VerboseSkippedMembers = $true
    }

    $verboseLog = Get-ConfigValue -Object $AdConfig -Names @('verbose_log') -Default $false
    if ([bool]$verboseLog) {
        $exportParams.VerboseLog = $true
    }

    Write-AgentLog "Collecting Active Directory inventory with Export-AdInventory.ps1."
    try {
        & $exportScript @exportParams
    }
    catch {
        throw "Active Directory inventory collection failed: $($_.Exception.Message)"
    }

    if (-not (Test-Path -LiteralPath $ExportPath -PathType Leaf)) {
        throw "Active Directory inventory collection finished without creating output file: $ExportPath"
    }
}

function Invoke-AdInventoryFlow {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Config,

        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$Token,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutSec
    )

    if (-not ($Config.PSObject.Properties.Name -contains 'ad_inventory')) {
        Write-AgentLog "AD inventory config not present; skipping AD collection."
        return
    }

    $adConfig = $Config.ad_inventory
    if ($null -eq $adConfig) {
        Write-AgentLog "AD inventory config is null; skipping AD collection." -Level 'WARN'
        return
    }

    $enabled = Get-ConfigValue -Object $adConfig -Names @('enabled') -Default $false
    if (-not [bool]$enabled) {
        Write-AgentLog "AD inventory collection disabled by config."
        return
    }

    $exportPath = Get-ConfigValue -Object $adConfig -Names @('export_json_path')
    if ($null -eq $exportPath -or [string]::IsNullOrWhiteSpace([string]$exportPath)) {
        $exportPath = New-TempAdInventoryPath -Config $Config
    }

    Invoke-AdInventoryExport -AdConfig $adConfig -ExportPath ([string]$exportPath)

    Write-AgentLog "Reading AD inventory payload: $exportPath"
    $payloadJson = Get-Content -LiteralPath ([string]$exportPath) -Raw -Encoding UTF8

    try {
        $payloadCheck = $payloadJson | ConvertFrom-Json
        $ouCount = @($payloadCheck.ous).Count
        $userCount = @($payloadCheck.users).Count
        $groupCount = @($payloadCheck.groups).Count
        $membershipCount = @($payloadCheck.memberships).Count
        $errorCount = @($payloadCheck.errors).Count
        Write-AgentLog "AD payload ready. OUs: $ouCount; Users: $userCount; Groups: $groupCount; Memberships: $membershipCount; collection errors: $errorCount"
    }
    catch {
        throw "Generated AD inventory JSON is invalid: $($_.Exception.Message)"
    }

    Write-AgentLog "Sending AD inventory payload to $Uri with -InFile"
    $adResponse = Invoke-NightowlPostJsonFile -Uri $Uri -Token $Token -PayloadPath ([string]$exportPath) -EndpointLabel 'ad-inventory endpoint' -TimeoutSec $TimeoutSec
    Write-AgentLog "AD inventory payload accepted. Run ID: $($adResponse.run_id)"
    Write-AgentLog "AD import summary: created=$($adResponse.summary.created); updated=$($adResponse.summary.updated); ignored=$($adResponse.summary.ignored); errors=$($adResponse.summary.errors)"
}

function Invoke-AclExportForTarget {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Target,

        [Parameter(Mandatory = $true)]
        [string]$ExportPath
    )

    $exportScript = Join-Path $PSScriptRoot 'Export-FileServerAcl.ps1'
    if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
        throw "Export script not found: $exportScript"
    }

    $targetName = Get-ConfigValue -Object $Target -Names @('name', 'share_name', 'ShareName') -Default 'target'
    $fileServerName = Get-RequiredConfigValue -Object $Target -Names @('file_server_name', 'FileServerName') -Label 'file_acl_targets[].file_server_name'
    $shareName = Get-RequiredConfigValue -Object $Target -Names @('share_name', 'ShareName') -Label 'file_acl_targets[].share_name'
    $uncPath = Get-RequiredConfigValue -Object $Target -Names @('path', 'unc_path', 'UncPath') -Label 'file_acl_targets[].path'
    $maxDepth = [int](Get-ConfigValue -Object $Target -Names @('max_depth', 'MaxDepth') -Default -1)

    $exportParams = @{
        FileServerName = $fileServerName
        ShareName = $shareName
        UncPath = $uncPath
        OutputPath = $ExportPath
        MaxDepth = $maxDepth
    }

    $includeInherited = Get-ConfigValue -Object $Target -Names @('include_inherited', 'IncludeInherited') -Default $false
    if ([bool]$includeInherited) {
        $exportParams.IncludeInherited = $true
    }

    $verboseLog = Get-ConfigValue -Object $Target -Names @('verbose_log', 'VerboseLog') -Default $false
    if ([bool]$verboseLog) {
        $exportParams.VerboseLog = $true
    }

    Write-AgentLog "Collecting ACLs for target '$targetName' using Export-FileServerAcl.ps1."
    try {
        & $exportScript @exportParams
    }
    catch {
        throw "ACL collection failed for target '$targetName': $($_.Exception.Message)"
    }

    if (-not (Test-Path -LiteralPath $ExportPath -PathType Leaf)) {
        throw "ACL collection for target '$targetName' finished without creating output file: $ExportPath"
    }
}

try {
    $config = Read-AgentConfig -Path $ConfigPath

    $defaultLogPath = Join-Path $PSScriptRoot 'logs\NightowlAccessInventoryAgent.log'
    $script:LogPath = [string](Get-ConfigValue -Object $config -Names @('log_path', 'LogPath') -Default $defaultLogPath)

    Write-AgentLog "Night Owl Access Inventory Agent starting. Version: $AgentVersion"
    Write-AgentLog "Config path: $ConfigPath"

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $serverUrl = Get-RequiredConfigValue -Object $config -Names @('server_url', 'BaseUrl') -Label 'server_url'
    $agentToken = Get-RequiredConfigValue -Object $config -Names @('agent_token', 'AgentToken') -Label 'agent_token'
    $hostname = Get-RequiredConfigValue -Object $config -Names @('hostname') -Label 'hostname'
    $collectorName = Get-RequiredConfigValue -Object $config -Names @('collector_name') -Label 'collector_name'
    $timeoutSec = [int](Get-ConfigValue -Object $config -Names @('timeout_sec', 'TimeoutSec') -Default 60)
    $baseUri = Assert-HttpsBaseUrl -BaseUrl $serverUrl
    Test-NightowlDns -BaseUri $baseUri

    $heartbeatUrl = Join-BaseUrl -BaseUrl $serverUrl -Path '/api/access-inventory/agent/heartbeat/'
    $fileAclUrl = Join-BaseUrl -BaseUrl $serverUrl -Path '/api/access-inventory/agent/file-acl/'
    $adInventoryUrl = Join-BaseUrl -BaseUrl $serverUrl -Path '/api/access-inventory/agent/ad-inventory/'

    $adInventoryEnabled = Test-AdInventoryEnabled -Config $config
    $targets = Get-FileAclTargets -Config $config
    $targetCount = @($targets).Count
    if ($targetCount -lt 1 -and -not $adInventoryEnabled) {
        throw "file_acl_targets must contain at least one target when ad_inventory.enabled is false or absent."
    }

    $heartbeatPayload = [ordered]@{
        version = $AgentVersion
        hostname = $hostname
        collector_name = $collectorName
        target_count = $targetCount
        ad_inventory_enabled = $adInventoryEnabled
        sent_at = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json -Depth 5

    Write-AgentLog "Sending heartbeat to $heartbeatUrl"
    $heartbeatResponse = Invoke-NightowlPostJsonBody -Uri $heartbeatUrl -Token $agentToken -JsonBody $heartbeatPayload -EndpointLabel 'heartbeat endpoint' -TimeoutSec $timeoutSec
    Write-AgentLog "Heartbeat accepted. Run ID: $($heartbeatResponse.run_id)"

    try {
        Invoke-AdInventoryFlow -Config $config -Uri $adInventoryUrl -Token $agentToken -TimeoutSec $timeoutSec
    }
    catch {
        $script:HadFailure = $true
        Write-AgentLog -Level 'ERROR' -Message $_.Exception.Message
    }

    if ($targetCount -lt 1) {
        Write-AgentLog "No file ACL targets configured; skipping file ACL collection."
    }

    foreach ($target in $targets) {
        $targetName = [string](Get-ConfigValue -Object $target -Names @('name', 'share_name', 'ShareName') -Default 'target')
        Write-AgentLog "Starting target '$targetName'."

        try {
            $exportPath = Get-ConfigValue -Object $target -Names @('export_json_path', 'ExportJsonPath')
            if ($null -eq $exportPath -or [string]::IsNullOrWhiteSpace([string]$exportPath)) {
                $exportPath = New-TempPayloadPath -Config $config -TargetName $targetName
            }

            Invoke-AclExportForTarget -Target $target -ExportPath ([string]$exportPath)

            Write-AgentLog "Reading ACL payload for target '$targetName': $exportPath"
            $payloadJson = Get-Content -LiteralPath ([string]$exportPath) -Raw -Encoding UTF8

            try {
                $payloadCheck = $payloadJson | ConvertFrom-Json
                $folderCount = @($payloadCheck.folders).Count
                $aclCount = @($payloadCheck.acl_entries).Count
                $errorCount = @($payloadCheck.errors).Count
                Write-AgentLog "ACL payload ready for target '$targetName'. Folders: $folderCount; ACL entries: $aclCount; collection errors: $errorCount"
            }
            catch {
                throw "Generated ACL JSON is invalid for target '$targetName': $($_.Exception.Message)"
            }

            Write-AgentLog "Sending ACL payload for target '$targetName' to $fileAclUrl with -InFile"
            $fileAclResponse = Invoke-NightowlPostJsonFile -Uri $fileAclUrl -Token $agentToken -PayloadPath ([string]$exportPath) -EndpointLabel 'file-acl endpoint' -TimeoutSec $timeoutSec
            Write-AgentLog "File ACL payload accepted for target '$targetName'. Run ID: $($fileAclResponse.run_id)"
            Write-AgentLog "Import summary for target '$targetName': created=$($fileAclResponse.summary.created); updated=$($fileAclResponse.summary.updated); ignored=$($fileAclResponse.summary.ignored); errors=$($fileAclResponse.summary.errors)"
        }
        catch {
            $script:HadFailure = $true
            Write-AgentLog -Level 'ERROR' -Message $_.Exception.Message
        }
    }

    if ($script:HadFailure) {
        Fail-Agent -Message "Night Owl Access Inventory Agent finished with one or more target failures." -ExitCode 2
    }

    Write-AgentLog "Night Owl Access Inventory Agent finished successfully."
    exit 0
}
catch {
    Fail-Agent -Message $_.Exception.Message -ExitCode 1
}
