#requires -Version 5.1
[CmdletBinding(DefaultParameterSetName = 'Audit')]
param(
    [Parameter(ParameterSetName = 'Audit')]
    [Parameter(ParameterSetName = 'Apply')]
    [string]$NightOwlSourceIp = '192.168.106.51',

    [Parameter(ParameterSetName = 'Audit')]
    [Parameter(ParameterSetName = 'Apply')]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$CertificateTemplate = 'Machine',

    [Parameter(Mandatory = $true, ParameterSetName = 'Apply')]
    [switch]$Apply,

    [Parameter(Mandatory = $true, ParameterSetName = 'Rollback')]
    [switch]$Rollback,

    [Parameter(Mandatory = $true, ParameterSetName = 'Rollback')]
    [string]$StatePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:RuleName = 'NightOwl-WinRM-HTTPS-5986'
$script:RuleDisplayName = 'NightOwl - WinRM HTTPS 5986'
$script:RuleGroup = 'NightOwl WinRM HTTPS bootstrap'
$script:ExpectedRootThumbprint = '0FB43130DE8CD5DDB99BE63D1B2C16107CBD4D60'
$script:StateRoot = Join-Path $env:ProgramData 'NightOwl\Bootstrap\WinRMHttps'

function Assert-SourceIp([string]$Value) {
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Value, [ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $parsed.Equals([Net.IPAddress]::Any) -or $parsed.Equals([Net.IPAddress]::Broadcast) -or
        [Net.IPAddress]::IsLoopback($parsed)) {
        throw 'INVALID_NIGHTOWL_SOURCE_IP'
    }
}

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-LocalIdentity {
    $computer = Get-CimInstance Win32_ComputerSystem
    $hostName = [string]$computer.DNSHostName
    if (-not $hostName) { $hostName = [string]$env:COMPUTERNAME }
    $domainJoined = [bool]$computer.PartOfDomain
    $fqdn = if ($domainJoined) { "$hostName.$($computer.Domain)" } else { $hostName }
    [pscustomobject]@{ Hostname = $hostName; Fqdn = $fqdn.ToLowerInvariant(); DomainJoined = $domainJoined }
}

function Get-LocalListeners {
    $script:ListenerReadFailed = $false
    try { return @(Get-WSManInstance winrm/config/Listener -Enumerate -ErrorAction Stop) }
    catch { $script:ListenerReadFailed = $true; return @() }
}

function Get-LocalRule {
    return @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
        Where-Object { $_.Name -eq $script:RuleName })
}

function Get-RuleSnapshot($Rule) {
    if ($null -eq $Rule) { return $null }
    $port = $Rule | Get-NetFirewallPortFilter
    $address = $Rule | Get-NetFirewallAddressFilter
    [ordered]@{
        Name = [string]$Rule.Name; DisplayName = [string]$Rule.DisplayName; Group = [string]$Rule.Group
        Enabled = [string]$Rule.Enabled; Profile = [string]$Rule.Profile
        Direction = [string]$Rule.Direction; Action = [string]$Rule.Action
        Protocol = [string]$port.Protocol; LocalPort = [string]$port.LocalPort
        RemotePort = [string]$port.RemotePort
        LocalAddress = @($address.LocalAddress); RemoteAddress = @($address.RemoteAddress)
    }
}

function Test-RuleSnapshot($Snapshot, [string]$SourceIp) {
    if ($null -eq $Snapshot) { return $false }
    return ($Snapshot.Name -eq $script:RuleName -and
        $Snapshot.DisplayName -eq $script:RuleDisplayName -and
        $Snapshot.Group -eq $script:RuleGroup -and
        $Snapshot.Enabled -eq 'True' -and $Snapshot.Profile -eq 'Domain' -and
        $Snapshot.Direction -eq 'Inbound' -and $Snapshot.Action -eq 'Allow' -and
        $Snapshot.Protocol -in @('TCP', '6') -and $Snapshot.LocalPort -eq '5986' -and
        $Snapshot.RemotePort -eq 'Any' -and @($Snapshot.LocalAddress).Count -eq 1 -and
        [string]$Snapshot.LocalAddress[0] -eq 'Any' -and
        @($Snapshot.RemoteAddress).Count -eq 1 -and
        [string]$Snapshot.RemoteAddress[0] -eq $SourceIp)
}

function Get-ListenerSnapshot($Listener) {
    if ($null -eq $Listener) { return $null }
    [ordered]@{
        Address = [string]$Listener.Address; Transport = [string]$Listener.Transport
        Hostname = [string]$Listener.Hostname
        CertificateThumbprint = ([string]$Listener.CertificateThumbprint).Replace(' ', '').ToUpperInvariant()
        Port = [int]$Listener.Port; Enabled = [string]$Listener.Enabled
    }
}

function Test-ListenerSnapshot($Snapshot, [string]$Fqdn, [string]$Thumbprint) {
    if ($null -eq $Snapshot) { return $false }
    return ($Snapshot.Address -eq '*' -and $Snapshot.Transport -eq 'HTTPS' -and
        $Snapshot.Hostname -eq $Fqdn -and $Snapshot.CertificateThumbprint -eq $Thumbprint -and
        $Snapshot.Port -eq 5986 -and $Snapshot.Enabled -eq 'true')
}

function Get-CertificateAssessment($Certificate, [string]$Fqdn) {
    $dnsNames = @($Certificate.DnsNameList | ForEach-Object { [string]$_.Unicode } | Where-Object { $_ })
    $serverAuth = @($Certificate.EnhancedKeyUsageList | ForEach-Object { [string]$_.ObjectId.Value }) -contains '1.3.6.1.5.5.7.3.1'
    $sanMatch = $dnsNames -contains $Fqdn
    $now = Get-Date
    $validDates = $Certificate.NotBefore -le $now -and $now -lt $Certificate.NotAfter
    $notSelfSigned = $Certificate.Subject -ne $Certificate.Issuer
    $chainValid = $false
    $rootMatch = $false
    if ($Certificate.HasPrivateKey -and $validDates -and $serverAuth -and $sanMatch -and $notSelfSigned) {
        $chain = New-Object Security.Cryptography.X509Certificates.X509Chain
        try {
            $chain.ChainPolicy.RevocationMode = [Security.Cryptography.X509Certificates.X509RevocationMode]::Online
            $chain.ChainPolicy.RevocationFlag = [Security.Cryptography.X509Certificates.X509RevocationFlag]::ExcludeRoot
            $chain.ChainPolicy.VerificationFlags = [Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
            $chain.ChainPolicy.UrlRetrievalTimeout = [TimeSpan]::FromSeconds(10)
            $chainValid = $chain.Build($Certificate)
            if ($chainValid -and $chain.ChainElements.Count -gt 0) {
                $root = $chain.ChainElements[$chain.ChainElements.Count - 1].Certificate
                $rootMatch = ([string]$root.Thumbprint).Replace(' ', '').ToUpperInvariant() -eq $script:ExpectedRootThumbprint
            }
        } catch { $chainValid = $false }
        finally { $chain.Dispose() }
    }
    [pscustomobject]@{
        Certificate = $Certificate; Thumbprint = ([string]$Certificate.Thumbprint).Replace(' ', '').ToUpperInvariant()
        Subject = [string]$Certificate.Subject; Issuer = [string]$Certificate.Issuer
        NotAfter = $Certificate.NotAfter.ToUniversalTime().ToString('o')
        PrivateKey = [bool]$Certificate.HasPrivateKey; ServerAuth = [bool]$serverAuth
        SanMatch = [bool]$sanMatch; ChainValid = [bool]$chainValid; RootMatch = [bool]$rootMatch
        Suitable = [bool]($Certificate.HasPrivateKey -and $validDates -and $serverAuth -and
            $sanMatch -and $notSelfSigned -and $chainValid -and $rootMatch)
    }
}

function Get-BootstrapSnapshot([string]$SourceIp) {
    $identity = Get-LocalIdentity
    $service = Get-Service WinRM
    $listeners = @(Get-LocalListeners)
    $rules = @(Get-LocalRule)
    $profiles = @(Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne 'Disconnected' } |
        ForEach-Object { [string]$_.NetworkCategory } | Select-Object -Unique)
    $certificates = @(Get-ChildItem Cert:\LocalMachine\My | ForEach-Object {
        Get-CertificateAssessment $_ $identity.Fqdn
    })
    $suitable = @($certificates | Where-Object { $_.Suitable } | Sort-Object NotAfter -Descending)
    $https = @($listeners | Where-Object { [string]$_.Transport -eq 'HTTPS' })
    $http = @($listeners | Where-Object { [string]$_.Transport -eq 'HTTP' })
    $rule = if ($rules.Count -eq 1) { Get-RuleSnapshot $rules[0] } else { $null }
    $activeRule = @(Get-NetFirewallRule -PolicyStore ActiveStore -ErrorAction Stop |
        Where-Object { $_.Name -eq $script:RuleName })
    $activeRuleValid = $activeRule.Count -eq 1 -and (Test-RuleSnapshot (Get-RuleSnapshot $activeRule[0]) $SourceIp)
    [pscustomobject]@{
        Identity = $identity; Service = [string]$service.Status; ServiceStartType = [string]$service.StartType
        ListenerReadFailed = [bool]$script:ListenerReadFailed
        NetworkProfiles = $profiles; Http = $http; Https = $https; Rules = $rules
        Rule = $rule; ActiveRuleValid = [bool]$activeRuleValid; Certificates = $certificates
        Suitable = $suitable
        Port5985 = @(Get-NetTCPConnection -State Listen -LocalPort 5985 -ErrorAction SilentlyContinue).Count -gt 0
        Port5986 = @(Get-NetTCPConnection -State Listen -LocalPort 5986 -ErrorAction SilentlyContinue).Count -gt 0
    }
}

function Protect-StateDirectory([string]$Path) {
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($existing in @($acl.Access)) { [void]$acl.RemoveAccessRuleAll($existing) }
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    foreach ($sidValue in @('S-1-5-18', 'S-1-5-32-544')) {
        $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid,
            [Security.AccessControl.FileSystemRights]::FullControl, $inherit,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Save-State($State, [string]$Path) {
    $State | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function New-BackupState($Snapshot, [string]$SourceIp, [string]$Fqdn) {
    New-Item -ItemType Directory -Path $script:StateRoot -Force | Out-Null
    Protect-StateDirectory $script:StateRoot
    $directory = Join-Path $script:StateRoot ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    Protect-StateDirectory $directory
    $path = Join-Path $directory 'state.json'
    $https = @($Snapshot.Https | ForEach-Object { Get-ListenerSnapshot $_ })
    $state = [ordered]@{
        SchemaVersion = 1; CreatedAt = (Get-Date).ToUniversalTime().ToString('o')
        Fqdn = $Fqdn; SourceIp = $SourceIp; RuleName = $script:RuleName
        OriginalService = $Snapshot.Service
        OriginalNetworkProfiles = @($Snapshot.NetworkProfiles)
        OriginalListeners = $https; OriginalRule = $Snapshot.Rule
        OriginalHttpListeners = @($Snapshot.Http | ForEach-Object { Get-ListenerSnapshot $_ })
        OriginalCertificates = @($Snapshot.Certificates | Select-Object Thumbprint, Subject, Issuer,
            NotAfter, PrivateKey, ServerAuth, SanMatch, ChainValid, RootMatch)
        Changes = [ordered]@{ ServiceStarted = $false; Listener = 'none'; Firewall = 'none'; EnrollmentAttempted = $false }
        AppliedListener = $null; AppliedRule = $null; Status = 'IN_PROGRESS'
    }
    Save-State $state $path
    return [pscustomobject]@{ State = $state; Path = $path }
}

function New-HttpsListener([string]$Fqdn, [string]$Thumbprint, [int]$Port = 5986, [string]$Enabled = 'true') {
    New-WSManInstance winrm/config/Listener -SelectorSet @{ Address = '*'; Transport = 'HTTPS' } `
        -ValueSet @{ Hostname = $Fqdn; CertificateThumbprint = $Thumbprint
            Port = [string]$Port; Enabled = $Enabled } | Out-Null
}

function Remove-HttpsListener {
    Remove-WSManInstance winrm/config/Listener -SelectorSet @{ Address = '*'; Transport = 'HTTPS' } | Out-Null
}

function Set-DedicatedRule([string]$SourceIp) {
    Set-NetFirewallRule -Name $script:RuleName -PolicyStore PersistentStore `
        -DisplayName $script:RuleDisplayName -Group $script:RuleGroup -Enabled True `
        -Profile Domain -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5986 `
        -RemotePort Any -LocalAddress Any -RemoteAddress $SourceIp | Out-Null
}

function New-DedicatedRule([string]$SourceIp) {
    New-NetFirewallRule -Name $script:RuleName -DisplayName $script:RuleDisplayName `
        -Group $script:RuleGroup -PolicyStore PersistentStore -Direction Inbound `
        -Protocol TCP -LocalPort 5986 -RemotePort Any -LocalAddress Any `
        -Profile Domain -RemoteAddress $SourceIp `
        -Action Allow -Enabled True | Out-Null
}

function Restore-Rule($Original) {
    Set-NetFirewallRule -Name $script:RuleName -PolicyStore PersistentStore `
        -DisplayName $Original.DisplayName -Group $Original.Group -Enabled $Original.Enabled `
        -Profile $Original.Profile -Direction $Original.Direction -Action $Original.Action `
        -Protocol $Original.Protocol -LocalPort $Original.LocalPort -RemotePort $Original.RemotePort `
        -LocalAddress $Original.LocalAddress -RemoteAddress $Original.RemoteAddress | Out-Null
}

function Select-PreferredCertificate($Snapshot) {
    if ($Snapshot.Https.Count -eq 1) {
        $currentThumbprint = ([string]$Snapshot.Https[0].CertificateThumbprint).Replace(' ', '').ToUpperInvariant()
        $current = @($Snapshot.Suitable | Where-Object { $_.Thumbprint -eq $currentThumbprint })
        if ($current.Count) { return $current[0] }
    }
    if ($Snapshot.Suitable.Count) { return $Snapshot.Suitable[0] }
    return $null
}

function New-Summary([string]$Mode, $Snapshot, [string]$SourceIp) {
    $cert = Select-PreferredCertificate $Snapshot
    $https = @($Snapshot.Https)
    $listenerValid = $cert -and $https.Count -eq 1 -and
        (Test-ListenerSnapshot (Get-ListenerSnapshot $https[0]) $Snapshot.Identity.Fqdn $cert.Thumbprint)
    $ready = $Snapshot.Identity.DomainJoined -and $Snapshot.Service -eq 'Running' -and
        -not $Snapshot.ListenerReadFailed -and
        $Snapshot.NetworkProfiles -contains 'DomainAuthenticated' -and $listenerValid -and
        $Snapshot.Port5986 -and $Snapshot.ActiveRuleValid
    [ordered]@{
        MODE = $Mode; HOSTNAME = $Snapshot.Identity.Hostname; FQDN = $Snapshot.Identity.Fqdn
        DOMAIN_JOINED = $(if ($Snapshot.Identity.DomainJoined) { 'YES' } else { 'NO' })
        NETWORK_PROFILE = ($Snapshot.NetworkProfiles -join ',')
        WINRM_SERVICE = $Snapshot.Service
        LISTENER_AUDIT_STATUS = $(if ($Snapshot.ListenerReadFailed) { 'ERROR' } else { 'PASS' })
        HTTP_LISTENER = $(if ($Snapshot.Http.Count) { 'YES' } else { 'NO' })
        HTTP_LISTENER_DETAILS = (@($Snapshot.Http | ForEach-Object { "$($_.Address):$($_.Port)" }) -join ',')
        HTTPS_LISTENER = $(if ($https.Count) { 'YES' } else { 'NO' })
        HTTPS_LISTENER_DETAILS = (@($https | ForEach-Object { "$($_.Address):$($_.Port)" }) -join ',')
        HTTPS_LISTENER_HOSTNAME = $(if ($https.Count -eq 1) { [string]$https[0].Hostname } else { '' })
        HTTPS_LISTENER_THUMBPRINT = $(if ($https.Count -eq 1) { [string]$https[0].CertificateThumbprint } else { '' })
        PORT_5985_LISTENING = $(if ($Snapshot.Port5985) { 'YES' } else { 'NO' })
        PORT_5986_LISTENING = $(if ($Snapshot.Port5986) { 'YES' } else { 'NO' })
        SUITABLE_CERT_FOUND = $(if ($cert) { 'YES' } else { 'NO' })
        SUITABLE_CERT_THUMBPRINT = $(if ($cert) { $cert.Thumbprint } else { '' })
        SUITABLE_CERT_NOT_AFTER = $(if ($cert) { $cert.NotAfter } else { '' })
        SUITABLE_CERT_ISSUER = $(if ($cert) { $cert.Issuer } else { '' })
        CERT_THUMBPRINT = $(if ($cert) { $cert.Thumbprint } else { '' })
        CERT_SUBJECT = $(if ($cert) { $cert.Subject } else { '' })
        CERT_ISSUER = $(if ($cert) { $cert.Issuer } else { '' })
        CERT_NOT_AFTER = $(if ($cert) { $cert.NotAfter } else { '' })
        CERT_SERVER_AUTH = $(if ($cert -and $cert.ServerAuth) { 'YES' } else { 'NO' })
        CERT_SAN_MATCH = $(if ($cert -and $cert.SanMatch) { 'YES' } else { 'NO' })
        CERT_CHAIN_VALID = $(if ($cert -and $cert.ChainValid -and $cert.RootMatch) { 'YES' } else { 'NO' })
        CERT_TEMPLATE = $CertificateTemplate
        CERT_ENROLLMENT_ATTEMPTED = 'NO'; CERT_ENROLLMENT_RESULT = 'NOT_NEEDED'
        FIREWALL_RULE = $(if ($Snapshot.ActiveRuleValid) { 'PASS' } else { 'MISSING_OR_INVALID' })
        FIREWALL_PROFILE = $(if ($Snapshot.Rule) { $Snapshot.Rule.Profile } else { '' })
        FIREWALL_REMOTE_ADDRESS = $(if ($Snapshot.Rule) { $Snapshot.Rule.RemoteAddress -join ',' } else { '' })
        CHANGES_MADE = 0; STATE_PATH = ''; ROLLBACK_STATUS = ''; ERROR_CODE = ''
        READY_FOR_NIGHTOWL_PREFLIGHT = $(if ($ready) { 'YES' } else { 'NO' })
    }
}

function Write-Summary($Summary) {
    foreach ($key in $Summary.Keys) { Write-Output "$key=$($Summary[$key])" }
}

function Read-BackupState([string]$Path, [string]$Fqdn) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $rootPath = [IO.Path]::GetFullPath($script:StateRoot).TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($fullPath) -ne 'state.json' -or
        -not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { throw 'ROLLBACK_STATE_INVALID' }
    $state = Get-Content -LiteralPath $fullPath -Raw | ConvertFrom-Json
    if ($state.SchemaVersion -ne 1 -or $state.Fqdn -ne $Fqdn -or
        $state.RuleName -ne $script:RuleName -or
        $state.Status -notin @('APPLIED', 'IN_PROGRESS', 'ROLLED_BACK') -or
        $state.Changes.Listener -notin @('none', 'created', 'replaced') -or
        $state.Changes.Firewall -notin @('none', 'created', 'modified') -or
        ($state.Changes.Listener -eq 'replaced' -and
            @($state.OriginalListeners | Where-Object { $null -ne $_ }).Count -ne 1) -or
        ($state.Changes.Firewall -eq 'modified' -and $null -eq $state.OriginalRule)) {
        throw 'ROLLBACK_STATE_INVALID'
    }
    try { Assert-SourceIp $state.SourceIp }
    catch { throw 'ROLLBACK_STATE_INVALID' }
    return [pscustomobject]@{ State = $state; Path = $fullPath }
}

function Invoke-Rollback($Backup, $Snapshot) {
    $state = $Backup.State
    $currentHttps = @($Snapshot.Https)
    $listenerChange = [string]$state.Changes.Listener
    $firewallChange = [string]$state.Changes.Firewall
    $rules = @(Get-LocalRule)
    if ($listenerChange -ne 'none') {
        if ($currentHttps.Count -ne 1 -or
            -not (Test-ListenerSnapshot (Get-ListenerSnapshot $currentHttps[0]) $state.AppliedListener.Hostname $state.AppliedListener.CertificateThumbprint)) {
            throw 'ROLLBACK_LISTENER_CHANGED'
        }
    }
    if ($firewallChange -ne 'none') {
        if ($rules.Count -ne 1) { throw 'ROLLBACK_FIREWALL_CHANGED' }
        $currentRule = Get-RuleSnapshot $rules[0]
        if (-not (Test-RuleSnapshot $currentRule $state.SourceIp) -or
            ($state.Status -eq 'APPLIED' -and
                ($currentRule | ConvertTo-Json -Depth 8 -Compress) -ne
                ($state.AppliedRule | ConvertTo-Json -Depth 8 -Compress))) {
            throw 'ROLLBACK_FIREWALL_CHANGED'
        }
    }
    if ($listenerChange -ne 'none') {
        Remove-HttpsListener
        if ($listenerChange -eq 'replaced') {
            $original = $state.OriginalListeners[0]
            New-HttpsListener $original.Hostname $original.CertificateThumbprint $original.Port $original.Enabled
        }
    }
    if ($firewallChange -ne 'none') {
        if ($firewallChange -eq 'created') {
            Remove-NetFirewallRule -Name $script:RuleName -PolicyStore PersistentStore | Out-Null
        } else { Restore-Rule $state.OriginalRule }
    }
    if ($state.Changes.ServiceStarted -and $state.OriginalService -ne 'Running' -and
        (Get-Service WinRM).Status -eq 'Running') { Stop-Service WinRM }
    $state.Status = 'ROLLED_BACK'
    Save-State $state $Backup.Path
}

function Invoke-Main {
    $mode = if ($Apply) { 'APPLY' } elseif ($Rollback) { 'ROLLBACK' } else { 'AUDIT' }
    $source = if ($Rollback) { '' } else { $NightOwlSourceIp }
    if (-not $Rollback) { Assert-SourceIp $source }
    $snapshot = Get-BootstrapSnapshot $source
    $summary = New-Summary $mode $snapshot $source
    $backup = $null
    try {
        if ($mode -eq 'AUDIT') { return $summary }
        if (-not (Test-Administrator)) { throw 'ADMINISTRATOR_REQUIRED' }
        if (-not $snapshot.Identity.DomainJoined) { throw 'DOMAIN_MEMBERSHIP_REQUIRED' }
        if ($snapshot.ListenerReadFailed) { throw 'WINRM_LISTENER_READ_FAILED' }
        if ($mode -eq 'ROLLBACK') {
            $backup = Read-BackupState $StatePath $snapshot.Identity.Fqdn
            $summary.STATE_PATH = $backup.Path
            if ($backup.State.Status -eq 'ROLLED_BACK') {
                $summary.ROLLBACK_STATUS = 'ALREADY_ROLLED_BACK'
                $summary.READY_FOR_NIGHTOWL_PREFLIGHT = 'NO'
                return $summary
            }
            Invoke-Rollback $backup $snapshot
            $summary.CHANGES_MADE = [int]($backup.State.Changes.Listener -ne 'none') +
                [int]($backup.State.Changes.Firewall -ne 'none') +
                [int]$backup.State.Changes.ServiceStarted
            $summary.ROLLBACK_STATUS = 'COMPLETED'
            $summary.READY_FOR_NIGHTOWL_PREFLIGHT = 'NO'
            return $summary
        }
        if ($snapshot.NetworkProfiles -notcontains 'DomainAuthenticated') { throw 'DOMAIN_PROFILE_REQUIRED' }
        if ($snapshot.Service -ne 'Running') { throw 'WINRM_SERVICE_NOT_RUNNING' }
        if ($snapshot.Https.Count -gt 1) { throw 'MULTIPLE_HTTPS_LISTENERS' }
        if ($snapshot.Https.Count -eq 1 -and [string]$snapshot.Https[0].Address -ne '*') {
            throw 'HTTPS_LISTENER_CONFLICT'
        }
        if ($snapshot.Rules.Count -gt 1) { throw 'FIREWALL_RULE_CONFLICT' }
        if ($snapshot.Rules.Count -eq 1 -and $snapshot.Rule.Group -ne $script:RuleGroup) {
            throw 'FIREWALL_RULE_NOT_OWNED'
        }
        $sameDisplayName = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -eq $script:RuleDisplayName -and $_.Name -ne $script:RuleName })
        if ($sameDisplayName.Count) { throw 'FIREWALL_NAME_CONFLICT' }
        $candidate = Select-PreferredCertificate $snapshot
        $listenerOkay = $candidate -and $snapshot.Https.Count -eq 1 -and
            (Test-ListenerSnapshot (Get-ListenerSnapshot $snapshot.Https[0]) $snapshot.Identity.Fqdn $candidate.Thumbprint)
        $ruleOkay = Test-RuleSnapshot $snapshot.Rule $source
        if ($candidate -and $listenerOkay -and $ruleOkay -and $snapshot.ActiveRuleValid -and $snapshot.Port5986) {
            return $summary
        }
        if (-not $candidate -or -not $listenerOkay -or -not $ruleOkay) {
            $backup = New-BackupState $snapshot $source $snapshot.Identity.Fqdn
            $summary.STATE_PATH = $backup.Path
        }
        if ($null -eq $backup) { throw 'ACTIVE_FIREWALL_OR_PORT_INVALID' }
        if (-not $candidate) {
            $summary.CERT_ENROLLMENT_ATTEMPTED = 'YES'
            $backup.State.Changes.EnrollmentAttempted = $true
            Save-State $backup.State $backup.Path
            $summary.CHANGES_MADE++
            & certreq.exe -enroll -machine $CertificateTemplate *> $null
            if ($LASTEXITCODE -ne 0) { throw 'CERT_ENROLLMENT_FAILED' }
            $afterEnrollment = Get-BootstrapSnapshot $source
            $candidate = Select-PreferredCertificate $afterEnrollment
            if (-not $candidate) { throw 'ENROLLED_CERTIFICATE_UNSUITABLE' }
            $summary.CERT_ENROLLMENT_RESULT = 'SUITABLE_CERT_FOUND'
        }
        if (-not $listenerOkay) {
            $backup.State.Changes.Listener = if ($snapshot.Https.Count) { 'replaced' } else { 'created' }
            $backup.State.AppliedListener = [ordered]@{
                Address = '*'; Transport = 'HTTPS'; Hostname = $snapshot.Identity.Fqdn
                CertificateThumbprint = $candidate.Thumbprint; Port = 5986; Enabled = 'true'
            }
            Save-State $backup.State $backup.Path
            if ($snapshot.Https.Count) {
                Remove-HttpsListener
                try { New-HttpsListener $snapshot.Identity.Fqdn $candidate.Thumbprint }
                catch {
                    $original = Get-ListenerSnapshot $snapshot.Https[0]
                    try { New-HttpsListener $original.Hostname $original.CertificateThumbprint $original.Port $original.Enabled }
                    catch { throw 'HTTPS_LISTENER_RESTORE_FAILED' }
                    throw 'HTTPS_LISTENER_CREATE_FAILED'
                }
            } else { New-HttpsListener $snapshot.Identity.Fqdn $candidate.Thumbprint }
            $summary.CHANGES_MADE++
        }
        if (-not $ruleOkay) {
            $backup.State.Changes.Firewall = if ($snapshot.Rules.Count) { 'modified' } else { 'created' }
            Save-State $backup.State $backup.Path
            if ($snapshot.Rules.Count) { Set-DedicatedRule $source }
            else { New-DedicatedRule $source }
            $summary.CHANGES_MADE++
        }
        $final = Get-BootstrapSnapshot $source
        $summary = New-Summary $mode $final $source
        $summary.CHANGES_MADE = [int]$backup.State.Changes.ServiceStarted +
            [int]($backup.State.Changes.Listener -ne 'none') +
            [int]($backup.State.Changes.Firewall -ne 'none') +
            [int]$backup.State.Changes.EnrollmentAttempted
        $summary.STATE_PATH = $backup.Path
        $summary.CERT_ENROLLMENT_ATTEMPTED = $(if ($backup.State.Changes.EnrollmentAttempted) { 'YES' } else { 'NO' })
        $summary.CERT_ENROLLMENT_RESULT = $(if ($backup.State.Changes.EnrollmentAttempted) { 'SUITABLE_CERT_FOUND' } else { 'NOT_NEEDED' })
        if ($summary.READY_FOR_NIGHTOWL_PREFLIGHT -ne 'YES') { throw 'POST_APPLY_VALIDATION_FAILED' }
        $backup.State.AppliedRule = $final.Rule
        $backup.State.Status = 'APPLIED'
        Save-State $backup.State $backup.Path
    } catch {
        $summary.ERROR_CODE = [string]$_.Exception.Message
        if ($summary.CERT_ENROLLMENT_ATTEMPTED -eq 'YES') { $summary.CERT_ENROLLMENT_RESULT = 'FAILED' }
        $summary.READY_FOR_NIGHTOWL_PREFLIGHT = 'NO'
    }
    return $summary
}

# Dot-sourcing exposes functions for isolated tests without running the bootstrap.
if ($MyInvocation.InvocationName -ne '.') {
    try { $result = Invoke-Main }
    catch {
        $mode = if ($Apply) { 'APPLY' } elseif ($Rollback) { 'ROLLBACK' } else { 'AUDIT' }
        Write-Output "MODE=$mode"
        Write-Output 'ERROR_CODE=AUDIT_COLLECTION_FAILED'
        Write-Output 'READY_FOR_NIGHTOWL_PREFLIGHT=NO'
        exit 1
    }
    Write-Summary $result
    if ($result.ERROR_CODE) { exit 1 }
}
