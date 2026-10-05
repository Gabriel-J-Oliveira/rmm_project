# WinRM HTTPS pilot bootstrap

`scripts/Configure-NightOwlWinRMHttps.ps1` prepares one domain-joined Windows
computer for the NightOwl remote-install preflight over WinRM HTTPS/5986. The
operator runs it **locally** on the selected machine. The script discovers the
machine's hostname and FQDN; it is not locked to `cs-rdp-02`.

## Prerequisites

- Windows PowerShell 5.1 and a domain-authenticated network profile.
- WinRM service already running. If it is stopped, the script stops without
  changing service configuration; resolve that separately under local policy.
- The renewed `control-DC01-CA` trusted in the Windows machine certificate
  chain. Its SHA1 fingerprint is
  `0FB43130DE8CD5DDB99BE63D1B2C16107CBD4D60`.
- The Enterprise CA must offer the `Machine` template with Server
  Authentication and the machine's exact DNS FQDN in SAN. Enrollment that does
  not meet these gates is rejected.
- Run PowerShell as Administrator **only** for `-Apply` or `-Rollback`.

## Operator sequence

From the repository on the Windows pilot, audit first:

```powershell
.\scripts\Configure-NightOwlWinRMHttps.ps1
```

Audit prints the WinRM listeners, listening ports, certificate suitability,
firewall scope, and `READY_FOR_NIGHTOWL_PREFLIGHT`. It makes no changes and
does not require elevation.

Apply only after reviewing that output:

```powershell
.\scripts\Configure-NightOwlWinRMHttps.ps1 -Apply -NightOwlSourceIp '192.168.106.51' -CertificateTemplate 'Machine'
```

Before any enrollment, listener or firewall change, Apply writes a local
`state.json` under
`C:\ProgramData\NightOwl\Bootstrap\WinRMHttps\<timestamp>\`. The directory
is restricted to SYSTEM and Administrators. Keep the `STATE_PATH` printed by
the script. If a suitable certificate already exists, it is reused. Otherwise
the script attempts native machine enrollment with `certreq -enroll -machine`.
It does not create a self-signed certificate or export any key. A failed or
unsuitable enrollment stops the process.

Rollback uses the exact state file from that Apply:

```powershell
.\scripts\Configure-NightOwlWinRMHttps.ps1 -Rollback -StatePath 'C:\ProgramData\NightOwl\Bootstrap\WinRMHttps\<timestamp>\state.json'
```

Rollback checks that the current NightOwl listener and firewall rule still
match what the bootstrap applied. It restores only those recorded changes and
leaves certificates in the machine store for administrator review. If state is
missing, invalid or the configuration has changed independently, it stops.

## Scope and security

The dedicated firewall rule `NightOwl - WinRM HTTPS 5986` permits TCP/5986
only on the Domain profile and only from the configured NightOwl source IP.
If NAT changes that source address, rerun with the verified address. The
bootstrap does not touch WinRM HTTP/5985, Basic authentication,
`AllowUnencrypted`, TrustedHosts, GPO or AD. HTTP/5985 remains during this
pilot so existing management paths are preserved; NightOwl itself uses only
HTTPS. Self-signed certificates are rejected because the NightOwl server
validates a corporate CA chain and the machine's DNS name.

After local Apply reports `READY_FOR_NIGHTOWL_PREFLIGHT=YES`, run the
single-target preflight from the NightOwl UI. This script does not perform a
remote connection or agent installation.

Microsoft references: [certreq](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/certreq_1),
[New-WSManInstance](https://learn.microsoft.com/en-us/powershell/module/microsoft.wsman.management/new-wsmaninstance?view=powershell-5.1),
[WinRM HTTPS requirements](https://learn.microsoft.com/en-us/troubleshoot/windows-client/system-management-components/configure-winrm-for-https).
