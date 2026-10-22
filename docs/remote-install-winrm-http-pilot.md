# WinRM HTTP 5985 pilot for remote installation

## Safety contract

HTTPS on TCP 5986 remains the default. HTTP on TCP 5985 is selected only when
`REMOTE_INSTALL_WINRM_HTTP_ENABLED=true` and the target FQDN exactly matches
`REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST`. The browser cannot select a scheme, port,
IP address, or URL. The target still has to pass the existing AD identity,
enabled-account, DNS/IP safety, admin, Windows compatibility, NightOwl absence,
and previous-installation checks.

The two pilot entries are:

```text
cs-cvel-0289.control.local
cs-cvel-0290.control.local
```

HTTP uses NTLM with pywinrm `message_encryption="always"`. Redirects remain
blocked. There is no retry on HTTPS or HTTP when the selected port, credentials,
or WinRM negotiation fail. HTTPS continues to require the configured CA bundle,
hostname validation, and certificate validation. Basic authentication,
`AllowUnencrypted`, and TLS bypasses are not configured by this change.

## Server configuration

The feature is disabled by default. On the NightOwl server, configure the two
variables in a dedicated systemd drop-in only after the code release has been
approved and deployed:

```ini
[Service]
Environment=REMOTE_INSTALL_WINRM_HTTP_ENABLED=true
Environment=REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=cs-cvel-0289.control.local,cs-cvel-0290.control.local
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
   exactly the two FQDNs above. Confirm the pilot GPO/firewall is still limited
   to the NightOwl server source address.
2. In **Instalacao do Agente**, select only `CS-CVEL-0289`, run preflight, and
   verify the returned target FQDN/IP and transport metadata show
   `HTTP_NTLM_ENCRYPTED` / `5985`. Do not proceed if identity, DNS, preflight,
   or transport differs.
3. Start the installation for `CS-CVEL-0289` only. Verify the job reaches its
   terminal success state, `NightOwlAgentDotNet` is running, the endpoint is
   correlated to the same FQDN, and a new heartbeat is received.
4. Review the audit event for transport `HTTP_NTLM_ENCRYPTED`, port 5985, and
   the safe preflight checks. Confirm no password or raw library error appears.
5. Only after step 3 passes, repeat steps 2-4 for `CS-CVEL-0290`.

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
   5986 and its normal CA validation for every target; it does not fall back to
   HTTPS within a failed HTTP attempt.
4. If code rollback is also needed, deploy the previously approved application
   revision using the normal release procedure. Do not revert database records,
   agents, GPOs, firewall rules, or certificates as part of application
   rollback.

No remote installation is executed by this change or its tests.
