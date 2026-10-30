# RC46 internal increment 01: lightweight heartbeat (DEC-01)

Date: 2026-10-29. Development only; not a published RC or a deployment authorization.

Branch: `feature/rc46-agent-core`.
Integrated base: `0d5d8ceb28928b6a580cdff15a5144b373093296`.
OBZ, recovery, bootstrap and other worktree changes are not included.

## Contract

Previously BuildHeartbeat called GetSystem, GetHardware and GetNetwork. The first
two start PowerShell/CIM and repeat mostly static hardware and OS inventory.
The new builder uses native local hostname, DNS suffix and interface APIs, native
uptime, running agent version and component file versions. It never resolves a
hostname through DNS or queries AD, WMI/CIM, PowerShell, hardware or telemetric
counters. The local DNS suffix is not independent proof of AD membership: the
server still checks the authenticated identity and rejects contradictions. An
unknown suffix/FQDN is omitted, not replaced with a guessed domain.

Sanitized OLD shape (abridged, synthetic):

```json
{
  "machine_id": "synthetic-machine",
  "hostname": "CS-LAB",
  "domain": "example.test",
  "fqdn": "CS-LAB.example.test",
  "logged_user": "EXAMPLE\\some-user",
  "os_name": "Windows 11 Pro",
  "hardware": {"cpu": "Example CPU", "memory_total_bytes": 17179869184},
  "agent_version": "example-previous-version"
}
```

Sanitized NEW shape (abridged, synthetic):

```json
{
  "schema_version": 1,
  "agent_id": "synthetic-machine",
  "machine_id": "synthetic-machine",
  "hostname": "CS-LAB",
  "domain": "example.test",
  "fqdn": "CS-LAB.example.test",
  "agent_version": "example-internal-version",
  "agent_mode": "dotnet-service",
  "uptime_seconds": 100,
  "agent": {
    "service_name": "NightOwlAgentDotNet",
    "mode": "dotnet-service",
    "capabilities": ["configure_telemetry"]
  },
  "heartbeat_at": "2026-10-29T12:00:00Z",
  "timestamp": "2026-10-29T12:00:00Z"
}
```

Existing IPs and essential component metadata remain supported. Worker continues
to announce supported telemetry configuration and its effective settings when
available. No interactive user is inferred from the SYSTEM account. OS/hardware
and interactive-user fields are omitted unless genuinely provided by an older
client; the builder does not send nulls/placeholders for uncollected inventory.

## Backend compatibility and provenance

No database schema change is needed. AgentMachine retains last valid inventory
and component fields when a heartbeat omits them or supplies unknown empty/null
values. Nonempty valid values from legacy heartbeats still update projections;
unknown memory size zero does not replace a known physical memory total.

InventorySnapshot still records a distinct current communication with
`snapshot_source=heartbeat`, its actual receipt time, root machine_id and agent
version. Cached inventory columns are preserved. `raw_payload.collections` and
its original per-section collected_at are not refreshed by heartbeat. The new
server-produced `raw_payload.heartbeat_inventory` distinguishes observed fields,
explicit unknowns and inherited values, including their prior source/time when
known. If a source time is unknown, it remains unknown. A cached projection is
not a new hardware measurement. Previously persisted snapshots are not edited.

Existing identity checks, bearer authentication, controlled rename, version and
last_seen updates, lifecycle rules and deployment gates remain in place. A
collection cannot replace the post-enrollment heartbeat required by deployment.

## Runtime isolation and remaining limits

One dedicated async heartbeat loop runs in the same Windows service, on a
separate Task, with the existing configurable interval (minimum five seconds)
and a 30-second cancellable HTTP budget. It does not wait for inventory or jobs.
One atomic timestamp is handed back to the main Worker; that Worker remains the
only writer of AgentState. If the main loop is blocked, persistence of that
checkpoint is delayed; restart can cause an extra heartbeat, not job replay.

All existing deep collection entrypoints used by Worker/jobs share a reentrant per-collector gate.
A caller's logical timeout does not release this gate while actual collection
continues. A different thread is rejected as busy without creating a queue of
waiting collection threads. Nested security/software calls remain valid. This does not introduce
new parallel jobs or modify job validation, results, types or deduplication.

DEC-04 limits remain explicit: inventory/jobs are still sequential in the main
loop; cancellation of a synchronous native/CIM collection is not preemptive;
shutdown and job polling can still wait for it. The heartbeat's cancellation is
linked directly to service shutdown, even while the main loop is blocked.
OS-wide resource starvation, filesystem faults and native API stalls are not a
hard real-time guarantee. No new service, generic event bus or executor exists.

## Local verification

Use an isolated test database and synthetic HTTP handlers only. No endpoint
operation, production database, real LDAP or real WinRM is needed.

```powershell
$env:DJANGO_SECRET_KEY = 'isolated-rc46-test-key'
$env:DJANGO_DEBUG = 'True'
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:AD_AUTH_ENABLED = 'False'
python manage.py test agents config dashboard.test_agent_install_workflow dashboard.test_remote_install_enrollment dashboard.test_capacity_dashboard --settings=config.settings_test --noinput
python manage.py check --settings=config.settings_test
python manage.py makemigrations --check --dry-run --settings=config.settings_test
dotnet run --project NightOwl.Agent.Windows.Tests -c Release -- --lightweight-heartbeat-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release
git diff --check
```

The new Windows tests build a real heartbeat and statically reject heavy/DNS
collection calls. Real Worker instances use synthetic blocking collectors and
fake HTTP handlers to verify multiple scheduled heartbeats during both periodic
inventory and a force_inventory job. They verify interval timing, inventory
failure isolation, single collection ownership after logical timeout and
preservation of persistent lifecycle/extension fields. Django tests cover old
and light/null heartbeats, provenance across 35 heartbeats, valid partial updates,
unknown initial inventory, identity rejection, rename and real enrollment/API
heartbeat confirmation inside a mocked remote deployment.

## Future integration and rollback (not executed)

Review and integrate the backward-compatible backend before distributing an
agent using this contract. Roll back by reverting this increment before release;
no data restoration or destructive migration is needed. Old clients remain
accepted. Keep this work internal until the remaining approved RC46 increments
and complete release gates pass. Do not build/sign/publish an RC, dispatch update
jobs or proceed automatically to the next architecture decision.

## Execution results, 2026-10-29

- Django regression: 637 tests, OK, 29 skipped (including PostgreSQL-only gates;
  these were not replaced with SQLite assertions).
- Lightweight heartbeat: four new Windows groups plus ten existing focused
  identity/state/hardware groups passed. Real Worker isolation used fake HTTP
  and synthetic blocked collection/job callbacks, not a production endpoint.
- Focused telemetry (ten groups), telemetry configuration and update handshake:
  passed. Shared, Updater and Uninstaller console test projects: passed.
- Local .NET compilation: passed. Django check and makemigrations dry-run:
  passed, no schema changes. git diff --check: passed.
- Complete Windows suite: FAILED at the existing
  TestRepairRunnerScriptPersistsCompletedResult assertion:
  `Repair runner pending result should be completed.` The same command fails
  at the same assertion in an untouched detached control worktree at base
  0d5d8ceb28928b6a580cdff15a5144b373093296. This increment does not change repair
  implementation or its expectations. The complete Windows gate is NOT green.
- No official release builder, packaging, signing, upload, deployment, service
  restart, real enrollment, endpoint update or next increment was performed.
  No commit or push was made. The feature worktree holds the uncommitted diff;
  the clean control worktree is retained for reproducing the preexisting gate.

Release readiness remains NO-GO until the outstanding Windows gate and the
remaining approved RC46 increments are completed. This is not a pilot release.
