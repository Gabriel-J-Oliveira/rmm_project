$ErrorActionPreference = 'Stop'
$path = (Resolve-Path (Join-Path $PSScriptRoot '..\NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1')).Path
$errors = $null; $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Invalid installer syntax' }
foreach ($name in @('Test-ProtectedPayloadRelativePath','Copy-AgentBinaries','New-InstallBinaryBackup','Restore-InstallBinaryBackup')) {
    $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    Invoke-Expression $fn.Extent.Text
}
$TrustLocalPackage = $false; $script:lastResult = $null; $script:invalidStage = $false
function Assert-RequiredPackageFiles { param($SourceDir, [switch]$RequireTrust) if ($script:invalidStage) { throw 'synthetic stage invalid' } }
function Add-ReportAction { param($Name, $Fields) $script:lastResult = $Fields }
function Write-InstallLog { param($a,$b,$c) }
function Stop-TrayIfExists { param($RootPath) }
function Stop-ServiceIfExists { param($Name) }
function Wait-NightOwlProcessesToExit { param($RootPath,$TimeoutSeconds) return $true }
$root = Join-Path ([IO.Path]::GetTempPath()) ('nightowl-fi02a-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
try {
    $src = Join-Path $root 'stage'; $dst = Join-Path $root 'install'
    New-Item -ItemType Directory -Path $src,$dst | Out-Null
    [IO.File]::WriteAllText((Join-Path $src 'agent.exe'), 'payload')
    [IO.File]::WriteAllText((Join-Path $src 'agent.config.json'), 'wrong')
    [IO.File]::WriteAllText((Join-Path $dst 'agent.config.json'), 'identity-and-enrollment')
    Copy-AgentBinaries $src $dst
    if ($script:lastResult.copied -ne 1 -or [IO.File]::ReadAllText((Join-Path $dst 'agent.exe')) -ne 'payload') { throw 'Initial application failed' }
    Copy-AgentBinaries $src $dst
    if ($script:lastResult.unchanged -ne 1 -or $script:lastResult.copied -ne 0) { throw 'Idempotence failed' }
    [IO.File]::WriteAllText((Join-Path $src 'agent.exe'), 'PAYLOAD')
    Copy-AgentBinaries $src $dst
    if ($script:lastResult.copied -ne 1 -or [IO.File]::ReadAllText((Join-Path $dst 'agent.exe')) -ne 'PAYLOAD') { throw 'Changed file not applied' }
    if ([IO.File]::ReadAllText((Join-Path $dst 'agent.config.json')) -ne 'identity-and-enrollment') { throw 'Protected configuration changed' }
    $script:NightOwlPaths = @{UpdatesBackup=(Join-Path $root 'backups')}
    $script:Report = [pscustomobject]@{rollback_performed=$false;rollback_success=$false}
    $backup = New-InstallBinaryBackup $dst
    [IO.File]::WriteAllText((Join-Path $dst 'agent.exe'), 'partial replacement')
    Restore-InstallBinaryBackup -BackupPath $backup -Path $dst -Name 'fixture'
    if (-not $script:Report.rollback_success -or [IO.File]::ReadAllText((Join-Path $dst 'agent.exe')) -ne 'PAYLOAD' -or
        [IO.File]::ReadAllText((Join-Path $dst 'agent.config.json')) -ne 'identity-and-enrollment') { throw 'Rollback failed' }
    $script:invalidStage = $true
    try { Copy-AgentBinaries $src $dst; throw 'Invalid stage accepted' } catch { if ($_.Exception.Message -eq 'Invalid stage accepted') { throw } }
    Write-Host 'PASS: package copy, unchanged payload, protected configuration, invalid stage and rollback fixture.'
} finally {
    if ((Split-Path -Parent $root) -eq ([IO.Path]::GetTempPath().TrimEnd('\')) -and (Split-Path -Leaf $root) -like 'nightowl-fi02a-*') { Remove-Item -LiteralPath $root -Recurse -Force }
}
