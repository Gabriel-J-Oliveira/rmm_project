param(
    [string]$ServiceName = "NightOwlAgentDotNet",
    [string]$RootPath = "",
    [string]$InstallPath = "",
    [switch]$Purge,
    [switch]$Force,
    [switch]$NonInteractive,
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"

function New-NightOwlPaths([string]$RequestedRoot, [string]$RequestedInstallPath) {
    $root = if ([string]::IsNullOrWhiteSpace($RequestedRoot)) {
        if ([string]::IsNullOrWhiteSpace($env:NIGHTOWL_HOME)) { "C:\ProgramData\NightOwl" } else { $env:NIGHTOWL_HOME }
    } else {
        $RequestedRoot
    }
    $install = if ([string]::IsNullOrWhiteSpace($RequestedInstallPath)) { Join-Path $root "AgentDotNet" } else { $RequestedInstallPath }
    $state = Join-Path $root "State"
    $logs = Join-Path $root "Logs"
    $diagnostics = Join-Path $root "Diagnostics"
    return [ordered]@{
        Root = $root
        Install = $install
        Config = Join-Path $root "Config"
        Identity = Join-Path $root "Identity"
        State = $state
        Logs = $logs
        Diagnostics = $diagnostics
        Updates = Join-Path $root "Updates"
        Packages = Join-Path $root "Packages"
        Cache = Join-Path $root "Cache"
        ConfigPath = Join-Path (Join-Path $root "Config") "agent.config.json"
        IdentityPath = Join-Path (Join-Path $root "Identity") "agent.identity.json"
        StatePath = Join-Path $state "agent.state.json"
        PendingResultsPath = Join-Path $state "pending-results"
        UpdateStatePath = Join-Path $state "update-state.json"
        LogPath = Join-Path $logs "service-install.log"
    }
}

$script:Paths = New-NightOwlPaths -RequestedRoot $RootPath -RequestedInstallPath $InstallPath
$InstallPath = [string]$script:Paths.Install
$script:Operation = if ($Purge) { "purge" } else { "uninstall" }
$script:LifecycleErrorCodes = @(
    "UNINSTALL_SERVICE_REMOVE_FAILED",
    "UNINSTALL_BACKEND_NOTIFY_FAILED",
    "PURGE_CONFIRMATION_REQUIRED",
    "PURGE_REVOKE_FAILED"
)
$script:Report = [ordered]@{
    operation = $script:Operation
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    completed_at = $null
    status = "running"
    installed_version = ""
    previous_version = ""
    machine_id = ""
    identity_preserved = -not $Purge
    enrollment_performed = $false
    service_status = ""
    actions = New-Object System.Collections.ArrayList
    warnings = New-Object System.Collections.ArrayList
    error_code = ""
    error_message = ""
}

function Write-Step($Status, $Message) {
    Write-Host ("[{0}] {1}" -f $Status, $Message)
}

function Write-UninstallLog($EventType, $Message, $Metadata = @{}) {
    try {
        New-Item -ItemType Directory -Force -Path ([string]$script:Paths.Logs) | Out-Null
        $entry = [ordered]@{
            timestamp = (Get-Date).ToUniversalTime().ToString("o")
            event_type = $EventType
            message = $Message
            metadata = $Metadata
        }
        $entry | ConvertTo-Json -Depth 6 -Compress | Add-Content -Path ([string]$script:Paths.LogPath) -Encoding UTF8
    }
    catch {}
}

function Add-Action([string]$Action, $Metadata = @{}) {
    [void]$script:Report.actions.Add([ordered]@{
        timestamp = (Get-Date).ToUniversalTime().ToString("o")
        action = $Action
        metadata = $Metadata
    })
}

function Add-Warning([string]$Code, [string]$Message, $Metadata = @{}) {
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

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    try { return Get-Content -Path $Path -Raw | ConvertFrom-Json } catch { return $null }
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

function Write-Report([string]$Status, [string]$ErrorCode = "", [string]$ErrorMessage = "") {
    try {
        $script:Report.completed_at = (Get-Date).ToUniversalTime().ToString("o")
        $script:Report.status = $Status
        $script:Report.error_code = $ErrorCode
        $script:Report.error_message = $ErrorMessage
        New-Item -ItemType Directory -Force -Path ([string]$script:Paths.Diagnostics) | Out-Null
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
        $name = "{0}-report-{1}.json" -f $script:Operation, $stamp
        $path = Join-Path ([string]$script:Paths.Diagnostics) $name
        $script:Report | ConvertTo-Json -Depth 8 | Set-Content -Path $path -Encoding UTF8
        Write-UninstallLog "operation.report.written" "Relatorio de desinstalacao gravado." @{ operation = $script:Operation; path = $path; status = $Status; error_code = $ErrorCode }
        if ($NonInteractive) {
            $script:Report | ConvertTo-Json -Depth 8
        }
    }
    catch {}
}

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "INSTALL_ADMIN_REQUIRED: Execute este script em um PowerShell como Administrador."
    }
}

function Assert-PurgeConfirmed {
    if (-not $Purge) { return }
    if ($RemoveData) {
        Add-Warning "PURGE_LEGACY_REMOVEDATA" "-RemoveData foi recebido, mas purge continua exigindo -Purge explicito." @{}
    }
    if ($NonInteractive -and -not $Force) {
        throw "PURGE_CONFIRMATION_REQUIRED: use -Purge -NonInteractive -Force para purge silencioso."
    }
    if (-not $Force) {
        $answer = Read-Host "Purge remove identidade, token, state, logs e exige novo enrollment. Digite NIGHTOWL PURGE para continuar"
        if ($answer -ne "NIGHTOWL PURGE") {
            throw "PURGE_CONFIRMATION_REQUIRED: purge cancelado."
        }
    }
}

function Stop-AndRemoveService {
    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        $script:Report.service_status = [string]$existing.Status
        if ($existing.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            try { $existing.WaitForStatus("Stopped", "00:00:30") } catch {}
        }
        $result = sc.exe delete $ServiceName 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "UNINSTALL_SERVICE_REMOVE_FAILED: sc.exe delete falhou: $result"
        }
        Add-Action "service.removed" @{ service_name = $ServiceName }
        Write-Step "OK" "Servico removido: $ServiceName"
        return
    }
    Add-Action "service.absent" @{ service_name = $ServiceName }
    Write-Step "OK" "Servico nao encontrado: $ServiceName"
}

function Remove-Tray {
    $taskName = "NightOwl Agent Tray"
    try {
        schtasks.exe /Delete /TN $taskName /F | Out-Null
        Add-Action "tray.task.removed" @{ task_name = $taskName }
        Write-Step "OK" "Tarefa de bandeja removida: $taskName"
    }
    catch {
        Add-Action "tray.task.absent" @{ task_name = $taskName }
        Write-Step "OK" "Tarefa de bandeja nao encontrada ou ja removida"
    }
    Get-Process -Name "NightOwl.Agent.Tray" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Add-Action "tray.process.stopped"
}

function Remove-DirectoryIfExists([string]$Path, [string]$ActionName) {
    if (Test-Path $Path) {
        Remove-Item -Path $Path -Recurse -Force
        Add-Action $ActionName @{ path = $Path }
        Write-Step "OK" "Removido: $Path"
    }
    else {
        Add-Action "$ActionName.absent" @{ path = $Path }
    }
}

function Write-UninstalledState {
    if ($Purge) { return }
    try {
        New-Item -ItemType Directory -Force -Path ([string]$script:Paths.State) | Out-Null
        $state = Read-JsonFile ([string]$script:Paths.StatePath)
        if ($null -eq $state) { $state = [pscustomobject]@{} }
        if ($state.PSObject.Properties.Name -notcontains "install_status") {
            $state | Add-Member -NotePropertyName "install_status" -NotePropertyValue "uninstalled"
        } else {
            $state.install_status = "uninstalled"
        }
        if ($state.PSObject.Properties.Name -notcontains "uninstalled_at") {
            $state | Add-Member -NotePropertyName "uninstalled_at" -NotePropertyValue (Get-Date).ToUniversalTime().ToString("o")
        } else {
            $state.uninstalled_at = (Get-Date).ToUniversalTime().ToString("o")
        }
        $state | ConvertTo-Json -Depth 8 | Set-Content -Path ([string]$script:Paths.StatePath) -Encoding UTF8
        Add-Action "state.marked_uninstalled" @{ path = [string]$script:Paths.StatePath }
    }
    catch {
        Add-Warning "UNINSTALL_STATE_WRITE_FAILED" "Nao foi possivel registrar estado uninstalled." @{ error = $_.Exception.Message }
    }
}

try {
    Assert-Elevated
    Assert-PurgeConfirmed

    $config = Read-JsonFile ([string]$script:Paths.ConfigPath)
    $identity = Read-JsonFile ([string]$script:Paths.IdentityPath)
    $machineId = Get-JsonProperty $identity @("machine_id", "machineId")
    if ([string]::IsNullOrWhiteSpace($machineId)) {
        $machineId = Get-JsonProperty $config @("machineId", "machine_id")
    }
    $script:Report.machine_id = Protect-SecretValue $machineId
    $version = Get-JsonProperty (Read-JsonFile (Join-Path ([string]$script:Paths.Install) "agent.version.json")) @("version")
    $script:Report.previous_version = $version
    $script:Report.installed_version = if ($Purge) { "" } else { $version }

    Write-UninstallLog ("operation.{0}.started" -f $script:Operation) "Operacao de remocao iniciada." @{
        operation = $script:Operation
        install_path = [string]$script:Paths.Install
        root = [string]$script:Paths.Root
        purge = [bool]$Purge
    }

    Remove-Tray
    Stop-AndRemoveService
    Remove-DirectoryIfExists -Path ([string]$script:Paths.Install) -ActionName "binaries.removed"

    if ($Purge) {
        foreach ($dir in @(
            [string]$script:Paths.Config,
            [string]$script:Paths.Identity,
            [string]$script:Paths.State,
            [string]$script:Paths.Updates,
            [string]$script:Paths.Diagnostics,
            [string]$script:Paths.Packages,
            [string]$script:Paths.Cache
        )) {
            Remove-DirectoryIfExists -Path $dir -ActionName "persistent.removed"
        }
        Add-Warning "PURGE_REVOKE_FAILED" "Revogacao no backend ainda nao esta implementada neste fluxo local." @{}
        Write-Step "WARN" "Revogacao backend nao implementada neste fluxo local; remova/revogue no painel se necessario."
        Remove-DirectoryIfExists -Path ([string]$script:Paths.Logs) -ActionName "logs.removed"
    }
    else {
        Write-UninstalledState
        Write-Step "OK" "Persistencia preservada: Config, Identity, State, Logs, Diagnostics e Updates"
    }

    Write-Report -Status "completed"
    Write-UninstallLog ("operation.{0}.completed" -f $script:Operation) "Operacao de remocao concluida." @{ operation = $script:Operation; machine_id = (Protect-SecretValue $machineId) }
    Write-Step "OK" ("Operacao concluida: {0}" -f $script:Operation)
}
catch {
    $message = $_.Exception.Message
    $code = if ($message -match "^([A-Z0-9_]+):") { $matches[1] } else { "UNINSTALL_UNEXPECTED_ERROR" }
    Write-UninstallLog ("operation.{0}.failed" -f $script:Operation) "Operacao de remocao falhou." @{ operation = $script:Operation; error_code = $code; error = $message }
    Write-Report -Status "failed" -ErrorCode $code -ErrorMessage $message
    throw
}
