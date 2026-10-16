# Remote installation diagnostics

Admission and preflight reuse `agents.AuditEvent`; no schema change is needed.
Authorized requests produce `remote_install.admission.started/finished`, including
rejections before any batch or RemoteInstallJob exists. Batch preflight produces
`remote_install.preflight.started/finished` using the item UUID as correlation ID.
Job revalidation uses the job UUID. Batch/job IDs link the phases in metadata.
The direct preflight API also records its attempt. Discovery and status GETs do
not create attempts or contact Windows.

Search by hostname, FQDN or correlation UUID under **Admin > Agents > Audit events**,
filtering `event_type` by the `remote_install.*` events. The task drawer fetches
the latest preflight or execution failure report for its item/job in one additional query. Its info
popover opens on hover/focus/click, supports text selection and has a copy button.
Polling defers rendering while the report is open. Older attempts without these
events explicitly say that detailed diagnostics were not persisted.

Reports contain only validated target identifiers/IP, UUIDs, UTC timestamps,
fixed stage/result/error codes, allowlisted exception class names and check
statuses. Never add request bodies, usernames supplied for WinRM, credentials,
commands, stdout/stderr or library exception messages. Audit storage failure is
logged with correlation UUID and safe exception category; it does not change the
existing installation admission/retry rules. A started event without a finished
event indicates that completion was not recorded; it does not prove installation.

The probe distinguishes DNS, CA bundle validation, TCP refusal/timeout, TLS
verification/handshake, NTLM rejection/negotiation, WinRM HTTP 403 authorization,
HTTP/protocol errors, request timeout and remote execution/output stages. Local
admin role, Windows compatibility and installed-agent checks remain required.
HTTP 403 does not prove that authentication succeeded. A successful preflight is
not evidence that a later installation succeeded; the drawer shows task status
and preflight result separately. No new retry, installer or network test is run
when viewing/copying diagnostics.

Failures after preflight also emit `remote_install.execution.finished`, with the
job correlation UUID and actual execution stage. The drawer names the phase so
an earlier successful preflight cannot be confused with a successful install.

Deploy Python changes and collect static assets, then restart nightowl.service.
No migration, certificate change, WinRM configuration or agent rollout is needed.
