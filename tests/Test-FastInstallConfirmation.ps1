$ErrorActionPreference = 'Stop'
$path = (Resolve-Path (Join-Path $PSScriptRoot '..\agents\bootstrap\nightowl_deployment_bootstrap.ps1')).Path
$errors = $null; $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Invalid bootstrap syntax' }
foreach ($name in @('Get-OptionalJsonProperty','Send-DeploymentCompletion')) {
    $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    Invoke-Expression $fn.Extent.Text
}
$Token = 'synthetic'; $script:response = 'awaiting_heartbeat'; $script:posted = $null
function Read-AgentConfig { [pscustomobject]@{agentToken='synthetic';machineId='fixture'} }
function Get-Service { param($Name,$ErrorAction) [pscustomobject]@{Status='Running'} }
function Invoke-JsonPost { param($Url,$Headers,$Body) $script:posted=$Body; [pscustomobject]@{status=$script:response} }
$meta = [pscustomobject]@{deployment_id='fixture';completion_url='https://example.invalid';release=[pscustomobject]@{version='1'}}
$result = Send-DeploymentCompletion $meta 'completed' '' ''
if ($result.ok -or $result.error -ne 'awaiting_heartbeat') { throw 'Pending incorrectly accepted' }
$script:response = 'pending'; $result = Send-DeploymentCompletion $meta 'pending' '' ''
if (-not $result.ok -or -not $script:posted.health_check_confirmed) { throw 'Timeout report rejected' }
$script:response = 'completed'; $result = Send-DeploymentCompletion $meta 'completed' '' ''
if (-not $result.ok) { throw 'Confirmed response rejected' }
Write-Host 'PASS: simulated pending, timeout report and operational confirmation.'
