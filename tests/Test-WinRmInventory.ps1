$ErrorActionPreference='Stop'
$source=Get-Content (Join-Path $PSScriptRoot '..\NightOwl.Agent.Windows\Collectors\WindowsInventoryCollector.cs') -Raw
$match=[regex]::Match($source, '(?s)result = RunPowerShellObject\("""\r?\n(.*?)\r?\n""", 12\);')
if (-not $match.Success) { throw 'Collector script missing' }
$script:partial=$false
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) [pscustomobject]@{State='Stopped';StartMode='Auto'} }
function Get-ChildItem { param($Path,$LiteralPath,$ErrorAction) if ($script:partial) { throw 'fixture listener unavailable' }; return @() }
function Get-NetConnectionProfile { param($ErrorAction) [pscustomobject]@{NetworkCategory='DomainAuthenticated'} }
function Get-NetFirewallRule { param($PolicyStore,$Enabled,$Direction,$Action,$ErrorAction) [pscustomobject]@{Profile='Domain';Action='Allow';RemoteAddress=@('10.0.0.0/24')} }
function Get-NetFirewallPortFilter { [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{Protocol='TCP';LocalPort='5986'} } }
function Get-NetFirewallAddressFilter { [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{RemoteAddress=$Rule.RemoteAddress} } }
function Test-Path { param($Path) return $false }
$scriptBlock=[scriptblock]::Create($match.Groups[1].Value)
$full=& $scriptBlock
if ($full.result -ne 'ok' -or $full.service.state -ne 'Stopped' -or $full.listeners.Count -ne 0 -or $full.profiles[0] -ne 'DomainAuthenticated' -or $full.firewall[0].remote_addresses[0] -ne '10.0.0.0/24') { throw 'Complete collection incorrect' }
$script:partial=$true
$partial=& $scriptBlock
if ($partial.result -ne 'partial' -or 'listener_unavailable' -notin $partial.errors -or $partial.service.state -ne 'Stopped') { throw 'Partial collection failed' }
Write-Host 'WinRM inventory fixtures passed; Windows configuration was simulated.'
