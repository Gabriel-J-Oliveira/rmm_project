# WinRM HTTPS pilot: CS-RDP-02

This bootstrap is for local, elevated Windows PowerShell 5.1 on
`cs-rdp-02.control.local` only. It has no credential or target parameters.
Publishing this script does not execute it. Run only one instance at a time.

```powershell
powershell.exe -NoProfile -File .\Configure-NightOwlWinRMHttpsPilot.ps1
```

The script checks host identity and elevation before mutations, requires WinRM
already running, and preserves HTTP 5985. It selects exactly one valid machine
certificate with the exact DNS identity, Server Authentication EKU, private key
and a Windows-validated chain (including revocation checks). Ambiguity stops.
If none exists, native `certreq` requests the published `Machine` template in
machine context with `Exportable=FALSE`. Enterprise enrollment policy must
resolve the issuing CA without interaction; unavailable templates, pending
requests, missing DNS identity or invalid certificates stop. No alternative
template or self-signed fallback is used. Enrollment may leave a machine key
or request/certificate for manual review even if later steps fail.

Existing conflicting HTTPS listeners or dedicated firewall rules stop without
being overwritten. A new rule permits only TCP 5986, Domain profile, inbound
from `192.168.106.51`. Other firewall rules are inventoried, not changed; this
dedicated rule does not negate broader existing rules. New listener/rule are
rolled back on later failure only if they still match this invocation's scope.
Certificates are never removed. Rollback failures are reported for manual review.
No service restart is performed: if the listener cannot become live, stop and
roll back instead of disrupting existing remoting sessions.

`PILOT_WINRM_HTTPS_CONFIGURED` / `ALREADY_CONFIGURED` prove local configuration
and TCP availability only. They do not prove NightOwl trusts the leaf chain.
After operator execution, a separate unauthenticated TLS handshake from
NightOwl must validate hostname and chain with
`/etc/nightowl/trust/control-DC01-CA-current.pem` before authenticated preflight.
That dedicated bundle currently contains only the renewed corporate root.
A Windows-valid certificate chaining to the old root is not automatically
trusted by NightOwl. Do not disable validation or add HTTP fallback.

Offline tests (no Windows configuration or endpoint access):

```powershell
pwsh -NoProfile -File .\Test-NightOwlWinRMHttpsPilot.ps1
```

Native command contracts:
[certreq](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/certreq_1)
and [New-WSManInstance](https://learn.microsoft.com/en-us/powershell/module/microsoft.wsman.management/new-wsmaninstance?view=powershell-5.1).
