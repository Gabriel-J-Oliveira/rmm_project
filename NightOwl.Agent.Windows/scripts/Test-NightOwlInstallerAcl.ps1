param([string]$InstallerPath = (Join-Path $PSScriptRoot 'Install-NightOwlAgentDotNet.ps1'))
$ErrorActionPreference = 'Stop'
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $InstallerPath), [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Installer AST invalid' }
$fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $n.Name -eq 'Set-NightOwlSecureAcl' }, $true)
if (-not $fn) { throw 'ACL function missing' }
if ($fn.Extent.Text.Contains('icacls')) { throw 'Destructive intermediate ACL writes are forbidden' }
Invoke-Expression $fn.Extent.Text

# Simulate a SYSTEM-owned protected bootstrap directory. Only native ACL objects
# are changed; no real directory, service, enrollment or installer is touched.
$script:OriginalSddl = 'O:SYG:BAD:P(D;OICI;FW;;;WD)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;BU)'
function New-TestAcl([string]$Sddl) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetSecurityDescriptorSddlForm($Sddl)
    return $acl
}
function Test-Path { param($LiteralPath, $ErrorAction) return $true }
function Get-Acl {
    param($LiteralPath, $ErrorAction)
    return New-TestAcl $script:PersistedSddl
}
function Write-InstallLog { param($EventType, $Message, $Metadata) }
function Set-Acl {
    param($LiteralPath, $AclObject, $ErrorAction)
    $script:Writes++
    if ($ErrorAction -ne 'Stop') { throw 'ACL write must fail closed' }
    if ($script:PersistedSddl -ne $script:OriginalSddl) { throw 'Unexpected intermediate persistent write' }
    $rules = @($AclObject.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $match = @($rules | Where-Object { $_.IdentityReference.Value -eq $sid })
        if ($match.Count -ne 1 -or $match[0].FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
            $match[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            throw 'Persistent write would omit SYSTEM or Administrators FullControl'
        }
    }
    if (-not $AclObject.AreAccessRulesProtected) { throw 'DACL must be protected' }
    if ($script:FailWrite) { throw 'SYNTHETIC_SECRET_ACL_EXCEPTION' }
    $script:PersistedSddl = $AclObject.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::All)
}

foreach ($read in @($false, $true)) {
    $script:PersistedSddl = $script:OriginalSddl
    $script:Writes = 0
    $script:FailWrite = $false
    Set-NightOwlSecureAcl -Paths @('synthetic-bootstrap') -AllowUsersRead:$read
    if ($script:Writes -ne 1) { throw 'Expected exactly one persistent ACL write' }
    $acl = New-TestAcl $script:PersistedSddl
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-18') { throw 'SYSTEM owner changed' }
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne (2 + [int]$read)) { throw 'Unexpected residual access rules' }
    foreach ($rule in $rules) {
        if ($rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' -or
            $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) { throw 'Wrong inheritance flags' }
    }
    $users = @($rules | Where-Object { $_.IdentityReference.Value -eq 'S-1-5-32-545' })
    if ($read -and ($users.Count -ne 1 -or
        ($users[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::ReadAndExecute) -ne [Security.AccessControl.FileSystemRights]::ReadAndExecute -or
        ($users[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write) -ne 0)) { throw 'Users must retain read/execute only' }
}
$script:PersistedSddl = $script:OriginalSddl
$script:Writes = 0
$script:FailWrite = $true
$aborted = $false
try {
    Set-NightOwlSecureAcl -Paths @('synthetic-bootstrap', 'must-not-be-reached')
    throw 'ACL failure did not abort'
} catch {
    if (-not $_.Exception.Message.StartsWith('INSTALL_ACL_APPLY_FAILED:') -or
        $_.Exception.Message.Contains('SYNTHETIC_SECRET')) { throw 'ACL error was not safely translated' }
    $aborted = $true
}
if (-not $aborted -or $script:Writes -ne 1 -or $script:PersistedSddl -ne $script:OriginalSddl) { throw 'ACL failure advanced or changed persistent state' }
Write-Host 'Installer ACL tests: PASS (SYSTEM ownership, atomic complete DACL, Users RX, failure abort/redaction).'
