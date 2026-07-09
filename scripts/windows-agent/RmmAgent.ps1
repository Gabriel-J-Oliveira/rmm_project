[CmdletBinding()]
param(
    [string]$BasePath = "C:\ProgramData\NightOwl",
    [string]$ConfigPath = "",
    [string]$LegacyConfigPath = "",
    [switch]$RunOnce,
    [switch]$RunJobsOnce,
    [switch]$DebugMode
)

$ErrorActionPreference = "Stop"

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$serviceScript = Join-Path $scriptDirectory "RmmAgentService.ps1"

if ([string]::IsNullOrWhiteSpace($LegacyConfigPath)) {
    $LegacyConfigPath = Join-Path $scriptDirectory "RmmAgent.config.ps1"
}

if (-not (Test-Path $serviceScript)) {
    $programDataServiceScript = Join-Path $BasePath "Agent\RmmAgentService.ps1"
    if (Test-Path $programDataServiceScript) {
        $serviceScript = $programDataServiceScript
    }
}

if (-not (Test-Path $serviceScript)) {
    Write-Error "RmmAgentService.ps1 nao encontrado. Verifique se o pacote contem RmmAgentService.ps1 ou se o servico foi instalado em $BasePath."
    exit 1
}

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $serviceScript,
    "-BasePath", $BasePath,
    "-LegacyConfigPath", $LegacyConfigPath
)

if (-not [string]::IsNullOrWhiteSpace($ConfigPath)) {
    $arguments += @("-ConfigPath", $ConfigPath)
}

if ($RunJobsOnce) {
    $arguments += "-RunJobsOnce"
}
else {
    # Compatibility mode for the legacy scheduled task: one fast agent iteration.
    $arguments += "-RunOnce"
}

if ($DebugMode) {
    $arguments += "-DebugMode"
}

& powershell.exe @arguments
exit $LASTEXITCODE
