# Installation Confirmation and Capacity

Production audit on 2026-10-09, baseline 785b9dc:

- CS-CVEL-0253 exists once under endpoint f2dbf5da-8a4f-4002-86c2-c4a3c9a73505.
  Capacity includes it even with an empty lifecycle and zero performance samples.
- Enrollment: 12:23:18 UTC. First persisted heartbeat: 12:23:30 UTC.
  Full inventory: collected 12:23:30 UTC, received 12:24:06 UTC.
  Job de85124d-b93a-4957-8901-d5a7a49608e8 timed out at 12:26:53 UTC.
  Thus cadence alone does not explain this failure.
- Heartbeat/collection receipt updates last_seen_at, not the persisted status.
  The old runner additionally waited for status=online, depending on the periodic
  status updater. Confirmation now requires a recent, persisted heartbeat with
  the expected endpoint, machine identity and version instead of that cached flag.
- The default wait is 420 seconds (previously 180). A timeout can hold the existing
  installation slot for up to 240 additional seconds per target. Concurrency is
  unchanged; successful confirmations still finish immediately. Deployment expiry
  and unknown-outcome hold already derive from this configured timeout.
- Capacity queries are unchanged. No lifecycle/telemetry filter was added.
  Zero samples are shown as waiting for performance samples; p95 stays unavailable.
  Partial evidence remains distinct. The static script URL version is updated.

## Historical Confirmation Preview

Use `dashboard.remote_install.preview_install_confirmation(job_id)` in a read-only
database transaction. It requires installer success, validated running service,
deployment/release/job binding, enrollment, unambiguous endpoint identity, correct
version and a persisted heartbeat after enrollment. It returns evidence and reasons.
It never updates jobs, deployments, lifecycle or audit history; no remote connection
is made. Eligibility is a preview, not authorization to repair historical records.

The 0253 history must remain INSTALLED_UNVERIFIED / HEARTBEAT_TIMEOUT and deployment
failed until an operator approves an explicit, audited historical reconciliation.
Any future apply must revalidate the preview under locks, retain original failure
timestamps/error/history and record actor, reason and heartbeat snapshot evidence.
Do not reinstall or create another job to repair bookkeeping.

CS-CVEL-0254 remains RC45, with existing telemetry disabled as externally confirmed.
No server-side flag is invented from missing samples. This change does not alter
Windows binaries, configuration, releases or rollout policy.
