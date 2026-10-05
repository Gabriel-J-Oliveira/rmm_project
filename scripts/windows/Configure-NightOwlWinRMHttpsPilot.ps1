#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:PilotFqdn = 'cs-rdp-02.control.local'
$script:RuleName = 'NightOwl-WinRM-HTTPS-Pilot'
$script:RuleGroup = 'NightOwl HTTPS pilot bootstrap v1'
$script:SourceIp = '192.168.106.51'

function Get-PilotIdentity {
    $computer = Get-CimInstance Win32_ComputerSystem
    $os = Get-CimInstance Win32_OperatingSystem
    [pscustomobject]@{ Hostname = $computer.DNSHostName; Domain = $computer.Domain
        Fqdn = "$($computer.DNSHostName).$($computer.Domain)"; Windows = $os.Caption; Build = $os.BuildNumber }
}

function Test-PilotCertificate($Certificate) {
    $now = Get-Date
    if (-not $Certificate.HasPrivateKey -or $Certificate.NotBefore -gt $now -or
        $Certificate.NotAfter -le $now -or $Certificate.Subject -eq $Certificate.Issuer) { return $false }
    $eku = @($Certificate.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId.Value })
    if ($eku -notcontains '1.3.6.1.5.5.7.3.1') { return $false }
    $names = @($Certificate.DnsNameList | ForEach-Object { $_.Unicode })
    if ($names.Count -eq 0) {
        $names = @($Certificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false))
    }
    if ($names -notcontains $script:PilotFqdn) { return $false }
    return Test-PilotChain $Certificate
}

function Test-PilotChain($Certificate) {
    $chain = New-Object Security.Cryptography.X509Certificates.X509Chain
    try {
        $chain.ChainPolicy.RevocationMode = 'Online'
        $chain.ChainPolicy.RevocationFlag = 'ExcludeRoot'
        $chain.ChainPolicy.VerificationFlags = 'NoFlag'
        $chain.ChainPolicy.UrlRetrievalTimeout = [TimeSpan]::FromSeconds(10)
        return $chain.Build($Certificate)
    } finally { $chain.Dispose() }
}

function Get-PilotCertificates { @(Get-ChildItem Cert:\LocalMachine\My) }
function Get-PilotListeners { @(Get-WSManInstance winrm/config/Listener -Enumerate) }
function Get-PilotFirewall {
    @(Get-NetFirewallRule -PolicyStore ActiveStore | Where-Object { $_.Name -eq $script:RuleName -or $_.DisplayName -eq 'NightOwl - WinRM HTTPS 5986' })
}
function Get-PilotRelatedFirewall {
    @(Get-NetFirewallRule -PolicyStore ActiveStore | Where-Object { $_.Name -like '*WinRM*' -or $_.DisplayName -like '*WinRM*' } |
        Select-Object Name, DisplayName, Profile, Direction, Action, Enabled)
}

function Test-PilotFirewall($Rule) {
    $port = @($Rule | Get-NetFirewallPortFilter)
    $address = @($Rule | Get-NetFirewallAddressFilter)
    return ($Rule.Name -eq $script:RuleName -and $Rule.Group -eq $script:RuleGroup -and
        [string]$Rule.Direction -eq 'Inbound' -and [string]$Rule.Action -eq 'Allow' -and
        [string]$Rule.Enabled -eq 'True' -and [string]$Rule.Profile -eq 'Domain' -and
        $port.Count -eq 1 -and [string]$port[0].Protocol -in @('TCP', '6') -and
        [string]$port[0].LocalPort -eq '5986' -and $address.Count -eq 1 -and
        @($address[0].RemoteAddress).Count -eq 1 -and $address[0].RemoteAddress[0] -eq $script:SourceIp)
}

function Invoke-PilotEnrollment {
    # Native machine-context request with an explicitly non-exportable private key.
    $directory = Join-Path $env:TEMP ([Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $directory | Out-Null
    try {
        $inf = Join-Path $directory 'machine.inf'
        $request = Join-Path $directory 'machine.req'
        $certificate = Join-Path $directory 'machine.cer'
        @'
[Version]
Signature="$Windows NT$"
[NewRequest]
MachineKeySet=TRUE
Exportable=FALSE
KeyLength=2048
KeyAlgorithm=RSA
HashAlgorithm=SHA256
RequestType=PKCS10
[RequestAttributes]
CertificateTemplate=Machine
'@ | Set-Content -LiteralPath $inf -Encoding Ascii
        & certreq.exe -q -new -machine $inf $request *> $null
        if ($LASTEXITCODE -ne 0) { throw 'MACHINE_REQUEST_FAILED' }
        & certreq.exe -q -submit -machine $request $certificate *> $null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $certificate)) { throw 'MACHINE_ENROLLMENT_FAILED_OR_PENDING' }
        & certreq.exe -q -accept -machine $certificate *> $null
        if ($LASTEXITCODE -ne 0) { throw 'MACHINE_ACCEPT_FAILED' }
    } finally {
        # Only this invocation's random temporary directory, never the certificate store.
        Remove-Item -LiteralPath $directory -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Test-PilotListener($Listener, $Thumbprint) {
    return ([string]$Listener.Transport -eq 'HTTPS' -and [string]$Listener.Address -eq '*' -and
        [string]$Listener.Hostname -eq $script:PilotFqdn -and
        [string]$Listener.CertificateThumbprint -eq $Thumbprint -and
        [int]$Listener.Port -eq 5986 -and [string]$Listener.Enabled -eq 'true')
}
function New-PilotListener($Thumbprint) {
    New-WSManInstance winrm/config/Listener -SelectorSet @{ Address = '*'; Transport = 'HTTPS' } `
        -ValueSet @{ Hostname = $script:PilotFqdn; CertificateThumbprint = $Thumbprint; Port = '5986' } | Out-Null
}
function Remove-PilotListener { Remove-WSManInstance winrm/config/Listener -SelectorSet @{ Address = '*'; Transport = 'HTTPS' } | Out-Null }
function New-PilotFirewall {
    New-NetFirewallRule -Name $script:RuleName -DisplayName 'NightOwl - WinRM HTTPS 5986' -Group $script:RuleGroup `
        -Direction Inbound -Protocol TCP -LocalPort 5986 -Profile Domain -Action Allow `
        -RemoteAddress $script:SourceIp -Enabled True -PolicyStore PersistentStore | Out-Null
}
function Remove-PilotFirewall { Remove-NetFirewallRule -Name $script:RuleName -PolicyStore PersistentStore }
function Test-PilotLocalPort {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', 5986)
        return ($task.Wait(3000) -and $client.Connected)
    } finally { $client.Dispose() }
}
function Test-PilotAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Get-PilotService { Get-Service WinRM }

function Invoke-PilotBootstrap {
    $createdListener = $false; $createdFirewall = $false
    $result = [ordered]@{ HOST_GUARD = 'FAIL'; CERT_SOURCE = 'NONE'; CERT_TEMPLATE = 'NONE'
        CERT_THUMBPRINT = ''; CERT_SUBJECT = ''; CERT_SAN = @(); CERT_ISSUER = ''; CERT_NOT_AFTER = ''
        SERVER_AUTH_EKU = 'FAIL'; PRIVATE_KEY = 'ABSENT'; HTTPS_LISTENER = 'FAIL'; HTTPS_PORT = 5986
        FIREWALL_RULE = 'FAIL'; FIREWALL_REMOTE_ADDRESS = $script:SourceIp; WINRM_SERVICE = 'OTHER'
        LOCAL_5986 = 'CLOSED'; FINAL_STATUS = 'STOPPED'; ERROR_CODE = ''; ROLLBACK_ERRORS = @()
        ENROLLED_CERTIFICATE_RETAINED = $false }
    try {
        $identity = Get-PilotIdentity
        if ($identity.Fqdn -ne $script:PilotFqdn) { throw 'WRONG_HOST' }
        $result.HOST_GUARD = 'PASS'
        if (-not (Test-PilotAdministrator)) { throw 'ELEVATION_REQUIRED' }
        $service = Get-PilotService
        if ([string]$service.Status -ne 'Running') { throw 'WINRM_NOT_RUNNING' }
        $result.WINRM_SERVICE = 'RUNNING'
        $listeners = @(Get-PilotListeners)
        $https = @($listeners | Where-Object { $_.Transport -eq 'HTTPS' })
        $rules = @(Get-PilotFirewall)
        $certificates = @(Get-PilotCertificates)
        $result['PRECHECK'] = @{ Identity = $identity; Service = [string]$service.Status
            Listeners = @($listeners | Select-Object Address, Transport, Hostname, Port, CertificateThumbprint)
            Firewall = @($rules | Select-Object Name, DisplayName, Profile, Direction, Action, Enabled)
            RelatedFirewall = @(Get-PilotRelatedFirewall)
            Certificates = @($certificates | Select-Object Thumbprint, Subject, Issuer, NotBefore, NotAfter, HasPrivateKey) }
        if ($rules.Count -gt 1 -or ($rules.Count -eq 1 -and -not (Test-PilotFirewall $rules[0]))) { throw 'FIREWALL_CONFLICT' }
        if ($https.Count -gt 1) { throw 'HTTPS_LISTENER_CONFLICT' }
        $candidates = @($certificates | Where-Object { Test-PilotCertificate $_ })
        if ($candidates.Count -gt 1) { throw 'AMBIGUOUS_CERTIFICATES' }
        if ($candidates.Count -eq 0) {
            if ($https.Count -gt 0) { throw 'HTTPS_LISTENER_CONFLICT' }
            $result.CERT_SOURCE = 'ENROLLED'; $result.CERT_TEMPLATE = 'Machine'
            $result.ENROLLED_CERTIFICATE_RETAINED = $true
            Invoke-PilotEnrollment
            $candidates = @(Get-PilotCertificates | Where-Object { Test-PilotCertificate $_ })
            if ($candidates.Count -ne 1) { throw 'ENROLLED_CERTIFICATE_INVALID_OR_AMBIGUOUS' }
        } else { $result.CERT_SOURCE = 'EXISTING' }
        $cert = $candidates[0]
        $result.CERT_THUMBPRINT = $cert.Thumbprint; $result.CERT_SUBJECT = $cert.Subject
        $result.CERT_SAN = @($cert.DnsNameList | ForEach-Object { $_.Unicode })
        $result.CERT_ISSUER = $cert.Issuer; $result.CERT_NOT_AFTER = $cert.NotAfter.ToUniversalTime().ToString('o')
        $result.SERVER_AUTH_EKU = 'PASS'; $result.PRIVATE_KEY = 'PRESENT'
        if ($https.Count -eq 1 -and -not (Test-PilotListener $https[0] $cert.Thumbprint)) { throw 'HTTPS_LISTENER_CONFLICT' }
        if ($https.Count -eq 0) { New-PilotListener $cert.Thumbprint; $createdListener = $true }
        if ($rules.Count -eq 0) { New-PilotFirewall; $createdFirewall = $true }
        $finalListeners = @(Get-PilotListeners | Where-Object { $_.Transport -eq 'HTTPS' })
        $finalRules = @(Get-PilotFirewall)
        if ($finalListeners.Count -ne 1 -or -not (Test-PilotListener $finalListeners[0] $cert.Thumbprint) -or
            @($finalListeners[0].ListeningOn).Count -eq 0 -or -not ($finalListeners[0].ListeningOn -join '').Trim()) { throw 'LISTENER_VALIDATION_FAILED' }
        if ($finalRules.Count -ne 1 -or -not (Test-PilotFirewall $finalRules[0])) { throw 'FIREWALL_VALIDATION_FAILED' }
        if ([string](Get-PilotService).Status -ne 'Running') { throw 'WINRM_NOT_RUNNING' }
        if (-not (Test-PilotLocalPort)) { throw 'LOCAL_PORT_CLOSED' }
        $result.HTTPS_LISTENER = 'PASS'; $result.FIREWALL_RULE = 'PASS'; $result.LOCAL_5986 = 'OPEN'
        $result.FINAL_STATUS = if ($createdListener -or $createdFirewall) { 'PILOT_WINRM_HTTPS_CONFIGURED' } else { 'ALREADY_CONFIGURED' }
    } catch {
        $known = @('WRONG_HOST','ELEVATION_REQUIRED','WINRM_NOT_RUNNING','FIREWALL_CONFLICT','HTTPS_LISTENER_CONFLICT',
            'AMBIGUOUS_CERTIFICATES','ENROLLED_CERTIFICATE_INVALID_OR_AMBIGUOUS','LISTENER_VALIDATION_FAILED',
            'FIREWALL_VALIDATION_FAILED','LOCAL_PORT_CLOSED','MACHINE_REQUEST_FAILED','MACHINE_ENROLLMENT_FAILED_OR_PENDING','MACHINE_ACCEPT_FAILED')
        $result.ERROR_CODE = if ($_.Exception.Message -in $known) { $_.Exception.Message } else { 'BOOTSTRAP_FAILED' }
        if ($createdFirewall) {
            try {
                $rollbackRules = @(Get-PilotFirewall)
                if ($rollbackRules.Count -ne 1 -or -not (Test-PilotFirewall $rollbackRules[0])) { throw 'ROLLBACK_OWNERSHIP_CHANGED' }
                Remove-PilotFirewall
            } catch { $result.ROLLBACK_ERRORS += 'FIREWALL_ROLLBACK_FAILED' }
        }
        if ($createdListener) {
            try {
                $rollbackListeners = @(Get-PilotListeners | Where-Object { $_.Transport -eq 'HTTPS' })
                if ($rollbackListeners.Count -ne 1 -or -not (Test-PilotListener $rollbackListeners[0] $result.CERT_THUMBPRINT)) { throw 'ROLLBACK_OWNERSHIP_CHANGED' }
                Remove-PilotListener
            } catch { $result.ROLLBACK_ERRORS += 'LISTENER_ROLLBACK_FAILED' }
        }
    }
    [pscustomobject]$result
}

# Dot sourcing loads helpers for isolated mocks; direct execution runs only locally.
if ($MyInvocation.InvocationName -ne '.') {
    $summary = Invoke-PilotBootstrap
    $summary | ConvertTo-Json -Depth 8
    if ($summary.FINAL_STATUS -eq 'STOPPED') { exit 1 }
}
