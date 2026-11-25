[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param()

$ErrorActionPreference = 'Stop'
$ConfigPath = 'C:\ProgramData\NightOwl\Config\agent.config.json'
$ExpectedMachineId = '1eca4af2-4950-4a2d-9b9f-fa7010a62464'
$serviceName = 'Spooler'
$allowedOperations = @('start_service', 'stop_service', 'restart_service')
$temporaryPath = $null

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'PowerShell 7 or later is required.'
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw 'NightOwl agent configuration file was not found.'
}
$resolvedPath = (Resolve-Path -LiteralPath $ConfigPath).Path
if ((Get-Item -LiteralPath $resolvedPath -Force).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
    throw 'Refusing to modify a reparse-point configuration file.'
}
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell session.'
}

$originalJson = [IO.File]::ReadAllText($resolvedPath)
$config = ConvertFrom-Json -InputObject $originalJson -AsHashtable
if ($config.machineId -ne $ExpectedMachineId) {
    throw 'Machine ID does not match the authorized pilot.'
}
if ($config.operationalActionsEnabled -isnot [bool] -or -not $config.operationalActionsEnabled -or
    $config.administrativeActionsEnabled -isnot [bool] -or -not $config.administrativeActionsEnabled) {
    throw 'Operational/admin action gates are not enabled in this endpoint configuration.'
}
if (($config.allowedJobTypes -isnot [array] -and $config.allowedJobTypes -isnot [System.Collections.IList]) -or
    @($config.allowedJobTypes) -cnotcontains 'operational_action') {
    throw 'The agent does not allow operational_action jobs.'
}

if ($null -eq $config.allowedOperationalActions) { $config.allowedOperationalActions = @() }
if ($config.allowedOperationalActions -isnot [array] -and
    $config.allowedOperationalActions -isnot [System.Collections.IList]) {
    throw 'allowedOperationalActions has an unexpected format.'
}
if ($null -eq $config.allowedServiceOperations) { $config.allowedServiceOperations = [ordered]@{} }
if ($config.allowedServiceOperations -isnot [System.Collections.IDictionary]) {
    throw 'allowedServiceOperations has an unexpected format.'
}
$configuredServiceNames = @($config.allowedServiceOperations.Keys | ForEach-Object { [string]$_ })
if ($configuredServiceNames.Count -gt 1 -or @($configuredServiceNames | Where-Object { $_ -ine $serviceName }).Count -gt 0) {
    throw 'A non-Spooler service is already present; refusing to broaden or replace its allowlist.'
}

$before = ConvertTo-Json -InputObject $config -Depth 100 -Compress
foreach ($operation in $allowedOperations) {
    if (@($config.allowedOperationalActions) -cnotcontains $operation) {
        $config.allowedOperationalActions = @($config.allowedOperationalActions) + $operation
    }
}
$config.allowedServiceOperations = [ordered]@{
    Spooler = $allowedOperations
}
$after = ConvertTo-Json -InputObject $config -Depth 100 -Compress
if ($before -ceq $after) {
    Write-Output 'Configuration already contains the pilot-only Spooler action allowlist; no file change made.'
    return
}

if (-not $PSCmdlet.ShouldProcess($resolvedPath, 'Enable start/stop/restart actions for Spooler on this pilot only')) {
    return
}

$service = Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction Stop
if ($service.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
    throw 'NightOwlAgentDotNet must be running. No configuration was written.'
}
$originalAcl = Get-Acl -LiteralPath $resolvedPath
$directory = [IO.Path]::GetDirectoryName($resolvedPath)
$temporaryPath = Join-Path $directory ('.agent.config.' + [Guid]::NewGuid().ToString('N') + '.tmp')
try {
    [IO.File]::Open($temporaryPath, [IO.FileMode]::CreateNew).Dispose()
    Set-Acl -LiteralPath $temporaryPath -AclObject $originalAcl
    [IO.File]::WriteAllText($temporaryPath, ($after + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    $check = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($temporaryPath)) -AsHashtable
    if ($check.machineId -ne $ExpectedMachineId -or
        @($check.allowedServiceOperations.Keys).Count -ne 1 -or
        (@($check.allowedServiceOperations[$serviceName]) -join ',') -cne ($allowedOperations -join ',') -or
        [IO.File]::ReadAllText($resolvedPath) -cne $originalJson) {
        throw 'Post-write validation failed; original configuration remains unchanged.'
    }
    [IO.File]::Move($temporaryPath, $resolvedPath, $true)
    $temporaryPath = $null
    Write-Output 'Pilot-only Spooler action allowlist updated. No job was created and the Spooler was not controlled.'
} finally {
    if ($temporaryPath -and (Test-Path -LiteralPath $temporaryPath)) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}

Restart-Service -Name 'NightOwlAgentDotNet'
(Get-Service -Name 'NightOwlAgentDotNet').WaitForStatus(
    [System.ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(30))
Write-Output 'NightOwlAgentDotNet restarted. Confirm genuine service capabilities on the next heartbeat.'
