param(
    [string]$ServiceName = "NightOwlAgentDotNet",
    [string]$InstallPath = "C:\ProgramData\NightOwl\AgentDotNet",
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"

function Write-Step($Status, $Message) {
    Write-Host ("[{0}] {1}" -f $Status, $Message)
}

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Execute este desinstalador em um PowerShell como Administrador."
    }
}

function Write-UninstallLog($EventType, $Message, $Metadata = @{}) {
    try {
        $logDir = "C:\ProgramData\NightOwl\Logs"
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $entry = [ordered]@{
            timestamp = (Get-Date).ToUniversalTime().ToString("o")
            event_type = $EventType
            message = $Message
            metadata = $Metadata
        }
        $entry | ConvertTo-Json -Depth 6 -Compress | Add-Content -Path (Join-Path $logDir "service-install.log") -Encoding UTF8
    }
    catch {}
}

Assert-Elevated
Write-UninstallLog "service.uninstall.started" "Removendo servico .NET." @{
    service_name = $ServiceName
    install_path = $InstallPath
    remove_data = [bool]$RemoveData
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Status -ne "Stopped") {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        $existing.WaitForStatus("Stopped", "00:00:20")
    }
    sc.exe delete $ServiceName | Out-Null
    Write-Step "OK" "Servico removido: $ServiceName"
}
else {
    Write-Step "OK" "Servico nao encontrado: $ServiceName"
}

if ($RemoveData) {
    if (Test-Path $InstallPath) {
        Remove-Item -Path $InstallPath -Recurse -Force
        Write-Step "OK" "Dados do AgentDotNet removidos: $InstallPath"
    }
    else {
        Write-Step "OK" "Diretorio AgentDotNet nao encontrado: $InstallPath"
    }
    Write-Step "OK" "Logs, Packages, Cache e o agente PowerShell legado foram preservados."
}
else {
    Write-Step "OK" "Dados preservados em $InstallPath. Use -RemoveData para remover apenas AgentDotNet."
}

Write-UninstallLog "service.uninstall.completed" "Servico .NET removido." @{
    service_name = $ServiceName
    install_path = $InstallPath
    remove_data = [bool]$RemoveData
}
Write-Step "OK" "Desinstalacao concluida"
