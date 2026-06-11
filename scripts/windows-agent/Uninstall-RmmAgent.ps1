[CmdletBinding()]
param(
    [string]$InstallPath = "C:\RMM",
    [switch]$KeepLogs,
    [switch]$KeepConfig,
    [switch]$RemoveAll,
    [switch]$RunCheck
)

$TaskName = "RMM-Agent-Heartbeat"
$LogDirectory = Join-Path $InstallPath "logs"
$UninstallLog = Join-Path $LogDirectory "uninstall.log"

function Write-UninstallLog {
    param([string]$Message, [string]$Level = "INFO")
    if (Test-Path $LogDirectory) {
        $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        Add-Content -Path $UninstallLog -Value "[$timestamp] [$Level] $Message"
    }
    Write-Host "[$Level] $Message"
}

function Remove-PathIfExists {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        Write-UninstallLog "Already absent: $Path"
        return
    }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-UninstallLog "Removed: $Path"
    }
    catch {
        Write-UninstallLog "Could not remove $Path :: $($_.Exception.Message)" "WARN"
    }
}

try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-UninstallLog "Scheduled task removed: $TaskName"
    }
    else {
        Write-UninstallLog "Scheduled task already absent: $TaskName"
    }

    if ($RemoveAll) {
        Remove-PathIfExists -Path $InstallPath
        Write-Host "[OK] Uninstall complete. RemoveAll requested."
        exit 0
    }

    $filesToRemove = @(
        "RmmAgent.ps1",
        "Install-RmmAgent.ps1",
        "Update-RmmAgent.ps1",
        "Uninstall-RmmAgent.ps1",
        "Check-RmmAgent.ps1",
        "RmmAgent.config.example.ps1",
        "VERSION",
        "manifest.json",
        "README.md",
        "agent.state.json"
    )

    foreach ($file in $filesToRemove) {
        Remove-PathIfExists -Path (Join-Path $InstallPath $file)
    }

    if (-not $KeepConfig) {
        Write-UninstallLog "Config preserved by default. Use -RemoveAll to remove config."
    }
    else {
        Write-UninstallLog "Config preserved by -KeepConfig."
    }

    if (-not $KeepLogs) {
        Write-UninstallLog "Logs preserved by default. Use -RemoveAll to remove logs."
    }
    else {
        Write-UninstallLog "Logs preserved by -KeepLogs."
    }

    if ($RunCheck) {
        $checkScript = Join-Path $InstallPath "Check-RmmAgent.ps1"
        if (Test-Path $checkScript) {
            powershell.exe -NoProfile -ExecutionPolicy Bypass -File $checkScript -InstallPath $InstallPath
        }
        else {
            Write-UninstallLog "RunCheck skipped because Check-RmmAgent.ps1 was removed." "WARN"
        }
    }

    Write-UninstallLog "Uninstall complete."
}
catch {
    Write-UninstallLog "Uninstall failed: $($_.Exception.Message)" "ERROR"
    exit 1
}
