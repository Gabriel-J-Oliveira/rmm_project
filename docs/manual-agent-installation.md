# Manual Windows Agent Installation

This flow installs one Windows endpoint at a time from the NightOwl UI. It uses the existing `AgentDeploymentToken` bootstrap and does not require WinRM, GPO, or remote execution.

## Server readiness

Before an operator starts, verify:

- `NIGHTOWL_PUBLIC_URL` is the public HTTPS origin reachable from the target Windows computer.
- The deployment bootstrap and the selected release artifacts are served over HTTPS.
- A currently valid, published deployment trust bundle exists. Its signing key must be active and not revoked.
- The selected release is signed, not revoked, not legacy-unsigned, and compatible with that trust bundle. Stable is the recommended channel; development requires an explicit release selection.
- The authorized operator is an active technical NightOwl user. The flow does not grant AD, Domain Admin, or Django superuser access as a side effect.

The create API fails closed when HTTPS, release signatures, or the trust bundle are missing, expired, or incompatible. No deployment token is created in those cases.

## Operator procedure

1. Open **Instalação do Agente** and select **INSTALAÇÃO MANUAL — WINDOWS**. The same flow is also available from the endpoint-list device action.
2. Keep **Stable (recomendado)** selected unless a release owner has explicitly approved a different signed release.
3. Click **Gerar instalação** once. The server creates an individual, single-use deployment token with a fixed 30-minute lifetime and returns the PowerShell command.
4. On the intended Windows computer, open Windows PowerShell 5.1 **as Administrator**, paste the command, and execute it. Do not run it on another computer or share the command. The command invokes the HTTPS bootstrap; the bootstrap validates metadata, release artifact hashes/signature and trust material before starting the installer.
5. Leave the NightOwl page open. It polls the deployment status and distinguishes waiting, installing, failure, expiry, confirmation pending, and heartbeat-confirmed installation.
6. Treat **Instalação confirmada** as operational confirmation only when the status API reports the matching endpoint identity and a post-enrollment heartbeat for the deployed release. A local installer/service health callback alone is not a heartbeat.
7. Verify the displayed hostname, domain, machine ID, version, service state, last heartbeat and endpoint link. Then use **Nova instalação** for the next computer.

The command is intentionally available only in the create response and the active page session so the operator can copy it again. It is not returned by status/history APIs and is not written to audit metadata. Treat the command as a credential: do not put it in tickets, chat, shell transcripts, screenshots, or shared documents. The token expires after 30 minutes and is single-use.

## Result handling

- **Aguardando execução:** command has not yet consumed/enrolled the token. Execute it only on the intended endpoint.
- **Instalando:** enrollment has started. Do not create another token or rerun the command while the result is progressing.
- **Confirmação pendente:** the bootstrap reported local completion, but no matching heartbeat has been observed yet. Check endpoint network access and local bootstrap/service logs; allow normal heartbeat delivery. Do not label this endpoint operational or blindly reinstall.
- **Instalação confirmada:** the endpoint identity, release version and post-enrollment heartbeat match this deployment.
- **Falha:** use the sanitized failure code and local bootstrap log to diagnose. If the installer may have started or the outcome is uncertain, reconcile the endpoint/job before another installation.
- **Token expirado:** generate a new deployment only after confirming the old attempt did not start or has been reconciled. Never reuse the expired command.

## Local diagnostics and cleanup

The bootstrap writes its diagnostic log under `%TEMP%\NightOwlDeployment\bootstrap.log`. The log is designed to contain sanitized stages and error codes, not the token or raw library exceptions. Preserve it for diagnosis; redact host/user details before sharing externally.

After successful confirmation, the operator may remove the temporary bootstrap directory from that Windows user profile after retaining any required diagnostic evidence. Do not remove `C:\ProgramData\NightOwl` or the installed service as a cleanup shortcut.

If an installation must be reversed, first use the existing authorized NightOwl uninstall workflow and reconcile its result. Do not delete the service, enrollment identity, or program directory manually. If the deployment is failed/uncertain, stop and reconcile it before creating another token.

## Deployment and rollback checklist

1. Deploy application/static assets through the normal NightOwl release procedure; do not change agent release artifacts as part of this UI/API change.
2. Confirm the deployed application reports the correct HTTPS `NIGHTOWL_PUBLIC_URL`, a valid trust bundle, and at least one eligible signed release.
3. Verify an authorized technical operator can load both the installation page and endpoint-list entry point; an ordinary user must not be able to create or inspect deployment history/status.
4. Complete a canary installation on an approved Windows VM, confirm the heartbeat, then proceed one endpoint at a time.
5. For application rollback, restore the prior application/static release using the normal server rollback process. Do not revoke or mutate deployment tokens to mask an uncertain install. Reconcile any in-flight deployment first; existing tokens remain single-use and expire within 30 minutes.

No database migration is required for this flow.
