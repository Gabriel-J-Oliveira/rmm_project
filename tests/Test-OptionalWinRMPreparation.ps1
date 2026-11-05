$ErrorActionPreference = 'Stop'
$path = (Resolve-Path (Join-Path $PSScriptRoot '..\NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1')).Path
$errors=$null; $ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$null,[ref]$errors)
if ($errors.Count) { throw 'Installer syntax invalid' }
$fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-OptionalWinRMPreparation'},$true)
Invoke-Expression $fn.Extent.Text
$script:policy=$false; $script:running=$true; $script:listener=$true; $script:created=0; $script:arguments=$null; $script:existing=$null; $script:activeRules=@(); $script:failInspection=$false
function Test-Path { param($Path) return $script:policy }
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) [pscustomobject]@{State= $(if ($script:running) {'Running'} else {'Stopped'});StartMode='Auto'} }
function Get-NetConnectionProfile { param($ErrorAction) [pscustomobject]@{NetworkCategory='DomainAuthenticated'} }
function Get-ChildItem { param($Path,$LiteralPath,$ErrorAction)
    if ($Path -and $script:listener) { return [pscustomobject]@{PSPath='listener'} }
    if ($LiteralPath -eq 'listener') { return @([pscustomobject]@{Name='Transport';Value='HTTPS'},[pscustomobject]@{Name='Port';Value='5986'},[pscustomobject]@{Name='Enabled';Value='true'},[pscustomobject]@{Name='CertificateThumbprint';Value=('a' * 40)}) }
}
function Get-Item { param($LiteralPath,$ErrorAction) [pscustomobject]@{HasPrivateKey=$true;NotBefore=(Get-Date).AddDays(-1);NotAfter=(Get-Date).AddDays(1)} }
function Get-NetFirewallRule { param($Name,$PolicyStore,$Enabled,$Direction,$Action,$ErrorAction) if ($script:failInspection) { throw 'simulated failure' }; if ($PolicyStore) { return $script:activeRules }; return $script:existing }
function Get-NetFirewallPortFilter { [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{Protocol='TCP';LocalPort='5986'} } }
function Get-NetFirewallAddressFilter { [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{RemoteAddress=$Rule.RemoteAddress} } }
function New-NetFirewallRule { param($Name,$DisplayName,$Direction,$Action,$Enabled,$Profile,$Protocol,$LocalPort,$RemoteAddress,$ErrorAction)
    $script:created++; $script:arguments=@{Profile=$Profile;RemoteAddress=$RemoteAddress;LocalPort=$LocalPort}
}
if ((Invoke-OptionalWinRMPreparation @('0.0.0.0/0')) -ne 'invalid_sources') { throw 'Unrestricted source accepted' }
$script:policy=$true
if ((Invoke-OptionalWinRMPreparation @('10.0.0.10')) -ne 'policy_managed' -or $script:created) { throw 'Policy conflict not blocked' }
$script:policy=$false; $script:running=$false
if ((Invoke-OptionalWinRMPreparation @('10.0.0.10')) -ne 'service_not_ready') { throw 'Stopped service not detected' }
$script:running=$true; $script:listener=$false
if ((Invoke-OptionalWinRMPreparation @('10.0.0.10')) -ne 'https_listener_required') { throw 'Missing listener not detected' }
$script:listener=$true
$script:activeRules=@([pscustomobject]@{Profile='Domain';RemoteAddress='Any'})
if ((Invoke-OptionalWinRMPreparation @('10.0.0.10')) -ne 'firewall_scope_conflict' -or $script:created) { throw 'Broad existing firewall rule not detected' }
$script:activeRules=@()
$script:failInspection=$true
if ((Invoke-OptionalWinRMPreparation @('10.0.0.10')) -ne 'inspection_or_preparation_failed') { throw 'Optional inspection error was not contained' }
$script:failInspection=$false
if ((Invoke-OptionalWinRMPreparation @('10.0.0.10','10.2.0.0/16')) -ne 'firewall_prepared') { throw 'Restricted preparation failed' }
if ($script:created -ne 1 -or $script:arguments.Profile -ne 'Domain' -or $script:arguments.LocalPort -ne 5986 -or ($script:arguments.RemoteAddress -join ',') -ne '10.0.0.10/32,10.2.0.0/16') { throw 'Firewall scope incorrect' }
$script:existing=[pscustomobject]@{Enabled='True';Direction='Inbound';Action='Allow';Profile='Domain';RemoteAddress=@('10.0.0.10/32','10.2.0.0/16')}
if ((Invoke-OptionalWinRMPreparation @('10.0.0.10','10.2.0.0/16')) -ne 'already_configured' -or $script:created -ne 1) { throw 'Existing configuration should be unchanged' }
if ($ast.Extent.Text.IndexOf('if ($PrepareWinRM)') -ge $ast.Extent.Text.IndexOf('$noOpAllowedParameters =')) { throw 'Preparation must precede no-op' }
if ($ast.Extent.Text -notmatch "'PrepareWinRM','WinRMAllowedSources'") { throw 'Preparation must remain compatible with no-op' }
Write-Host 'PASS: restricted sources, policy, stopped service, missing listener and scoped firewall preparation; Windows controls simulated.'
