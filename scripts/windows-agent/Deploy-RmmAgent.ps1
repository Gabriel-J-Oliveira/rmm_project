[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$DeployCsv,

    [ValidateSet("CopyOnly", "InstallViaScheduledTaskRemote")]
    [string]$Mode = "CopyOnly",

    [string]$SourcePath = "\\192.168.104.120\controlsul\Comum\_Agents",
    [string]$InstallPath = "C:\RMM",
    [string]$ReportPath = "",
    [switch]$SkipPing,
    [switch]$ForceConfig,
    [int]$MaxConcurrency = 1
)

$ErrorActionPreference = "Stop"
$startedScriptAt = Get-Date

function New-ReportRoot {
    if ($ReportPath) {
        $parent = Split-Path -Parent $ReportPath
        if ($parent -and -not (Test-Path $parent)) {
            New-Item -Path $parent -ItemType Directory -Force | Out-Null
        }
        return $ReportPath
    }
    $root = Join-Path (Get-Location) "deploy-reports"
    if (-not (Test-Path $root)) {
        New-Item -Path $root -ItemType Directory -Force | Out-Null
    }
    return Join-Path $root ("deploy-report-{0}.csv" -f (Get-Date).ToString("yyyyMMdd-HHmmss"))
}

function Write-DeployLog {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $script:LogPath -Value "[$timestamp] [$Level] $Message"
    Write-Host "[$Level] $Message"
}

function Convert-ToAdminSharePath {
    param([string]$Hostname, [string]$LocalPath)
    $relative = $LocalPath.TrimStart("\")
    if ($relative -match "^[A-Za-z]:\\") {
        $drive = $relative.Substring(0, 1)
        $rest = $relative.Substring(3)
        return "\\$Hostname\$drive`$\$rest"
    }
    return "\\$Hostname\C$\$relative"
}

function Test-ReadableFile {
    param([string]$Path)
    Get-Content -Path $Path -TotalCount 1 -ErrorAction Stop | Out-Null
}

function New-Result {
    param(
        [string]$Hostname,
        [string]$Status,
        [string]$Step,
        [string]$Message,
        [datetime]$StartedAt,
        [bool]$PingOk,
        [bool]$AdminShareOk,
        [int]$CopiedFilesCount,
        [string]$ConfigAction,
        [string]$TaskAction,
        [string]$ErrorMessage
    )
    $finishedAt = Get-Date
    [PSCustomObject]@{
        hostname = $Hostname
        status = $Status
        mode = $Mode
        step = $Step
        message = $Message
        started_at = $StartedAt.ToString("yyyy-MM-ddTHH:mm:ss")
        finished_at = $finishedAt.ToString("yyyy-MM-ddTHH:mm:ss")
        duration_seconds = [math]::Round(($finishedAt - $StartedAt).TotalSeconds, 2)
        ping_ok = $PingOk
        admin_share_ok = $AdminShareOk
        copied_files_count = $CopiedFilesCount
        config_action = $ConfigAction
        task_action = $TaskAction
        error = $ErrorMessage
    }
}

function Copy-AgentToHost {
    param($Row)

    $hostname = ([string]$Row.hostname).Trim()
    $startedAt = Get-Date
    $pingOk = $false
    $adminShareOk = $false
    $copied = 0
    $configAction = ""
    $taskAction = "not_supported"

    if ([string]::IsNullOrWhiteSpace($hostname)) {
        return New-Result -Hostname "" -Status "skipped" -Step "validate" -Message "Empty hostname" -StartedAt $startedAt -PingOk $false -AdminShareOk $false -CopiedFilesCount 0 -ConfigAction "" -TaskAction $taskAction -ErrorMessage ""
    }

    try {
        if ($Mode -ne "CopyOnly") {
            return New-Result -Hostname $hostname -Status "skipped" -Step "mode" -Message "InstallViaScheduledTaskRemote is reserved for a future phase." -StartedAt $startedAt -PingOk $false -AdminShareOk $false -CopiedFilesCount 0 -ConfigAction "" -TaskAction "todo" -ErrorMessage ""
        }

        if (-not $SkipPing) {
            $pingOk = Test-Connection -ComputerName $hostname -Count 1 -Quiet -ErrorAction SilentlyContinue
            if (-not $pingOk) {
                return New-Result -Hostname $hostname -Status "error" -Step "ping" -Message "Host unreachable" -StartedAt $startedAt -PingOk $false -AdminShareOk $false -CopiedFilesCount 0 -ConfigAction "" -TaskAction $taskAction -ErrorMessage "Ping failed"
            }
        }
        else {
            $pingOk = $true
        }

        $adminShare = "\\$hostname\C$"
        if (-not (Test-Path $adminShare)) {
            return New-Result -Hostname $hostname -Status "error" -Step "admin_share" -Message "Admin share not accessible" -StartedAt $startedAt -PingOk $pingOk -AdminShareOk $false -CopiedFilesCount 0 -ConfigAction "" -TaskAction $taskAction -ErrorMessage "Cannot access $adminShare"
        }
        $adminShareOk = $true

        $remoteInstall = Convert-ToAdminSharePath -Hostname $hostname -LocalPath $InstallPath
        $remoteLogs = Join-Path $remoteInstall "logs"

        if ($PSCmdlet.ShouldProcess($hostname, "Deploy Night Owl agent files")) {
            New-Item -Path $remoteInstall -ItemType Directory -Force | Out-Null
            New-Item -Path $remoteLogs -ItemType Directory -Force | Out-Null

            $files = @(
                "RmmAgent.ps1",
                "Install-RmmAgent.ps1",
                "Update-RmmAgent.ps1",
                "Uninstall-RmmAgent.ps1",
                "Check-RmmAgent.ps1",
                "VERSION",
                "manifest.json",
                "README.md",
                "RmmAgent.config.example.ps1"
            )

            foreach ($file in $files) {
                $source = Join-Path $SourcePath $file
                if (-not (Test-Path $source)) {
                    Write-DeployLog "[$hostname] Missing source file: $source" "WARN"
                    continue
                }
                Test-ReadableFile -Path $source
                Copy-Item -Path $source -Destination (Join-Path $remoteInstall $file) -Force
                $copied++
            }

            $configPath = Join-Path $remoteInstall "RmmAgent.config.ps1"
            if ((Test-Path $configPath) -and -not $ForceConfig) {
                $configAction = "config_preserved"
            }
            else {
                $serverUrl = [string]$Row.server_url
                $agentToken = [string]$Row.agent_token
                if ([string]::IsNullOrWhiteSpace($serverUrl) -or [string]::IsNullOrWhiteSpace($agentToken)) {
                    return New-Result -Hostname $hostname -Status "warning" -Step "config" -Message "Files copied but config not written because server_url or token is empty." -StartedAt $startedAt -PingOk $pingOk -AdminShareOk $adminShareOk -CopiedFilesCount $copied -ConfigAction "config_missing_token" -TaskAction $taskAction -ErrorMessage ""
                }
                @"
`$RmmServerUrl = "$serverUrl"
`$AgentToken = "$agentToken"
"@ | Set-Content -Path $configPath -Encoding UTF8
                $configAction = if ($ForceConfig) { "config_overwritten" } else { "config_created" }
            }
        }
        else {
            return New-Result -Hostname $hostname -Status "skipped" -Step "whatif" -Message "WhatIf: deploy skipped" -StartedAt $startedAt -PingOk $pingOk -AdminShareOk $adminShareOk -CopiedFilesCount 0 -ConfigAction "whatif" -TaskAction $taskAction -ErrorMessage ""
        }

        $status = if ($configAction -eq "config_preserved") { "warning" } else { "success" }
        $step = if ($configAction -eq "config_preserved") { "config_preserved" } else { "copy_completed" }
        $message = if ($configAction -eq "config_preserved") { "Files copied; existing config preserved." } else { "Files copied and config prepared." }
        return New-Result -Hostname $hostname -Status $status -Step $step -Message $message -StartedAt $startedAt -PingOk $pingOk -AdminShareOk $adminShareOk -CopiedFilesCount $copied -ConfigAction $configAction -TaskAction $taskAction -ErrorMessage ""
    }
    catch {
        return New-Result -Hostname $hostname -Status "error" -Step "copy_failed" -Message "Deploy failed" -StartedAt $startedAt -PingOk $pingOk -AdminShareOk $adminShareOk -CopiedFilesCount $copied -ConfigAction $configAction -TaskAction $taskAction -ErrorMessage $_.Exception.Message
    }
}

if ($MaxConcurrency -ne 1) {
    Write-Warning "MaxConcurrency is accepted for future use; this phase runs sequentially."
}

if (-not (Test-Path $DeployCsv)) {
    throw "DeployCsv not found: $DeployCsv"
}
if (-not (Test-Path $SourcePath)) {
    throw "SourcePath not accessible: $SourcePath"
}

$script:ReportFile = New-ReportRoot
$script:ReportDirectory = Split-Path -Parent $script:ReportFile
if (-not (Test-Path $script:ReportDirectory)) {
    New-Item -Path $script:ReportDirectory -ItemType Directory -Force | Out-Null
}
$script:LogPath = Join-Path $script:ReportDirectory "deploy.log"
Write-DeployLog "Deploy started. mode=$Mode source=$SourcePath csv=$DeployCsv"

$rows = Import-Csv -Path $DeployCsv
$results = foreach ($row in $rows) {
    $result = Copy-AgentToHost -Row $row
    Write-DeployLog "[$($result.hostname)] $($result.status) $($result.step) $($result.message)"
    $result
}

$results | Export-Csv -Path $script:ReportFile -NoTypeInformation -Encoding UTF8
Write-DeployLog "Deploy finished. report=$script:ReportFile duration=$([math]::Round(((Get-Date) - $startedScriptAt).TotalSeconds, 2))s"
Write-Host "Report: $script:ReportFile"
