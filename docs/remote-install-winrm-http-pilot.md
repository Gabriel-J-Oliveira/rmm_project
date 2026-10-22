# WinRM HTTP 5985 pilot for remote installation

## Safety contract

HTTPS on TCP 5986 is always attempted first. HTTP on TCP 5985 is considered only
after TCP/WinRM connectivity failure on HTTPS, and only when
`REMOTE_INSTALL_WINRM_HTTP_ENABLED=true` and the target FQDN exactly matches
`REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST`. Invalid TLS certificates, authentication
failures, authorization failures, and identity mismatches never trigger an HTTP
fallback. The browser cannot select a scheme, port, IP address, or URL. The
target still has to pass the existing AD identity, enabled-account, DNS/IP
safety, admin, Windows compatibility, NightOwl absence, and
previous-installation checks.

Existing pilot entries are:

```text
cs-cvel-0289.control.local
cs-cvel-0290.control.local
```

For the separately approved first test of `CS-CVEL-0047` and `CS-CVEL-0292`,
the effective service allowlist must explicitly include those exact FQDNs as
well. Preserve the existing entries unless their authorization is separately
revoked. Do not authorize an OU, wildcard, suffix, IP address, or browser value.

HTTP uses NTLM with pywinrm `message_encryption="always"`. Redirects remain
blocked. An authentication, authorization, certificate, identity, or protocol
failure is terminal for that target; it never causes a different transport to
be tried. The endpoint IP selected and identity-verified during
preflight is pinned for install and service validation; a new DNS answer is not
used for dispatch. A pre-dispatch reconnect to another DNS address repeats
remote identity verification. Once the installer payload may have been sent,
no endpoint or transport retry occurs. HTTPS continues to require the configured CA bundle,
hostname validation, and certificate validation. Basic authentication,
`AllowUnencrypted`, and TLS bypasses are not configured by this change.

## Server configuration

The feature is disabled by default. On the NightOwl server, configure the two
variables in a dedicated systemd drop-in only after the code release has been
approved and deployed:

```ini
[Service]
Environment=REMOTE_INSTALL_WINRM_HTTP_ENABLED=true
Environment=REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=cs-cvel-0289.control.local,cs-cvel-0290.control.local,cs-cvel-0047.control.local,cs-cvel-0292.control.local
```

Create/edit it with `sudo systemctl edit --drop-in=winrm-http-pilot.conf
nightowl.service`, then run `sudo systemctl daemon-reload` and restart
`nightowl.service` during an approved maintenance window. Do not print the full
service environment; it may contain credentials. Verify only the two effective
settings through a restricted Django shell or a purpose-built sanitized health
check. No GPO, AD, certificate, or firewall change is part of this application
change.

## Pilot sequence

1. Confirm the effective HTTP setting is enabled and the allowlist contains
   exactly the approved FQDNs listed above. Confirm the pilot GPO/firewall is
   still limited to the NightOwl server source address.
2. For a host authorized for HTTP, **Instalacao do Agente** should report the
   transport that actually passed preflight. If 5986 is open and valid, it stays
   on HTTPS; otherwise, only a connectivity failure may lead to 5985. Do not
   proceed if identity, DNS, preflight, or transport differs.
3. Start the installation for one authorized endpoint only. Verify the job
   reaches its terminal success state, `NightOwlAgentDotNet` is running, the endpoint is
   correlated to the same FQDN, and a new heartbeat is received.
4. Review the audit event for transport `HTTP_NTLM_ENCRYPTED`, port 5985, and
   the safe preflight checks. Confirm no password or raw library error appears.
5. Only after one endpoint passes, repeat the same controlled steps for the
   next explicitly authorized pilot. For the current change, the requested
   first pair is `CS-CVEL-0047` followed by `CS-CVEL-0292`.

## HTTPS recovery after an HTTP installation

An HTTP success does not change WinRM, request certificates, create an HTTPS
listener, or alter firewall policy. HTTPS recovery is deliberately separate
from agent installation and is not automatically attempted on install success.
Use the existing corporate `scripts/Configure-NightOwlWinRMHttps.ps1` contract
through its separately approved diagnostic/apply procedure. First run its
read-only Audit mode and confirm the expected corporate CA root, FQDN/SAN,
Domain profile, and source-IP-restricted 5986 rule. Only a separately authorized
operator may run Apply. If certificate enrollment or validation fails, retain
the successful agent installation and record HTTPS recovery as pending; do not
install a self-signed certificate or weaken TLS. Prevent concurrent recovery
attempts by permitting one operator-controlled run per endpoint at a time.

The batch workflow calls the same per-target preflight and the same server-side
protocol selector. A pilot batch is not required; initially test one endpoint at
a time to preserve clear attribution.

## Rollback

1. Stop admitting new remote-install jobs for the two pilot targets.
2. In the dedicated systemd drop-in set
   `REMOTE_INSTALL_WINRM_HTTP_ENABLED=false` and clear
   `REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=`. Apply with `daemon-reload` and a
   controlled service restart.
3. Verify the effective setting is disabled. The application then uses HTTPS
   5986 and its normal CA validation for every target; no HTTP fallback is
   available while disabled.
4. If code rollback is also needed, deploy the previously approved application
   revision using the normal release procedure. Do not revert database records,
   agents, GPOs, firewall rules, or certificates as part of application
   rollback.

No remote installation is executed by this change or its tests.
