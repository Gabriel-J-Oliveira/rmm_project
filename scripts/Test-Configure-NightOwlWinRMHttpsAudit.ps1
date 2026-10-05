#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Configure-NightOwlWinRMHttps.ps1')
$originalAssessment = ${function:Get-CertificateAssessment}

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ($Actual -cne $Expected) { throw "$Label`: expected '$Expected', got '$Actual'" }
}

$script:Fault = ''
$script:MutationCalls = 0
function Get-CimInstance { [CmdletBinding()] param($ClassName)
    if ($script:Fault -eq 'IDENTITY') { throw 'Access is denied' }
    [pscustomobject]@{ DNSHostName = 'cs-rdp-02'; PartOfDomain = $true; Domain = 'control.local' }
}
function Get-Service { [CmdletBinding()] param($Name)
    if ($script:Fault -eq 'SERVICE') { throw 'Service query failed' }
    [pscustomobject]@{ Status = 'Running'; StartType = 'Automatic' }
}
function Get-WSManInstance { [CmdletBinding()] param($ResourceURI, [switch]$Enumerate)
    if ($script:Fault -eq 'LISTENERS') { throw 'WSMan query failed' }
}
function Get-NetConnectionProfile { [CmdletBinding()] param()
    if ($script:Fault -eq 'PROFILE') { throw 'Profile query failed' }
    [pscustomobject]@{ IPv4Connectivity = 'Internet'; NetworkCategory = 'DomainAuthenticated' }
}
function Get-NetFirewallRule { [CmdletBinding()] param($PolicyStore)
    if ($script:Fault -eq "FIREWALL_$PolicyStore") { throw 'Access is denied' }
}
function Get-ChildItem { [CmdletBinding()] param($Path)
    if ($script:Fault -eq 'CERT_STORE') { throw 'Certificate store unavailable' }
    [pscustomobject]@{ Thumbprint = 'FAKE' }
}
function Get-CertificateAssessment { param($Certificate, $Fqdn)
    if ($script:Fault -eq 'CHAIN') { throw 'CERTIFICATE_CHAIN_EVALUATION_FAILED' }
    [pscustomobject]@{
        Certificate = $Certificate; Thumbprint = 'FAKE'; Subject = 'CN=cs-rdp-02.control.local'
        Issuer = 'CN=control-DC01-CA'; NotAfter = '2030-01-01T00:00:00Z'
        PrivateKey = $true; ServerAuth = $true; SanMatch = $true
        ChainValid = $true; RootMatch = $true; Suitable = $true
    }
}
function Get-NetTCPConnection { [CmdletBinding()] param($State, $LocalPort)
    if ($script:Fault -eq "PORT_$LocalPort") { throw 'Port query failed' }
    [pscustomobject]@{ LocalPort = $LocalPort; State = 'Listen' }
}
function Test-Administrator {
    if (-not $Apply -and -not $Rollback) { throw 'Audit requested elevation' }
    return $true
}
function New-BackupState { $script:MutationCalls++; throw 'MUTATION_ATTEMPTED' }

$cases = @(
    @{ Fault = 'IDENTITY'; Component = 'LOCAL_IDENTITY'; Code = 'LOCAL_IDENTITY_READ_FAILED'; Other = 'WINRM_SERVICE' }
    @{ Fault = 'SERVICE'; Component = 'WINRM_SERVICE'; Code = 'WINRM_SERVICE_READ_FAILED'; Other = 'NETWORK_PROFILE' }
    @{ Fault = 'LISTENERS'; Component = 'WINRM_LISTENERS'; Code = 'WINRM_LISTENER_READ_FAILED'; Other = 'WINRM_SERVICE' }
    @{ Fault = 'PROFILE'; Component = 'NETWORK_PROFILE'; Code = 'NETWORK_PROFILE_READ_FAILED'; Other = 'WINRM_SERVICE' }
    @{ Fault = 'FIREWALL_PersistentStore'; Component = 'FIREWALL_PERSISTENT_RULE'; Code = 'FIREWALL_RULE_READ_FAILED'; Other = 'FIREWALL_ACTIVE_RULE' }
    @{ Fault = 'FIREWALL_ActiveStore'; Component = 'FIREWALL_ACTIVE_RULE'; Code = 'FIREWALL_ACTIVE_RULE_READ_FAILED'; Other = 'FIREWALL_PERSISTENT_RULE' }
    @{ Fault = 'CERT_STORE'; Component = 'CERTIFICATE_STORE'; Code = 'CERTIFICATE_ENUMERATION_FAILED'; Other = 'WINRM_SERVICE' }
    @{ Fault = 'CHAIN'; Component = 'CERTIFICATE_CHAIN'; Code = 'CERTIFICATE_CHAIN_EVALUATION_FAILED'; Other = 'WINRM_SERVICE' }
    @{ Fault = 'PORT_5985'; Component = 'PORT_5985'; Code = 'PORT_STATE_READ_FAILED'; Other = 'PORT_5986' }
    @{ Fault = 'PORT_5986'; Component = 'PORT_5986'; Code = 'PORT_STATE_READ_FAILED'; Other = 'PORT_5985' }
)
foreach ($case in $cases) {
    $script:Fault = $case.Fault
    $snapshot = Get-AuditSnapshot '192.168.106.51'
    $summary = Add-AuditDiagnostics (New-Summary 'AUDIT' $snapshot '192.168.106.51') $snapshot.Components
    Assert-Equal $summary["$($case.Component)_STATUS"] 'FAIL' $case.Fault
    Assert-Equal $summary["$($case.Other)_STATUS"] 'PASS' "$($case.Fault) continuation"
    if ($summary.AUDIT_ERRORS -notlike "*$($case.Code)*") { throw "Missing specific code for $($case.Fault)" }
    if ($summary.ERROR_CODE -eq 'AUDIT_COLLECTION_FAILED') { throw "Generic audit code for $($case.Fault)" }
    Assert-Equal $summary.READY_FOR_NIGHTOWL_PREFLIGHT 'NO' "$($case.Fault) readiness"
    Assert-Equal $summary.MODE 'AUDIT' "$($case.Fault) mode"
    Assert-Equal $summary.HOSTNAME $(if ($case.Fault -eq 'IDENTITY') { 'UNKNOWN' } else { 'cs-rdp-02' }) "$($case.Fault) hostname"
    $Apply = $true
    $strictSummary = Invoke-Main
    Assert-Equal $strictSummary.READY_FOR_NIGHTOWL_PREFLIGHT 'NO' "$($case.Fault) Apply fail closed"
    Assert-Equal $script:MutationCalls 0 "$($case.Fault) Apply mutation"
    $Apply = $false
}
Assert-Equal $script:MutationCalls 0 'Audit mutations'

$script:Fault = 'LISTENERS'
$Apply = $false
$Rollback = $false
$fullAudit = Invoke-Main
Assert-Equal $fullAudit.WINRM_LISTENER_AUDIT_STATUS 'FAIL' 'Invoke-Main audit status'
Assert-Equal $fullAudit.ERROR_CODE 'WINRM_LISTENER_READ_FAILED' 'Invoke-Main specific error'
Assert-Equal $fullAudit.HTTP_LISTENER 'UNKNOWN' 'Unread listener is unknown'

# Exercise the real certificate assessment when X509Chain.Build throws.
function New-Object { param($TypeName)
    if ($TypeName -ne 'Security.Cryptography.X509Certificates.X509Chain') { throw 'Unexpected constructor' }
    $chain = [pscustomobject]@{ ChainPolicy = [pscustomobject]@{
        RevocationMode = $null; RevocationFlag = $null; VerificationFlags = $null
        UrlRetrievalTimeout = $null }; ChainElements = @() }
    $chain | Add-Member ScriptMethod Build { param($Certificate) throw 'Chain build failed' }
    $chain | Add-Member ScriptMethod Dispose { }
    return $chain
}
$fakeCertificate = [pscustomobject]@{
    DnsNameList = @([pscustomobject]@{ Unicode = 'cs-rdp-02.control.local' })
    EnhancedKeyUsageList = @([pscustomobject]@{ ObjectId = [pscustomobject]@{ Value = '1.3.6.1.5.5.7.3.1' } })
    NotBefore = (Get-Date).AddDays(-1); NotAfter = (Get-Date).AddDays(1)
    Subject = 'CN=cs-rdp-02.control.local'; Issuer = 'CN=CA'; HasPrivateKey = $true
    Thumbprint = 'FAKE'
}
$chainError = ''
try { & $originalAssessment $fakeCertificate 'cs-rdp-02.control.local' | Out-Null }
catch { $chainError = [string]$_.Exception.Message }
Assert-Equal $chainError 'CERTIFICATE_CHAIN_EVALUATION_FAILED' 'X509Chain.Build failure'
Remove-Item Function:\New-Object

# One certificate assessment failure must not prevent another certificate being assessed.
$script:Fault = 'CHAIN'
function Get-ChildItem { [CmdletBinding()] param($Path)
    [pscustomobject]@{ Thumbprint = 'BAD' }
    [pscustomobject]@{ Thumbprint = 'GOOD' }
}
function Get-CertificateAssessment { param($Certificate, $Fqdn)
    if ($Certificate.Thumbprint -eq 'BAD') { throw 'CERTIFICATE_CHAIN_EVALUATION_FAILED' }
    [pscustomobject]@{ Certificate = $Certificate; Thumbprint = 'GOOD'; Subject = 'CN=good'
        Issuer = 'CN=CA'; NotAfter = '2030-01-01T00:00:00Z'; PrivateKey = $true
        ServerAuth = $true; SanMatch = $true; ChainValid = $true; RootMatch = $true; Suitable = $true }
}
$snapshot = Get-AuditSnapshot '192.168.106.51'
Assert-Equal $snapshot.Certificates.Count 1 'Certificate continuation'
Assert-Equal $snapshot.Suitable[0].Thumbprint 'GOOD' 'Suitable certificate survives'
Assert-Equal $snapshot.Components.CERTIFICATE_CHAIN.Status 'FAIL' 'Partial assessment status'

# Apply and rollback retain strict preflight; a failed read never reaches mutations.
$script:Fault = 'IDENTITY'
$Apply = $true
$Rollback = $false
$applySummary = Invoke-Main
Assert-Equal $applySummary.READY_FOR_NIGHTOWL_PREFLIGHT 'NO' 'Apply fail closed'
Assert-Equal $script:MutationCalls 0 'Apply mutations after read failure'
$Apply = $false
$Rollback = $true
$StatePath = 'C:\nonexistent\state.json'
$rollbackSummary = Invoke-Main
Assert-Equal $rollbackSummary.READY_FOR_NIGHTOWL_PREFLIGHT 'NO' 'Rollback fail closed'
Assert-Equal $script:MutationCalls 0 'Rollback mutations after read failure'

Write-Output 'AUDIT_DIAGNOSTIC_TESTS=PASS; MUTATIONS=0'
