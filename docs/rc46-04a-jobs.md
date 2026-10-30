# RC46 DEC-04A - Local audit and implementation record

Branch feature/rc46-agent-core; base 0d5d8ceb28928b6a580cdff15a5144b373093296.
DEC-01/02/03 uncommitted changes are preserved. No production access or release.

## Initial audit (before DEC-04A edits)

- Worker awaits inventory, result transmission and job execution in the same loop.
  A slow collection delays receiving work; heartbeat/telemetry already have their
  own DEC-01/03 paths and must keep them.
- AgentJobsPullView sets started_at and emits job.started at delivery, before
  actual execution. A lost response can strand a sent job.
- UI creates ping.count and collect_logs.lines, but policy accepts target and
  max_lines respectively. These real jobs can be rejected before execution.
- JobExecutor.RunCollectionAsync stops awaiting on cancellation while its native
  task can still be running. Releasing coordination at that point is unsafe.
- The inventory PowerShell timeout kills the tree best-effort without confirming
  exit; returning null conceals an incomplete collection.
- JobStore final state is written before the result queue. Power loss in between
  can lose the exact result. A replay currently yields duplicate instead of
  necessarily resending the original evidence.
- PendingResultQueue has no same-instance thread synchronization. Existing
  critical-result capacity exemption and sent archive retention need explicit
  handling before adding concurrent uploader/executor paths.
- Existing lifecycle runners/updater handshakes own work beyond the service task.
  Running is not completion; external ownership must block conflicting execution.

## Transport decision

Repository requirements include Gunicorn; DEPLOY.md uses config.wsgi:application
on 127.0.0.1:8010. config/asgi.py is only Django HTTP ASGI, not a WebSocket router.
No approved WebSocket server, channel layer, worker sizing or upgrade proxy
contract is configured. Production was not inspected.

Implement small bearer-authenticated HTTPS signal polling with a two-second
normal interval, bounded HTTP deadlines and jittered reconnect backoff. Signals
contain availability only, never executable commands. PostgreSQL AgentJob remains
the durable source of truth. Polling supports current WSGI; persistent WebSocket
deployment remains an explicit future infrastructure decision, not a local-only
success claim. At 100 connected agents, idle two-second polling implies about
50 requests/s, to be benchmarked before production approval.

## Planned regression evidence

Tests cover isolated endpoints/auth, delivered versus started, lost delivery and
ACK replay, original result replay, bounded admission and heavy execution,
independent dispatch/upload, cancellation ownership and legacy payload aliases.
Services and software are internal typed capabilities only; software stays
disabled without a locally approved catalog. No generic shell/URL is accepted.
Real installation, lifecycle/repair and PostgreSQL races are separate gates.

## Implemented runtime

`Worker` starts one `JobRuntime` after interrupted-job recovery. Its dispatcher,
executor and result uploader are independent tasks inside the existing service.
The old blocking pull/run/flush methods were removed, not left as a second engine.
Heartbeat and continuous telemetry keep their DEC-01/03 routines.

- Dispatcher checks availability every `jobSignalSeconds` (default 2, clamped
  2..30). A shared five-second network budget covers signal plus pull. Failures
  back off with jitter, capped at about 60s. No inbound Windows port is opened.
- An authenticated signal is a hint only. The agent fetches authorized jobs from
  the existing durable backend queue. A successful check updates the existing
  LastJobPullAt checkpoint; it does not mean a job was delivered or executed.
- V2 requests use `X-NightOwl-Job-Protocol: 2`. Delivery sets SENT/dispatched_at,
  not started_at. Unacknowledged SENT work can be redelivered after 30s. Running
  jobs are never blindly reissued. Legacy agents still use their prior pull
  contract; their historical started_at must not be mistaken for new v2 evidence.
- The identity-scoped inbox atomically persists a validated UUID envelope before
  admission to execution. It allows at most 32 entries / 2 MiB; polling stops at
  24 entries or 100 pending results. Each response is limited to five jobs.
- The coordinator validates type, parameters, expiry, attempt and local history.
  The explicit start endpoint checks bearer ownership, machine ID and current
  job state under transaction locks. It is idempotent. An ACK failure does not
  authorize execution. Revoked work cannot run.
- One native/heavy execution slot is shared with periodic inventory; initially
  this is deliberately more conservative than allowing parallel light jobs.
  Inventory releases it before HTTP transmission. New work can be received and
  results transmitted while execution/collection is blocked.
- Lifecycle/software installation requires exclusivity. An external runner past
  its deadline is REVIEW-required, not fabricated as terminated. Such ownership
  blocks further execution until real completion/reconciliation evidence exists.
- Synchronous collection retains ownership after logical cancellation until the
  actual task ends. Inventory PowerShell receives the job cancellation token,
  kills its tree on cancellation/timeout and waits for exit before returning.
  A native API that refuses cancellation can still hold this slot; it must not
  be force-aborted or announced completed. That scenario remains a lab gate.

The critical state/inbox/result writes now use a targeted
`NightOwlFileStore.WriteAllTextDurable` helper: write-through temporary file,
Flush(true), then the existing atomic same-directory replacement/retry mechanism.
Other FileStore callers are unchanged. Physical power-cut/NTFS/storage-controller
behavior was not tested and is not guaranteed merely by a unit test.

## Exact results, recovery and limits

`JobExecutionPolicy` journals the exact `JobExecutionResult` before the local
final-state transition. Restart recovery can therefore queue the original result
instead of re-executing a completed operation or replacing its evidence with
`duplicate`. An interrupted/uncertain operation is not automatically retried.

`PendingResultQueue` is reused with same-instance locking, bounded critical
admission, existing payload hashes/backoff/quarantine, and an ACK archive bounded
to 1,000 files / 14 days. Critical records cannot silently bypass capacity limits.
JobStore retention prunes terminal records but preserves active/external ownership.
The regression now asserts both conditions; the old assertion counted and deleted
active/corrupt records along with completed ones.

Results use stable `job-<UUID>-<status>` idempotency IDs, five-second HTTP budgets
and up to ten due records per pass. The client checks the returned job/result IDs
before archiving. The backend also ACKs a late, ignored result by its result ID,
without changing the already-final job. ACK and applied effect are distinct.

The inbox/outcome journal shares a process lock across its instances. A recovered
outcome whose ACK is already archived is cleaned without requeueing or reexecution.
Malformed individual inbox/outcome files are isolated; healthy entries continue.
Journal quarantine is bounded to 128 files / 16 MiB. Losing an exact outcome to
corruption is a review/evidence gap, never permission to repeat a mutation.
Disk/permission failures remain fail-closed. Runtime diagnostics record categories
and exception types, not library response bodies, credentials or command output.

Existing updater healthcheck and legacy pending-result migration are retained.
Generic migration does not compete with an active update handshake. A restart
request is RUNNING, not success before the restart; confirmation requires a new
process that started after its persisted request. Repair/uninstall runners retain
their separate durable ownership rather than being treated as orphan processes.

The public creation endpoint locks the endpoint and caps pending jobs at 50,
rejecting excess requests atomically. This is not a recurring scheduler.

## HTTP and deployment compatibility

New control requests use the existing endpoint bearer, HTTPS and same configured
server origin. A named HttpClient disables redirects and retains normal TLS
validation. HTTP is permitted only for explicit loopback integration-test setup.
No NTLM/WinRM transport, certificates or firewall are changed.

Backend first, compatible agent later is required in a future approved release.
A new agent against a backend without the signal/start contract fails closed; it
does not silently fall back to unsafe execution semantics. Existing deployed
agents continue to use the legacy endpoints. The fallback in this increment is
HTTPS polling instead of persistent signaling, not an authentication downgrade.

WSGI workers are not occupied by long polling. No Redis, channel service, ASGI
server or WebSocket-only local path was added. A future persistent channel needs
an approved server/proxy/worker and shared notification design, heartbeat/replay
limits and production capacity validation. Do not deploy a development WebSocket
server as a substitute.

## collect_services (internal)

The typed payload accepts only `names` (up to ten exact service names), `limit`
(integer 1..100, default 50) and `include_details` (boolean, default false).
`ServiceController` and read-only SCM QueryServiceConfig collect name, display
name, state/startup type and optional account/executable. Unknown/access-denied
values remain explicit. Executable command-line arguments are not exposed.
The existing 64-KiB result budget still applies. No start/stop/restart service
operation is added.

`agents.internal_jobs.create_internal_job` requires a technical operator plus
add_agentjob, unambiguous endpoint identity, a matching fresh heartbeat capability
and no conflicting active job. `NIGHTOWL_SERVICE_COLLECTION_ENABLED` defaults
false. The feature/capability is rechecked on dispatch. Completion needs the
versioned read-only result contract, not an empty success response.

This capability is advertised by the development agent but has no new frontend
button. The normal frontend dispatch explicitly rejects both internal types.

## install_software (disabled prototype)

Only `package_id` is accepted. Activation needs all of:

- Backend `NIGHTOWL_SOFTWARE_INSTALL_ENABLED` (default false).
- Backend approved IDs in `NIGHTOWL_APPROVED_SOFTWARE_IDS`.
- Fresh matching agent capability and operator/identity/conflict checks.
- Agent `softwareInstallEnabled=true` and install_software in allowedJobTypes.
- An administrator-owned `software-catalog.json` beside the configured StatePath,
  pinned independently by `softwareCatalogSha256` in protected configuration.

No real catalog was provisioned. These settings are not activation instructions
for production. The initial contract accepts approved MSI, per-machine only,
fixed `/qn /norestart ALLUSERS=1`, never a browser URL/path/argument string.
Catalog entries contain id, HTTPS URL, SHA-256, exact size (<=100 MiB), MSI product
code, expected version and signer certificate SHA-256. Redirects, credentials in
URLs, query strings, size/hash mismatch and invalid catalog pins are rejected.

Authenticity uses Windows WinVerifyTrust chain/revocation policy plus the pinned
signer certificate hash. A hash alone is not treated as authenticity. There is
no signature-ignore or TLS-ignore path. The real Authenticode/MSI/EDR/revocation
matrix is NOT homologated; only negative/injected synthetic contracts were run.

An already-installed matching MSI product/version is verified without reinstall.
Before mutation, a product-scoped uncertainty journal is written. An unconfirmed
attempt blocks another installation of that product until explicit review.
The owned process is created suspended, assigned to a Windows Job Object with
kill-on-close, then resumed. Timeout/cancel terminates owned descendants and waits
for their exit. Success needs exit 0 or 3010 plus MSI product/version verification;
3010 reports reboot required and never reboots automatically.

Windows Installer service transactions can outlive a client; uncertain effects
remain blocked/review-required rather than assumed undone. No per-user installer,
generic EXE, custom properties, script, UAC bypass or security-tool change is
supported. Package approval, catalog distribution/ACL audit and real signed MSI
homologation remain prerequisites; no package was installed by this increment.

Illustrative catalog shape only (all values fictitious, not executable approval):

```json
[{"id":"synthetic-tool-1","url":"https://approved.invalid/tool.msi",
  "sha256":"<64 hexadecimal characters>","size":12345,
  "productCode":"00000000-0000-4000-8000-000000000001","version":"1.0.0",
  "signerSha256":"<SHA-256 of approved signer certificate>"}]
```

## Dashboard and migration

Jobs tab now reads `/jobs/data/` (technical role plus view_agentjob), not mock jobs.
It shows the latest 200 persisted jobs with bounded audit history, operator,
delivery/start/result timestamps, attempt, duration, errors and heartbeat context.
It refreshes every five seconds while visible, serializes refreshes and keeps the
open drawer current. Text values are escaped/textContent; no result is executed.
Mock re-run/cancel controls no longer pretend to mutate real jobs. Existing
Agenda/List/Templates previews are disabled/labeled; no scheduler was developed.

Ping count is bounded 1..10 and actually honored. Logs uses max_lines; the old
lines alias is accepted, but contradictory aliases and invalid numeric types fail.
Status filters distinguish delivery, execution and final/timeout states.

Migration0038 changes AgentJob choices only (two internal types); it is not a
destructive schema redesign. Migration0037 belongs to preserved DEC-03. Both
were applied by isolated Django test database creation, not production.

## Reproduction and gates

Use the current worktree and its isolated test settings, never production env:

```powershell
$env:DJANGO_SECRET_KEY='isolated-rc46-test-key'
$env:DJANGO_DEBUG='True'
$env:DATABASE_URL='sqlite:///:memory:'
$env:AD_AUTH_ENABLED='False'
$python='C:\Users\gabriel.oliveira\Downloads\rmm_project\venv\Scripts\python.exe'
dotnet build NightOwl.Agent.Windows.Tests -c Release --no-restore
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --jobs-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --handshake-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build
& $python manage.py test agents config dashboard.test_agent_install_workflow dashboard.test_remote_install_enrollment dashboard.test_capacity_dashboard --settings=config.settings_test --noinput
$env:NODE_PATH='C:\Users\gabriel.oliveira\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:NIGHTOWL_TEST_BROWSER_E2E='1'
& $python manage.py test agents.test_jobs_protocol_e2e --settings=config.settings_test --noinput
& $python manage.py check --settings=config.settings_test
& $python manage.py makemigrations --check --dry-run --settings=config.settings_test
node tests/frontend/test_endpoint_detail_jobs_static.js
git diff --check
```

Build and tests that load those same DLLs must run sequentially. A concurrent
rebuild/test attempt encountered Windows DLL sharing locks and was discarded;
it is not a passing gate. The integration harness now mirrors factory handler
reuse and excludes ambient proxies for loopback only. Its initial short timeout
exposed the harness connection mismatch, not evidence of remote execution success.

Test assertions were not weakened to accept incorrect state: updated heartbeat/
handshake fakes implement signal and exact ACK contracts; the browser assertion
checks actual textContent instead of CSS uppercase innerText. A transient original
Shared mutex assertion was observed on one run, then passed on rerun; its code
was not changed. It needs CI repeatability analysis, not a silently waived gate.

## Validation evidence (2026-10-30, local only)

Branch: `feature/rc46-agent-core`. Base HEAD remains
`0d5d8ceb28928b6a580cdff15a5144b373093296`. The worktree contains preserved
DEC-01/02/03 changes plus DEC-04A, all uncommitted. No production environment,
corporate endpoint, deployment, signing or release publication was used.

| Gate | Result and scope |
| --- | --- |
| Windows Release build | PASS, zero warnings/errors |
| Shared tests | PASS, including bounded queue/retention and durable store changes |
| Windows jobs-only | PASS: bounded independent dispatch, lost ACK/dedup, journals/corruption, ownership, typed contracts, synthetic software and owned-child cancellation |
| Windows regression | New jobs, continuous telemetry, hardware cadence, lightweight heartbeat, telemetry configuration and updater handshake groups passed before the known repair-runner failure |
| Complete Windows suite | FAIL at `TestRepairRunnerScriptPersistsCompletedResult`: `Repair runner pending result should be completed.`; clean-base reproduction also fails |
| Updater tests | PASS, including synthetic rollback decisions; no real endpoint update |
| Uninstaller tests | PASS in local test fixtures; no real endpoint uninstall |
| Django relevant regression suite | PASS: 679 tests, 29 skipped, 93.652 seconds; agents/config and the dashboard modules listed above, not every unrelated repository suite |
| Real .NET -> Django -> Chrome | PASS: ten real typed collect_logs jobs, exact receipts, persisted API rows and audit timeline; isolated loopback database |
| Frontend | PASS: jobs syntax, endpoint job static checks, monitoring five tests and hardware regression; actual Chrome 1920x1080 and 390x844 screenshots |
| Django check | PASS, no issues |
| makemigrations --check --dry-run | PASS, no additional changes detected |
| git diff --check | PASS |

The final browser rerun additionally checks that the closed task preview drawer
is hidden, the open result drawer fits the mobile viewport and the page has no
horizontal document overflow. A scoped jobs CSS fix corrects the mobile shell
grid specificity and hides offscreen closed drawers. Long fact values wrap.
The wide jobs table retains its own horizontal scrolling. External CDNs are
blocked in that browser harness, so missing external icons/fonts are not evidence
of their production availability. No mock jobs API was intercepted.

### Measured latency and resources

Final integrated rerun, ten prequeued jobs, cold test-process startup:

| Creation to stage | p50 (ms) | p95 (ms) |
| --- | ---: | ---: |
| Signal observed | 3649.71 | 4817.62 |
| Job received | 3854.99 | 5196.30 |
| Execution started | 5090.41 | 6928.66 |
| Exact result ACK | 5880.58 | 8138.34 |

The harness made three signal requests, reported working set 63,963,136 bytes
and total process CPU time 4,062.5 ms. This includes its startup and execution;
it is not steady-state agent CPU percentage. Socket/physical connection counts
were not measured. Named-client pooling is configured but does not prove any
particular number of network connections.

The complete regression run measured signal p50/p95 3823.09/4976.42 ms;
an earlier contended run measured p95 5013.59 ms. Thus the requested 3-5 second
signal target is locally feasible, not a guaranteed SLA. Execution and result
are later because one heavy-operation slot serializes those stages. These are
ten synthetic jobs in SQLite/HTTP loopback, not PostgreSQL, production TLS,
100 agents, a proxy or an offline fleet. Actual EventLog collection and the
owned-child timeout run locally; service contracts/software verification also
use isolated/synthetic cases and do not constitute signed-MSI homologation.

The deployed infrastructure described by the repository is WSGI/Gunicorn, with
no proven WebSocket hosting/routing layer. The implemented fallback is therefore
small authenticated HTTPS availability polling, default two seconds, with
bounded requests/backoff/jitter, followed by the authoritative job queue.
At 100 idle agents that interval implies approximately 50 signal requests/second,
not a measured capacity claim. No inbound Windows port or extra service is added.

## Pending release requirements

NO-GO for RC46 publication until all gates close. Required follow-up evidence:

- PostgreSQL migration/locking/concurrent delivery/start/result/creation tests.
  postgres/initdb/psql/Docker are not available on this environment's PATH.
  SQLite tests and PostgreSQL-specific skips are not substituted for that gate.
- Full Windows repair runner: `Repair runner pending result should be completed.`
  was again reproduced using the existing clean-base Release executable at the
  base HEAD; no source changes were made in that other worktree. Do not waive it.
- Sustained 100-agent WSGI/proxy load, TLS failure/reconnect tests and actual socket
  counts, not only loopback short-run timings. Native permanently blocked calls
  and physical power loss need separate lab testing. Heartbeat/telemetry and
  bounded ownership must remain functional while those failures are injected.
- Real lifecycle restart/update/repair/uninstall in approved disposable VMs.
- Real signed MSI verification, transaction timeout/reboot and post-install
  confirmation with an approved catalog and no endpoint rollout.
- Review cache/retention and all DEC-03 pending gates before a combined release.

No new schedules, generic script library, ConPTY terminal or DEC-04B/C/D work is
implemented. The internal boundary (signal hint -> authoritative queue -> typed
operation -> durable result) can support those future approved increments.

## DEC-04A file inventory

New agent files: Jobs/JobRuntime.cs, JobJournal.cs, OwnedProcess.cs,
SoftwareInstallation.cs, Collectors/WindowsServicesCollector.cs and
Windows.Tests/JobRuntimeTests.cs.

Agent integration: Worker, Program HTTP registration, AgentConfig, AgentApiClient,
JobExecutionPolicy/Coordinator/Executor; targeted cancellation support in the
already-modified WindowsInventoryCollector; Shared JobState/FileStore and their
focused test assertions/fakes. Updater/Uninstaller implementations and release
artifacts are unchanged.

Backend: agents/job_control.py, internal_jobs.py, test_job_control.py,
test_jobs_protocol_e2e.py, migration0038, models/urls/views; dashboard jobs data
view and queue admission; config/urls.py. Frontend: static/js/jobs.js,
templates/dashboard/jobs.html, scoped jobs rules in static/css/nightowl.css and
tests/frontend/test_jobs_protocol_e2e.cjs.
All DEC-01/02/03 changes and their documentation remain uncommitted/preserved.
