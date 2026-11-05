$ErrorActionPreference = 'Stop'
$path = (Resolve-Path (Join-Path $PSScriptRoot '..\NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1')).Path
$errors = $null; $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Installer syntax invalid' }
foreach ($name in @('Test-ProtectedPayloadRelativePath','Test-PayloadUnchanged','Test-NoOpHeartbeat','Test-NoOpServiceBinding')) {
    $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    Invoke-Expression $fn.Extent.Text
}
$branch = $ast.Find({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($existingInstallation -and $InstallAsService -and -not $noOpHasOtherParameters')}, $true)
if (-not $branch) { throw 'No-op branch missing' }
function Assert-RequiredPackageFiles { param($SourceDir, [switch]$RequireTrust) }
function Get-JsonProperty($object, $names) { foreach ($name in $names) { if ($object -and $object.PSObject.Properties.Name -contains $name) { return [string]$object.$name } }; return '' }
function Read-JsonFile($p) { if ($p -eq 'identity') { return [pscustomobject]@{machine_id='fixture-id'} }; return [pscustomobject]@{machine_id='fixture-id';lastHeartbeatAt=[DateTimeOffset]::UtcNow.ToString('o')} }
function Test-MachineId($id) { return $id -eq 'fixture-id' }
function Normalize-ServerUrl($url) { return $url }
function Get-CimInstance { param($ClassName,$ErrorAction) [pscustomobject]@{Name='fixture';PathName=$script:servicePath} }
function Test-AgentLifecycleHealth { if ($script:versionMismatch) { return 'version_metadata_mismatch' }; return @() }
function Get-StartMenuShortcutPath { return 'fixture' }
function Protect-SecretValue($x) { return '[redacted]' }
function Add-ReportAction($name, $fields) { $script:action=$name }
function Write-OperationReport { param($Status) }
function Write-Step($a,$b) {}
function Stop-ServiceIfExists { $script:stops++ }
$root = Join-Path ([IO.Path]::GetTempPath()) ('nightowl-fi02b-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
try {
    $sourcePath=Join-Path $root 'stage'; $InstallPath=Join-Path $root 'install'
    New-Item -ItemType Directory -Path $sourcePath,$InstallPath | Out-Null
    [IO.File]::WriteAllText((Join-Path $sourcePath 'agent.exe'), 'payload')
    [IO.File]::WriteAllText((Join-Path $InstallPath 'agent.exe'), 'payload')
    $existingInstallation=$true; $InstallAsService=$true; $noOpHasOtherParameters=$false
    $script:ValidatedReleaseMetadata=@{version='1'}; $script:NightOwlPaths=@{IdentityPath='identity'}
    $script:Report=[pscustomobject]@{machine_id='';identity_preserved=$false;previous_version='';installed_version=''}
    $preservedConfig=[pscustomobject]@{machineId='fixture-id'}; $statePath='state'; $configPath='config'; $legacyConfigPath='legacy'
    $existingToken='fixture-token'; $existingServer='https://example.invalid'; $serverBase=$existingServer; $serverChangedExplicitly=$false
    $EnrollmentToken=''; $AgentToken=''; $ManualValidationToken=''; $ServiceName='fixture'; $releaseMetadata=@{version='1'}
    $script:servicePath = '"' + (Join-Path $InstallPath 'NightOwl.Agent.Windows.exe') + '"'
    $script:stops=0; $script:action=''; $script:versionMismatch=$false
    $execute = [scriptblock]::Create($branch.Extent.Text + "`nStop-ServiceIfExists")
    & $execute
    if ($script:stops -ne 0 -or $script:action -ne 'operation.unchanged') { throw 'Healthy fixture did not take no-op' }
    $script:servicePath = '"C:\other\NightOwl.Agent.Windows.exe"'
    & $execute
    if ($script:stops -ne 1) { throw 'Different service binary incorrectly accepted' }
    $script:stops=0; $script:servicePath = '"' + (Join-Path $InstallPath 'NightOwl.Agent.Windows.exe') + '"'
    $script:versionMismatch=$true; & $execute
    if ($script:stops -ne 1) { throw 'New version incorrectly skipped' }
    $script:stops=0; $script:versionMismatch=$false
    $noOpHasOtherParameters=$true; & $execute
    if ($script:stops -ne 1) { throw 'Explicit lifecycle change incorrectly skipped' }
    $script:stops=0; $noOpHasOtherParameters=$false
    [IO.File]::WriteAllText((Join-Path $InstallPath 'agent.exe'), 'damaged')
    $script:action=''; & $execute
    if ($script:stops -ne 1 -or $script:action -eq 'operation.unchanged') { throw 'Damaged fixture did not take repair path' }
    [IO.File]::Delete((Join-Path $InstallPath 'agent.exe'))
    & $execute
    if ($script:stops -ne 2) { throw 'Missing file did not take repair path' }
    $stale=[pscustomobject]@{machine_id='fixture-id';lastHeartbeatAt=[DateTimeOffset]::UtcNow.AddMinutes(-11).ToString('o')}
    if (Test-NoOpHeartbeat $stale 'fixture-id') { throw 'Stale heartbeat accepted' }
    Write-Host 'PASS: healthy no-op does not stop service; damaged/missing payload falls through; stale heartbeat rejected.'
} finally {
    if ((Split-Path -Parent $root) -eq ([IO.Path]::GetTempPath().TrimEnd('\')) -and (Split-Path -Leaf $root) -like 'nightowl-fi02b-*') { Remove-Item -LiteralPath $root -Recurse -Force }
}
