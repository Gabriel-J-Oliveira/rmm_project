#requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Configure-NightOwlWinRMHttpsPilot.ps1')
$firewallValidator = ${function:Test-PilotFirewall}

function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
function New-FakeCertificate {
    [pscustomobject]@{ Thumbprint = 'ABC123'; Subject = 'CN=cs-rdp-02.control.local'; Issuer = 'CN=CorporateCA'
        HasPrivateKey = $true; NotBefore = (Get-Date).AddDays(-1); NotAfter = (Get-Date).AddDays(30)
        EnhancedKeyUsageList = @([pscustomobject]@{ ObjectId = [pscustomobject]@{ Value = '1.3.6.1.5.5.7.3.1' } })
        DnsNameList = @([pscustomobject]@{ Unicode = 'cs-rdp-02.control.local' }) }
}
function Reset-Fakes {
    $script:mutations = 0; $script:enrollments = 0; $script:chainValid = $true
    $script:localOpen = $true; $script:wrongHost = $false
    $script:certs = @(New-FakeCertificate); $script:listeners = @(); $script:rules = @()
    $script:ruleValid = $true; $script:removedListener = 0; $script:removedFirewall = 0
}
function Get-PilotIdentity {
    [pscustomobject]@{ Fqdn = $(if ($script:wrongHost) { 'another.control.local' } else { 'CS-RDP-02.CONTROL.LOCAL' }) }
}
function Test-PilotAdministrator { $true }
function Get-PilotService { [pscustomobject]@{ Status = 'Running' } }
function Get-PilotCertificates { $script:certs }
function Test-PilotChain($Certificate) { $script:chainValid }
function Get-PilotListeners { $script:listeners }
function Get-PilotFirewall { $script:rules }
function Get-PilotRelatedFirewall { @() }
function Test-PilotFirewall($Rule) { $script:ruleValid }
function Test-PilotLocalPort { $script:localOpen }
function Invoke-PilotEnrollment {
    $script:mutations++; $script:enrollments++; $script:certs = @(New-FakeCertificate)
}
function New-PilotListener($Thumbprint) {
    $script:mutations++
    $script:listeners = @([pscustomobject]@{ Address = '*'; Transport = 'HTTPS'; Hostname = $script:PilotFqdn
        CertificateThumbprint = $Thumbprint; Port = 5986; Enabled = 'true'; ListeningOn = @('192.168.104.33') })
}
function New-PilotFirewall { $script:mutations++; $script:rules = @([pscustomobject]@{ Name = $script:RuleName }) }
function Remove-PilotListener { $script:removedListener++; $script:listeners = @() }
function Remove-PilotFirewall { $script:removedFirewall++; $script:rules = @() }

$passed = 0
function Get-NetFirewallPortFilter { process { [pscustomobject]@{ Protocol = 'TCP'; LocalPort = $script:testPort } } }
function Get-NetFirewallAddressFilter { process { [pscustomobject]@{ RemoteAddress = @($script:testSource) } } }
$script:testPort = '5986'; $script:testSource = $script:SourceIp
$testRule = [pscustomobject]@{ Name = $script:RuleName; Group = $script:RuleGroup; Direction = 'Inbound'
    Action = 'Allow'; Enabled = 'True'; Profile = 'Domain' }
Assert (& $firewallValidator $testRule) 'real firewall validator accepts exact scope'; $passed++
foreach ($badScope in @('Any','Private','5985','OtherGroup')) {
    $script:testSource = $script:SourceIp; $script:testPort = '5986'; $testRule.Profile = 'Domain'; $testRule.Group = $script:RuleGroup
    switch ($badScope) {
        'Any' { $script:testSource = 'Any' }
        'Private' { $testRule.Profile = 'Private' }
        '5985' { $script:testPort = '5985' }
        'OtherGroup' { $testRule.Group = 'Other' }
    }
    Assert (-not (& $firewallValidator $testRule)) "reject firewall $badScope"; $passed++
}
Reset-Fakes
$script:wrongHost = $true
$r = Invoke-PilotBootstrap
Assert ($r.ERROR_CODE -eq 'WRONG_HOST' -and $script:mutations -eq 0) 'wrong-host guard'; $passed++

Reset-Fakes
Assert (Test-PilotCertificate $script:certs[0]) 'valid certificate'; $passed++
foreach ($kind in @('expired','wrong-san','missing-eku','no-key','self-signed','bad-chain')) {
    Reset-Fakes
    switch ($kind) {
        'expired' { $script:certs[0].NotAfter = (Get-Date).AddDays(-1) }
        'wrong-san' { $script:certs[0].DnsNameList[0].Unicode = 'other.control.local' }
        'missing-eku' { $script:certs[0].EnhancedKeyUsageList = @() }
        'no-key' { $script:certs[0].HasPrivateKey = $false }
        'self-signed' { $script:certs[0].Issuer = $script:certs[0].Subject }
        'bad-chain' { $script:chainValid = $false }
    }
    Assert (-not (Test-PilotCertificate $script:certs[0])) "reject $kind"; $passed++
}
Reset-Fakes
$script:certs += New-FakeCertificate
$r = Invoke-PilotBootstrap
Assert ($r.ERROR_CODE -eq 'AMBIGUOUS_CERTIFICATES' -and $script:mutations -eq 0) 'ambiguity'; $passed++

Reset-Fakes
$script:certs = @()
$r = Invoke-PilotBootstrap
Assert ($r.FINAL_STATUS -eq 'PILOT_WINRM_HTTPS_CONFIGURED' -and $script:enrollments -eq 1) 'enrollment'; $passed++
$before = $script:mutations
$r = Invoke-PilotBootstrap
Assert ($r.FINAL_STATUS -eq 'ALREADY_CONFIGURED' -and $script:mutations -eq $before -and $script:enrollments -eq 1) 'second run'; $passed++

Reset-Fakes
New-PilotListener 'DIFFERENT'
$script:mutations = 0
$r = Invoke-PilotBootstrap
Assert ($r.ERROR_CODE -eq 'HTTPS_LISTENER_CONFLICT' -and $script:mutations -eq 0) 'listener conflict'; $passed++

Reset-Fakes
$script:rules = @([pscustomobject]@{ Name = $script:RuleName }); $script:ruleValid = $false
$r = Invoke-PilotBootstrap
Assert ($r.ERROR_CODE -eq 'FIREWALL_CONFLICT' -and $script:mutations -eq 0) 'firewall conflict'; $passed++

Reset-Fakes
$script:localOpen = $false
$r = Invoke-PilotBootstrap
Assert ($r.FINAL_STATUS -eq 'STOPPED' -and $script:removedListener -eq 1 -and $script:removedFirewall -eq 1 -and $script:certs.Count -eq 1) 'rollback new resources only'; $passed++

Reset-Fakes
New-PilotListener 'ABC123'; New-PilotFirewall
$script:localOpen = $false; $script:mutations = 0
$r = Invoke-PilotBootstrap
Assert ($r.FINAL_STATUS -eq 'STOPPED' -and $script:removedListener -eq 0 -and $script:removedFirewall -eq 0 -and $script:mutations -eq 0) 'preserve existing resources'; $passed++

$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'Configure-NightOwlWinRMHttpsPilot.ps1'), [ref]$tokens, [ref]$errors)
Assert ($errors.Count -eq 0) 'syntax'
$source = $ast.Extent.Text
foreach ($forbidden in @('TrustedHosts','New-SelfSignedCertificate','Restart-Computer','quickconfig','Export-PfxCertificate','Start-Service','Restart-Service')) {
    Assert (-not $source.Contains($forbidden)) "forbidden: $forbidden"
}
Assert ($source.Contains('Exportable=FALSE') -and $source.Contains('CertificateTemplate=Machine')) 'machine non-exportable request'
$passed++
Write-Output "PILOT_MOCK_TESTS=$passed PASS; REAL_WINDOWS_MUTATIONS=0"
