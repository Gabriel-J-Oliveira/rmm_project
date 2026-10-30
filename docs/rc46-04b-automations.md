# RC46 DEC-04B - Backend job automations

## Scope and baseline

Local branch `feature/rc46-agent-core`, base HEAD
`0d5d8ceb28928b6a580cdff15a5144b373093296`. DEC-01/02/03/04A work remains
uncommitted and preserved. No production access, endpoint operation, commit,
push, merge, deployment, signed artifact or release publication was performed.

Audit findings:

- AgentJob, bearer authentication, explicit v2 start, exact result receipts,
  JobStore/Coordinator/Executor and durable result queues are reused unchanged
  as the execution mechanism. Scheduling does not run in the Windows agent.
- MaintenanceRun and run_maintenance_tasks coordinate backend housekeeping;
  they are not endpoint calendar definitions. Their existing task list is not
  expanded automatically, which would silently activate remote operations.
- Existing rollout commands/timer examples are a deployment convention, not a
  generic scheduler. No Celery, Redis, ASGI/WebSocket dependency or Windows task
  is introduced.
- The real Jobs tab already reads persisted AgentJobs. Legacy human agenda,
  templates and list previews remain disabled. A real Agendamentos tab now
  replaces the disabled agenda entry without enabling those mock previews.

## Data model and migration

`agents/migrations/0039_job_automations.py` depends on preserved migration0038:

- AutomationDefinition: owner, explicit frozen endpoint identities, typed payload,
  UTC start/end, original IANA zone, calendar settings, validity/offline policy,
  state/revision, next due time and bounded retry diagnostics.
- AutomationOccurrence: one endpoint and intended instant, immutable definition
  snapshot/revision/identity/operator, expiry, materialization status/reason and
  optional protected one-to-one AgentJob. A UUID5 key and database uniqueness on
  definition/revision/time/endpoint/invocation prevent duplicate occurrences.
  Manual run-now requests additionally require a stable request UUID; replay
  returns the original occurrence without extending validity or executing again.
- AutomationSchedulerHealth: latest round start/success and per-round counts.

No existing identity, credential, inventory, telemetry or result record is
rewritten by the migration. New history relations use PROTECT. Definition owner
deletion becomes a null owner, which cannot authorize future work. Pending targets
are JSON snapshots: if a target was deleted before its first occurrence, the
whole definition is paused with definition_target_unavailable, not reassigned.

Editing uses optimistic revision checking plus a row lock. Previous occurrence
snapshots/jobs retain their original parameters and operator; a configuration edit
does not silently rewrite or cancel previously materialized jobs. Pause prevents
future materialization, not execution of already-created jobs. Cancel stops
future slots and cancels only jobs still queued, never delivered/running work.
An ended agenda is not a statement that its jobs succeeded: results are separate.

## Creation, delivery and execution

1. Authenticated technical operator creates an immediate or calendar definition.
2. Backend validates permissions, explicit set of 1..50 endpoints, identity and
   typed payload. No arbitrary command, URL, script or browser IP is accepted.
3. Immediate creation materializes directly in that transaction; it never waits
   for the periodic scheduler. The preexisting manual endpoint-job route is also
   retained for old agents and its existing actions.
4. Future definitions create no executable AgentJob before the intended slot.
5. A bounded scheduler round locks due definitions, chooses at most the latest
   due slot after downtime and transactionally creates per-endpoint occurrences
   and eligible AgentJobs. Older slots are coalesced, with the original cursor
   recorded in coalesced_from, not replayed one by one.
6. DEC-04A signal/pull/start/execute/result/ACK handles execution. The frontend is
   not required to be open. A new schedule is not a new executor or transport.

Allowlist: force_inventory, collect_disks, collect_security, collect_software,
collect_logs, ping and windows_update_scan. These existing typed read operations
are the only supported automation jobs. Logs max_lines is 1..500; ping count is
1..10 and its destination derives from the frozen endpoint hostname/domain, never
a caller-selected host. Other types have no caller-supplied parameters.

No lifecycle recurrence, repair, uninstall, update, restart, telemetry mutation,
service-control operation, generic shell, install_software or collect_services
button is enabled. DEC-04A internal flags/catalog/capability gates are preserved.

Calendar-created jobs require previously reported jobs_protocol_v2 capability
matching current machine_id/agent version. The report may be old for an offline
endpoint; actual identity, current permissions, capability, unchanged job payload,
expiry and not-before time are checked again at delivery and true start. Legacy
pulls never receive these jobs. Existing immediate/manual legacy jobs remain
compatible. A v2 agent queues durably and cannot start after expiry. A running job
is not killed or falsely finalized merely because its start window has elapsed.

## Calendar semantics

- Once: a single intended instant.
- Daily: original local wall-clock time each day.
- Weekly: selected ISO weekdays, Monday=0 through Sunday=6, at that local time.
- Monthly: chosen day 1..31; months lacking that day are skipped, not clamped.
- Interval: elapsed UTC minutes, 15..43200, anchored at the configured start.
- Optional end limits occurrence start times, not the completion time of an
  already-started job. All persisted instants are UTC; IANA zone is retained.
- Nonexistent local time at creation is rejected. A DST gap in a recurring
  calendar skips that occurrence. Ambiguous fall-back time uses fold=0 (first
  occurrence) only; it is not executed twice. Offsets explicitly provided in an
  API timestamp represent an absolute instant, converted to the retained zone.
- Resume selects the next future slot. It does not backfill a missed one-time
  schedule or replay a pause period. After downtime only the latest due slot
  is considered, and its original validity window still applies.

## Offline policy and overlap

Validity is 60..86400 seconds, default 900. The conservative default is expire.
No expired job can be newly delivered or accepted at start.

| Policy | At materialization | After the window |
| --- | --- | --- |
| wait | Queue even offline; accept reconnection only inside the original window | Expired |
| skip | If offline at processing time, record skipped without creating a job | Skipped with window_elapsed if the scheduler itself was late |
| expire | If offline at processing time, record expired without creating a job | Expired |

Online here means heartbeat within 15 minutes, not TCP reachability or proof of
successful job execution. Unknown/no heartbeat counts as offline. A long wait
window is an explicit operator choice, not an automatic next-business-day retry.
Sensitive lifecycle operations are excluded regardless of that window.

Any queued/sent/running job on the endpoint blocks a new automation occurrence;
the occurrence becomes skipped/overlap_blocked. No pending work is silently
replaced. A round expires up to 100 queued/sent automation jobs whose window ended,
even without an agent pull. Running and uncertain lifecycle work is preserved.
The default round processes up to 20 definitions, each with at most 50 endpoints;
the explicit --limit range is 1..100. Unprocessed definitions stay durable.

## Transactions, permissions and audit

PostgreSQL design: select_for_update(skip_locked=True) on due definitions,
deterministic ordered endpoint locks, per-definition atomic occurrence/job/cursor
commit, unique occurrence constraint, unique job relation and serialized queued
job cancellation. Start/result continue using DEC-04A locking and receipts.
An occurrence created once is not a claim of exactly-once physical execution.

Database lock/statement budgets are 5/30 seconds per statement in these new
transactions. Transient materialization failure rolls back partial jobs and
records retry, backoff 30/60/120/240/480 seconds, then pauses after five failures.
Resuming resets that budget. A due retry does not starve other eligible definitions
in a limited batch. If the database itself cannot store diagnostics, the command
fails and the last successful health timestamp ages; it never invents success.
Round health is observational, not a distributed lease or fleet-wide counter.

SQLite tests do not validate PostgreSQL row locks, deadlocks or worker races.
The real two-worker test is skipUnlessDBFeature gated and MUST run on PostgreSQL
before release. No PostgreSQL, initdb, pg_ctl or Docker installation was found on
PATH, Program Files or the checked local tool/download folders in this environment.
That validation remains blocked, not approved by SQLite results.

`config.settings_test_automations_postgres` accepts only an explicitly supplied
NIGHTOWL_TEST_POSTGRES_URL for a disposable loopback database named
nightowl_rc46_isolated, without URL query options or a production fallback.
After an isolated PostgreSQL instance is provisioned and its URL placed in that
process environment by the operator, the reproducible gate is:

```powershell
& $python manage.py test agents.test_automation --settings=config.settings_test_automations_postgres --noinput
```

Django creates a separate process-specific test database, applies migrations
(including0039), runs the real lock/uniqueness test and destroys that test DB.
This command has NOT been run against PostgreSQL in this delivery.

Authorization requires an active technical user and Django permissions:

- Create: add_automationdefinition + add_agentjob.
- Edit: change_automationdefinition + add_agentjob.
- Pause/resume/cancel: control_automation.
- Run now: execute_automation + add_agentjob, active definition and request UUID.
- View: view_automationdefinition + view_agentjob for occurrence/result history.

There is no inferred tenant/object scope in the existing technical job model;
permissions authorize technical access to explicitly chosen endpoints. Every
target is checked independently against its frozen machine_id/hostname/domain,
terminal lifecycle, duplicate identifiers and the four historical quarantines
(0029,0059,0133,0262). Permission/capability/identity is rechecked on creation,
materialization, delivery and start as applicable. Revocation cannot authorize a
new start; it does not retroactively erase an already-running job/result.

Automation changes/occurrences/retries use transactional AuditEvent writes, so
an audit failure cannot commit unrecorded job creation. Immediate operations retain
the actual user actor; scheduler rounds identify the scheduler and preserve the
authorizing operator. Only bounded parameters, identifiers and sanitized reason
codes are stored, not raw DB errors, bearer tokens or process credentials.

Cancel-next reserves a cancelled occurrence for each endpoint in the next slot.
Cancel-occurrence can cancel only queued work, is idempotent, and returns conflict
after delivery/start/final result. No remote cancellation is falsely reported and
no PowerShell process is terminated by the scheduler.

## Real interface

Existing /jobs/ now has Agendamentos: immediate/once/daily/weekly/monthly/interval,
IANA zone, start/end, validity, offline policy, bounded typed parameters and explicit
multi-endpoint selection. Editing, pause/resume, run-now confirmation, cancel-next,
cancel-definition and queued-occurrence cancellation use authenticated CSRF APIs.

The tables show next slot, revision, actual endpoint results, reason, retry state
and scheduler health. History filters by definition; job links open the real job
drawer through a scoped persisted lookup. Values use textContent/createElement,
not injected HTML. Timestamps display an explicit zone. Data is capped at 200
definitions/occurrences per response; the endpoint picker searches a capped set
of 500 and preserves selections. No mock fallback is used for automation failures.

## Operational activation plan - NOT executed

Prerequisites: approved combined RC46, PostgreSQL migration/locking tests, backup,
reviewed migration0039, proper role permissions and compatible canary agent.
This work does not migrate or activate production. The existing service's exact
user, Python path and protected environment must be confirmed before copying any
unit example. Do not overwrite a preexisting maintenance timer.

The only execution command is:

```bash
python manage.py process_job_automations --limit 20
```

It is disabled unless NIGHTOWL_JOB_AUTOMATIONS_ENABLED=true. A future approved
oneshot systemd unit can invoke this command under the same restricted backend
service account/environment. Suggested timer: OnBootSec=60s,
OnUnitInactiveSec=30s, AccuracySec=1s, Persistent=false; service Type=oneshot,
TimeoutStartSec=180s, NoNewPrivileges=true, PrivateTmp=true. No worker process
started from a browser, Windows task, Redis or new Windows service is needed.
Cron once per minute is also compatible, with that documented scheduling latency.
Immediate creation bypasses this cadence; later delivery uses DEC-04A polling.

The UI flags a missing/older-than-120s success heartbeat. Monitor command exit,
failed count, paused retry definitions and due backlog in normal operations.
If a process dies mid-definition, the DB transaction rolls back; committed prior
definitions remain durable and the next round resumes from their cursors.

Rollback after an approved deployment: disable/stop the automation timer first,
turn the scheduler flag off, pause/cancel definitions through authorized APIs and
review queued jobs. Turning only the scheduler flag off does NOT revoke already
queued/sent/running work. Cancel queued occurrences explicitly; delivered/running
work needs DEC-04A outcome reconciliation, never blind retry. Preserve history
tables and migration state; no drop/delete of production data is proposed.

## Local validation and reproduction

```powershell
$env:DJANGO_SECRET_KEY='isolated-rc46-test-key'
$env:DJANGO_DEBUG='True'
$env:DATABASE_URL='sqlite:///:memory:'
$env:AD_AUTH_ENABLED='False'
$python='C:\Users\gabriel.oliveira\Downloads\rmm_project\venv\Scripts\python.exe'
& $python manage.py test agents.test_automation agents.test_automation_protocol_e2e --settings=config.settings_test --noinput
$env:NODE_PATH='C:\Users\gabriel.oliveira\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:NIGHTOWL_TEST_BROWSER_E2E='1'
& $python manage.py test agents.test_automation.AutomationBrowserTests --settings=config.settings_test_browser --noinput
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --hardware-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --jobs-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --continuous-only
```

Browser settings use a process-specific temporary SQLite file with a lock timeout,
not the production DB. Django creates/destroys it. An in-memory live-server rerun
failed with sqlite3.InterfaceError under concurrent HTTP threads; the isolated
file-backed rerun passed. No functional assertion was relaxed. Screenshots are
in the user's Temp directory: nightowl-automations-1920.png and
nightowl-automations-390.png. External CDNs are blocked in this test harness, so
external icon/font loading is not homologated by these screenshots.

One compatibility bug surfaced in DEC-04A's collector cancellation integration:
the optional enrichment timeout propagated OperationCanceledException instead of
missing evidence. The minimal fix still kills/waits the owned PowerShell tree,
then propagates an EXTERNAL job cancellation, but returns null for the internal
enrichment budget. Existing hardware timeout assertions are preserved and a new
regression verifies external cancellation. No updater/uninstaller change is made.

### Final results (2026-10-30)

| Check | Result |
| --- | --- |
| Relevant Django regression (agents, config, install workflow/enrollment, capacity) | PASS, 713 tests, 31 skipped, 95.738s; includes DEC-01/02/03/04A regressions, not all unrelated repository apps |
| Final focused automation + job-control + real protocol/browser, file SQLite | PASS, 48 tests, 1 skipped, 14.671s; skip is real PostgreSQL concurrency |
| Actual calendar -> Django -> .NET -> exact result receipt | PASS, local read-only collect_logs, no execution before intended slot, one job after repeated rounds |
| Actual Chrome -> authenticated/CSRF Django API | PASS, immediate/future creation, pause/resume, immutable edit revision, future cancellation, recurrence controls, XSS, history and job drawer deep link |
| Desktop/mobile visual checks | PASS, 1920x1080 and 390x844, no document overflow; tables scroll within their own containers |
| Node syntax and existing endpoint job/hardware/XSS checks | PASS |
| Release Windows build after compatibility fix | PASS, 0 warnings/errors |
| Windows hardware-only + lightweight heartbeat | PASS, including internal timeout fallback and external cancellation regression |
| Windows jobs-only / continuous-only | PASS; complete Windows rerun also passed those groups and telemetry configuration before the known failure |
| Complete Windows suite | FAIL, unchanged known repair-runner assertion: Repair runner pending result should be completed. No assertion skipped or weakened |
| Django check | PASS |
| makemigrations --check --dry-run | PASS, no further changes beyond0039 |
| git diff --check | PASS |
| Real PostgreSQL migrations/locking/races | NOT RUN, no disposable PostgreSQL runtime available; release-blocking |

The 713-test run used the normal isolated in-memory settings and skipped the
explicit browser test; the separate final 48-test run enabled Chrome using the
file-backed isolated settings. Neither is production evidence. Intentional
negative cases log expected CSRF/identity errors. A missing staticfiles directory
warning is expected: browser test serves repository static files directly.

Decision: local functional implementation complete; **NO-GO for RC46 publication
and scheduler activation**. Required next evidence: provision disposable
PostgreSQL, run real migrations/uniqueness/two-worker/cancel-versus-dispatch tests,
then close the preexisting repair-runner and DEC-03/04A release gates. The current
PostgreSQL test covers two scheduler instances; broader cancellation/deadlock/load
homologation is still required, not inferred from that unexecuted test.

## DEC-04B file inventory

New: agents/automation.py, agents/management/commands/process_job_automations.py,
agents/migrations/0039_job_automations.py, agents/test_automation.py,
agents/test_automation_protocol_e2e.py, dashboard/automation_views.py,
templates/dashboard/job_automations.html, static/js/job_automations.js,
tests/frontend/test_job_automations_e2e.cjs, config/settings_test_browser.py,
and this report.
Additional isolated gate settings: config/settings_test_automations_postgres.py.

Integration: agents/models.py, agents/views.py, agents/job_control.py,
dashboard/views.py, config/urls.py, config/settings.py, .env.example,
templates/dashboard/jobs.html, static/js/jobs.js, scoped static/css/nightowl.css.
Compatibility fix: WindowsInventoryCollector.cs and Windows.Tests/Program.cs.
All these shared files already contained preserved DEC-01/02/03/04A changes.

No DEC-04C action/script catalog or DEC-04D terminal is implemented. No services,
software, security or diagnostic page/activation is added. The RC46 publication
block remains: PostgreSQL concurrency/migration evidence, known repair-runner
failure and outstanding DEC-03/04A load/lifecycle/signed-MSI gates.
