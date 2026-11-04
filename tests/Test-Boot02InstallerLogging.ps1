$ErrorActionPreference='Stop'
$path=(Resolve-Path (Join-Path $PSScriptRoot '..\NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1')).Path
$errors=$null; $ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$null,[ref]$errors)
if ($errors.Count) { throw 'Installer syntax invalid' }
foreach ($name in @('Write-NightOwlBoundedLog','Write-InstallLog','Write-Step','Write-OperationReport')) {
    $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
    Invoke-Expression $fn.Extent.Text
}
$root=Join-Path ([IO.Path]::GetTempPath()) ('nightowl-boot02-logs-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$script:NightOwlPaths=@{Logs=$root;InstallLog=(Join-Path $root 'installer.log');Diagnostics=(Join-Path $root 'diagnostics')}
$EnrollmentToken='deploy_' + 'sensitive_fixture_123456'; $AgentToken='rmm_live_' + 'fixture_token_123'; $ManualValidationToken='manual-secret-fixture'
$script:Operation='install'; $ServiceName='fixture'
$script:Report=[pscustomobject]@{completed_at='';status='';error_code='';error_message='';service_status=''}
function Get-Service { param($Name,$ErrorAction) return $null }
$env:NIGHTOWL_INSTALLER_SILENT='1'
try {
    $captured=@(Write-Step 'OK' 'Validacao concluida' *>&1)
    if ($captured.Count) { throw 'Silent installer emitted routine output' }
    Write-InstallLog 'fixture' 'synthetic diagnostic' @{details="failure $EnrollmentToken and $AgentToken and $ManualValidationToken"}
    $reportOutput=@(Write-OperationReport -Status 'failed' -ErrorCode 'TEST' -ErrorMessage "failure $EnrollmentToken $AgentToken $ManualValidationToken")
    if ($reportOutput.Count) { throw 'Silent installer emitted operation report' }
    $log=Get-Content -Raw -LiteralPath $script:NightOwlPaths.InstallLog
    if ($log.Contains($EnrollmentToken) -or $log.Contains($AgentToken) -or $log.Contains($ManualValidationToken)) { throw 'Secret leaked to installer log' }
    if ($log -notmatch 'Validacao concluida' -or $log -notmatch '\[REDACTED\]') { throw 'Diagnostic or redaction missing' }
    $report=Get-Content -Raw -LiteralPath (Get-ChildItem -LiteralPath $script:NightOwlPaths.Diagnostics -Filter '*report*.json').FullName
    if ($report.Contains($EnrollmentToken) -or $report.Contains($AgentToken) -or $report.Contains($ManualValidationToken)) { throw 'Secret leaked to operation report' }
    Write-Host 'PASS: silent installer step and log redaction.'
} finally {
    Remove-Item Env:\NIGHTOWL_INSTALLER_SILENT -ErrorAction SilentlyContinue
    $resolvedRoot=(Resolve-Path -LiteralPath $root).Path; $resolvedTemp=(Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path
    if ((Split-Path -Parent $resolvedRoot) -eq $resolvedTemp -and (Split-Path -Leaf $resolvedRoot) -like 'nightowl-boot02-logs-*') { Remove-Item -LiteralPath $resolvedRoot -Recurse -Force }
}
