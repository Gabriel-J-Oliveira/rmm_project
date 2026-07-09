[CmdletBinding()]
param(
    [string]$BasePath = "C:\ProgramData\NightOwl",
    [string]$NssmPath = "",
    [switch]$RemoveProgramData,
    [switch]$KeepLogs,
    [switch]$KeepConfig
)

$ErrorActionPreference = "Stop"
$ServiceName = "NightOwlAgent"
$AgentPath = Join-Path $BasePath "Agent"
$LogPath = Join-Path $BasePath "Logs"
$UninstallLog = Join-Path $LogPath "service-uninstall.log"

function Write-ServiceUninstallLog {
    param([string]$Message, [string]$Level = "INFO")
    if (-not (Test-Path $LogPath)) {
        New-Item -Path $LogPath -ItemType Directory -Force | Out-Null
    }
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $UninstallLog -Value "[$timestamp] [$Level] $Message"
    Write-Host "[$Level] $Message"
}

function Resolve-NssmPath {
    if (-not [string]::IsNullOrWhiteSpace($NssmPath) -and (Test-Path $NssmPath)) {
        return (Resolve-Path $NssmPath).Path
    }
    $command = Get-Command "nssm.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    foreach ($candidate in @((Join-Path $AgentPath "nssm.exe"), "C:\Program Files\nssm\nssm.exe", "C:\nssm\nssm.exe")) {
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    }
    return ""
}

function Remove-PathIfExists {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    Remove-Item -LiteralPath $Path -Recurse -Force
    Write-ServiceUninstallLog "Removed: $Path"
}

try {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
        $nssm = Resolve-NssmPath
        if (-not [string]::IsNullOrWhiteSpace($nssm)) {
            & $nssm remove $ServiceName confirm | Out-Null
            Write-ServiceUninstallLog "Service removed with NSSM: $ServiceName"
        }
        else {
            sc.exe delete $ServiceName | Out-Null
            Write-ServiceUninstallLog "Service removed with sc.exe: $ServiceName"
        }
    }
    else {
        Write-ServiceUninstallLog "Service already absent: $ServiceName"
    }

    if ($RemoveProgramData) {
        Remove-PathIfExists -Path $BasePath
        Write-ServiceUninstallLog "ProgramData removed by -RemoveProgramData."
        exit 0
    }

    $filesToRemove = @(
        "RmmAgentService.ps1",
        "Install-RmmAgentService.ps1",
        "Uninstall-RmmAgentService.ps1",
        "RmmAgent.config.json.example",
        "VERSION",
        "manifest.json",
        "README.md"
    )
    foreach ($file in $filesToRemove) {
        Remove-PathIfExists -Path (Join-Path $AgentPath $file)
    }

    if (-not $KeepConfig) {
        Write-ServiceUninstallLog "Service config preserved by default. Use -RemoveProgramData to remove it."
    }
    if (-not $KeepLogs) {
        Write-ServiceUninstallLog "Service logs preserved by default. Use -RemoveProgramData to remove them."
    }

    Write-ServiceUninstallLog "NightOwl service uninstall complete."
}
catch {
    Write-ServiceUninstallLog "Service uninstall failed: $($_.Exception.Message)" "ERROR"
    exit 1
}
