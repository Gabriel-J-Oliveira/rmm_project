# RC46 DEC-03 - Continuous telemetry, workload and live monitoring

## Status and scope

Local implementation and isolated tests, 2026-10-29. Branch
`feature/rc46-agent-core`, base HEAD
`0d5d8ceb28928b6a580cdff15a5144b373093296`. DEC-01/02 changes remain
uncommitted alongside DEC-03. No production access, endpoint installation,
release, signature, deployment, commit or push was performed.

**Release decision: NO-GO.** Functional local gates below pass, but the full
Windows gate still fails on the preexisting repair runner, also reproduced
on the clean base. PostgreSQL migration/concurrency and service-level fleet
homologation remain required. This is not a declaration of full DEC-03
homologation and does not start DEC-04.

## Architecture and data path

One existing Windows service hosts independent heartbeat, inventory, jobs
and telemetry routines. `TelemetryPipeline` selects its existing legacy
path or `ContinuousTelemetryPipeline`; no additional service or executor.

1. A single native reader runs approximately every 10 seconds. The budget is
   5 seconds; a timed-out native operation retains ownership until it really
   ends. A subsequent read is not started concurrently with a blocked read.
2. `TelemetryMinuteAggregator` closes UTC minute windows. It accepts at most
   one observation per 10-second slot, with six expected observations. Missing
   observations stay missing. Clock regression discards the uncertain open
   window explicitly; suspension does not fabricate intervening windows.
3. A bounded channel of 16 completed windows separates sampling from the
   durable writer. A stalled writer cannot block the sampler; overflow is
   counted and logged as lost coverage.
4. The writer atomically persists one immutable JSON file per minute. Only
   persisted windows are eligible for reliable upload. A single uploader
   sends bounded batches and removes only server-ACKed identities.
5. In parallel, a volatile ring retains up to 180 native readings / 30 minutes.
   Live sessions use these exact readings, never another sampler.
6. Bearer-authenticated outbound HTTP uses the existing agent client/server
   configuration. No inbound port, WinRM, remote script or job is needed.
7. Django stores minute evidence, evaluates workload and exposes existing
   Capacity APIs. The endpoint page uses a separate short-lived control
   lease and shared cache for live points, not permanent raw-point rows.

## Native collectors and semantics

- CPU uses `GetSystemTimes` deltas. Initial, suspended and abnormal intervals
  are unknown; machines with multiple processor groups are explicitly not
  reported as a reliable whole-machine CPU measurement. No invented per-core
  data. Real elapsed time is monotonic.
- RAM uses the existing native APIs: physical total/used/available and commit
  used/limit are separate. Page Reads/sec is PDH evidence, not inferred from
  commit. An unavailable metric is omitted, not zero.
- Persistent PDH uses English counter identifiers and wildcard disk instances.
  Rates, operations, latency, queue and valid idle-derived activity are retained.
  `_Total` is excluded. Instance identifiers are hashed; metadata says Windows
  disk instance, with unverified physical-device association. RAID/VM/Storage
  Spaces are not asserted to be physical-device identities.
- Network uses per-interface .NET counters, stable hashed interface IDs,
  elapsed-time deltas and nominal link speed. Counter reset, disconnection and
  invalid intervals invalidate rates. RX/TX and interfaces are not indiscriminately
  summed. Virtual/tunnel heuristics suppress untrustworthy utilization; they are
  not authoritative hardware classification. Link utilization is not internet
  saturation. Counter values above 100 are not blindly clamped into valid evidence.
- Processes are collected every five minutes, not every native read. Capacity
  retains the last nonempty observation and exposes its collection window time.
  This is a past observation, not proof of causal impact or current execution.
- Self-instrumentation includes process CPU, working set, group durations,
  failures/timeouts, dropped windows, clock loss, inventory PowerShell starts
  and acknowledged serialized payload bytes. The latter excludes HTTP/TLS
  overhead and retransmitted wire bytes; PowerShell count is inventory-scoped,
  not a claim about every existing job.

## Contracts

Legacy v1 samples keep their original semantics and 300-second default. They
are neither renamed as minute windows nor deleted by v2 retention. A backend
with v2 support continues to accept v1 agents.

Synthetic raw reading example (volatile ring/live only):

```json
{"sample_id":"00000000-0000-4000-8000-000000000001",
 "collected_at":"2026-10-29T12:00:10Z",
 "metrics":{"cpu.usage_percent":95,"memory.used_percent":60},
 "quality":{"cpu":"valid"}}
```

Synthetic complete minute example (one metric for brevity):

```json
{"schema_version":2,"machine_id":"synthetic-machine",
 "windows":[{"window_id":"00000000-0000-4000-8000-000000000002",
 "start_at":"2026-10-29T12:00:00Z","end_at":"2026-10-29T12:01:00Z",
 "resolution_seconds":60,"expected_samples":6,"received_samples":6,
 "metrics":{"cpu.usage_percent":{"count":6,"sum":570,
 "min":95,"max":95,"weight":60,"weighted_sum":5700,
 "distribution":{"95":60}}},"quality":{}}]}
```

Production window IDs are deterministic from machine ID and UTC minute.
Mean is weighted_sum / weight. Rate weights use effective elapsed duration;
disk latency uses operation weights. Percent histograms preserve duration in
one-percentage-point bins. Rollups merge counts, weights, sums, extrema and
histograms, never averages of percentiles. Quantiles are approximate to the
histogram resolution. Instantaneous memory readings use a documented step
estimate, not proof of every intermediate instant.

Ingestion validates strict field/type/count/time/size/finite-value contracts,
rejects contradictory window identity atomically and ACKs safe replay. After
rollup, retained source hashes still recognize identical original windows.
Mixed histories identify their sources; v2 diagnostics do not numerically blend
unlike v1 observations into fictitious minute evidence.

## Buffers, limits and recovery

- Durable queue: default 128 MiB, eight days, at most 11,520 windows, namespaced
  by machine identity. Configurable bytes are bounded 1..512 MiB; age 1..192h.
- One atomic file per minute, no full queue rewrite every ten seconds.
- Batches: up to 24 windows / approximately 200 KiB; ingest accepts up to 24.
- Upload approximately 60s, 10s HTTP budget, bounded 10..300s retry backoff.
- Raw live ring: up to 180 points / 30min; live POST up to 12 / about 100 KiB.
- Corrupt syntax/semantics are quarantined without blocking healthy windows.
  Quarantine itself is bounded at 128 files / 16 MiB. Expiry/space/channel loss
  is counted/logged; coverage gaps remain visible. Local counters are not a
  durable central incident ledger across service restarts.
- Lost ACK keeps files for idempotent replay. Only sent-and-ACKed IDs delete.
- Shutdown completes the channel and allows up to five seconds for the writer.
  Open partial minute and raw ring are volatile and explicitly logged as such.
  A blocked writer keeps ownership; reload cannot create competing uploaders.
- Eight-day retention is a target within the byte cap, not a promise for every
  disk/interface topology. A larger payload can reach the byte limit sooner.

## Live sessions and server cache

Routes under `/api/agent/telemetry/`: `control/`, `live/` (agent points),
`live/<endpoint UUID>/` (operator control/read), plus existing summary ingest.
Agent control polling is 30s idle / 10s active; point delivery approximately
5s, based on the same 10s samples. It can take an idle poll to discover a session.

Operator access requires technical role plus endpoint and performance view
permissions; writes use existing session authentication and CSRF. One owner
per endpoint, endpoint row locking, 45s renewable lease and 15min hard expiry.
The visible browser renews about every 15s and polls every 5s. A hidden/closed
browser cannot keep the lease alive indefinitely. Cache points expire in 90s;
stale >25s is not presented as live. No raw points enter PostgreSQL.

The existing application had no explicitly configured shared live cache.
Implementation uses an isolated Django FileBasedCache alias on a single host,
not Redis, another process or a database cache table. LocMem/dummy and public
media/static paths are rejected. Cache failure returns unavailable, not a fake
local success. Separate-process cache reads were tested; database locking still
requires PostgreSQL validation. Multi-host deployment needs another approved
shared cache design; local files must not be claimed to span hosts.

Future Linux deployment requires a private service-owned mode-0700 directory
outside all web-served trees. `NIGHTOWL_TELEMETRY_LIVE_CACHE` example:
`/var/cache/nightowl/telemetry-live`. Do not use world-writable cache directories:
Django file cache uses serialization and must remain trusted. No directory or
service was provisioned in production by this task.

## Historical retention and workload

Migration `agents/0037_continuous_telemetry.py` adds version/resolution/window
end/statistics fields, an endpoint-window-resolution conditional unique
constraint and a single live lease per endpoint. Uptime can be unknown rather
than invented as zero. Database defaults preserve old-model inserts into the
new schema, tested on isolated SQLite. PostgreSQL migration/locking is pending.

Retention defaults: 1min for 7d, 5min for 30d, 1h for 90d. Environment settings
`NIGHTOWL_TELEMETRY_RETENTION_1M_DAYS`, `..._5M_DAYS`, `..._1H_DAYS` configure them.
History chooses matching resolution for 30/90d; merging keeps weighted evidence.

`python manage.py compact_telemetry` is read-only dry-run.
`python manage.py compact_telemetry --apply --limit 500` mutates only v2 retention
in the selected database; endpoint locking coordinates with ingest. This command
is implemented/tested, but **no production schedule is installed**. An approved
future deployment must schedule and monitor it, e.g. every 15min with bounded
work and drain runs. Never test `--apply` against production in this increment.

Backend-only `NIGHTOWL_WORKLOAD_THRESHOLDS` controls conservative policies:
CPU >=85%, low available RAM <=10% corroborated by commit >=90% or paging >=5/s,
disk latency >=25ms plus queue >=1 and actual operations, interface RX/TX >=85%.
Two consecutive complete minute windows are required by default; disk/NIC
identity must match. Isolated spikes, incomplete windows and absent counters
are not upgrade recommendations. Policies retain episodes, duration and coverage.
Immediate summary ingestion evaluates warning alerts using existing audit/alert
infrastructure. Existing acknowledged alerts are not overwritten. Automatic
resolution is not added; these alerts require the established operator lifecycle.

## Activation, compatibility and rollback

New `AgentConfig` objects default to telemetryMode `continuous`, but persisted
legacy JSON with no mode explicitly loads as `legacy`, preserving the deployed
cadence. An installer/config that never writes telemetryMode will therefore not
silently activate the new path. No signed installer or artifact was changed.
Activation on a future approved lab agent requires explicit `telemetryMode`:
`continuous`, enabled telemetry and the new binary/backend. Queue settings are
`telemetryWindowMaxBytes` and `telemetryWindowMaxAgeHours`.

Existing configure_telemetry enable/disable remains supported. Its old sample/
flush numeric fields describe the legacy profile; continuous mode is fixed at
10/60s in this increment and the UI marks those legacy fields read-only. No new
job payload or remote cadence-management contract was introduced.

Old v1 disk buffers are preserved, not blindly migrated/replayed by v2 because
their historical identity provenance is insufficient. Drain/reconcile them in
legacy mode before an approved profile switch. Changing mode does not erase
v1 evidence. A service restart still loses the volatile recent/open-minute tail.

Rollback plan for future homologation: stop/expire live sessions, revert the
agent profile to legacy under an approved procedure and preserve both queues.
Keep the additive migration and v2-aware readers. Do not blindly reverse 0037:
that discards statistics and can fail on unknown uptime. An old backend can
insert with DB defaults but its old analytics do not understand v2 windows;
binary rollback alone is not a transparent reporting rollback. Backup the lab
database/queue first; production rollback needs a separately approved plan.

## Local test evidence

- Django regression: **PASS**, 664 tests, 29 skipped, 86.983s. Includes agents,
  config, installation workflow/enrollment, Capacity and DEC-01/02. Expected
  negative tests log rejected synthetic requests; no production requests.
- .NET Release build: **PASS**, zero warnings/errors.
- Continuous .NET suite: **PASS**; two-minute synthetic peak, UTC boundaries,
  weighted latency, holes/clock regression, bounded persistent queue/restart,
  partial ACK, lost ACK, syntax/semantic/histogram corruption, size/age,
  network reset and slow HTTP/native ownership isolation.
- Full Windows suite: **FAIL**, `TestRepairRunnerScriptPersistsCompletedResult`,
  first assertion `Repair runner pending result should be completed.` Same
  failure reproduced in clean detached base at the same commit; not waived.
- Shared / Updater / Uninstaller suites: **PASS**, unchanged implementations.
- Actual loopback HTTP .NET -> Django -> Chrome with CSRF: **PASS**. Two synthetic
  minute rows persisted, two raw live points cached only. Prints
  `PASS_REAL_DOTNET_DJANGO_BROWSER_LIVE_WITH_CSRF=YES`. This tests real transport
  and UI with synthetic measurements, not a deployed Windows service.
- Live frontend: **PASS**, real Chrome synthetic fixture at 1920x1080/390x844,
  start/charts/selectors/stop/expiry, gaps and escaped hostile labels.
- Monitoring frontend: **PASS**, five tests including responsive rendering/XSS.
- Hardware frontend: **PASS**, DEC-02 data/provenance and hostile input escaping.
- Capacity frontend: **PASS**, 20/20 real Chrome fixture tests, including
  filters, pagination, refresh ownership, responsive layout and XSS. Set
  `PLAYWRIGHT_CHANNEL=chrome` when bundled Playwright Chromium is not installed;
  missing browser/runtime attempts were environment errors, not passing gates.
- Embedded hardware PowerShell/CIM regression: **PASS** on Windows PowerShell
  5.1 with synthetic CIM. This is not validation of PDH on multiple OS languages.
- JavaScript syntax and `git diff --check`: **PASS**.
- Django check / migration state check: **PASS**; no extra migration generated.
- PostgreSQL-only tests/concurrent locks: **NOT VALIDATED** locally. No postgres,
  initdb or Docker executable available. SQLite success is not substituted.

Reproduction (PowerShell, this worktree, isolated test config):

```powershell
$env:DJANGO_SECRET_KEY='isolated-rc46-test-key'
$env:DJANGO_DEBUG='True'
$env:DATABASE_URL='sqlite:///:memory:'
$env:AD_AUTH_ENABLED='False'
$python='C:\Users\gabriel.oliveira\Downloads\rmm_project\venv\Scripts\python.exe'
dotnet build NightOwl.Agent.Windows.Tests -c Release
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --continuous-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --hardware-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --telemetry-only
& $python manage.py test agents config dashboard.test_agent_install_workflow dashboard.test_remote_install_enrollment dashboard.test_capacity_dashboard --settings=config.settings_test --noinput
$env:NODE_PATH='C:\Users\gabriel.oliveira\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:PLAYWRIGHT_CHANNEL='chrome'
$env:NIGHTOWL_TEST_BROWSER_E2E='1'
& $python manage.py test agents.test_telemetry_protocol_e2e --settings=config.settings_test --noinput
node tests/frontend/test_endpoint_live.cjs --visual
node tests/frontend/test_endpoint_monitoring.cjs
node tests/frontend/test_endpoint_hardware.cjs
node dashboard/test_capacity_analysis.cjs
& $python manage.py check --settings=config.settings_test
& $python manage.py makemigrations --check --dry-run --settings=config.settings_test
git diff --check
```

## Resource measurements and limitations

Actual local Windows console harness, seven native reads ten seconds apart:
average read 51.37ms, maximum 120.97ms, process CPU average 0.109%, maximum
individual-read CPU average 9.434%, maximum working set 51,068,928 bytes.
Six valid CPU deltas, seven memory reads, 47 disk metric values, no PowerShell
starts, one closed minute, maximum minute payload 7,699 bytes. Total serialized
read payload 15,791 bytes. This is NOT a service-under-load benchmark or an
instantaneous CPU peak measurement. Reproduce only locally with
`--continuous-native-minute`; it reads native counters and does not install.

Estimate from this one host: 7,699 bytes/minute is about 10.57 MiB/day and
84.58 MiB/eight days before transport overhead. Other device counts differ.
Persistent write strategy is one new summary file/minute plus ACK deletion;
physical disk write amplification was not measured.

Synthetic load: 100 endpoint-minute ingests into isolated SQLite in 0.998s in
the final broad run. This is not PostgreSQL capacity or a 100-agent soak test.

Still required: PostgreSQL migrations/constraints/transactions and multiworker
race tests; Windows service cancellation/reload under load; multi-language PDH,
VM/RAID/Storage Spaces/processor-group hardware matrix; longer offline/restart
soak, fleet payload sizing, explicit configuration migration/activation and the
preexisting repair-runner release gate. No automatic DEC-04 work begins.

## File inventory

Agent new: `Models/ContinuousTelemetry.cs`, `Collectors/ContinuousNativeReader.cs`,
`Collectors/NativeDiskCounters.cs`, `Services/TelemetryMinuteAggregator.cs`,
`Services/TelemetryWindowQueue.cs`, `Services/ContinuousTelemetryPipeline.cs`
under `NightOwl.Agent.Windows`; `ContinuousTelemetryTests.cs` in Windows.Tests.

Agent integration: TelemetryCollector, WindowsInventoryCollector instrumentation,
AgentConfig, ConfigService, TelemetryPipeline, AgentApiClient, Worker and Windows
test Program. Some already contain preserved DEC-01/02 work.

Backend new: `agents/telemetry_continuous.py`, `telemetry_history.py`,
`telemetry_live.py`, `management/commands/compact_telemetry.py`, migration0037,
`test_continuous_telemetry.py`, `test_telemetry_protocol_e2e.py`.
Backend integration: agents models/urls/telemetry_views/telemetry_analytics/
telemetry_configuration/alerting, config/settings, .env.example, dashboard views,
capacity_views and focused capacity regression test.

UI: new `static/js/endpoint_live.js`, `tests/frontend/test_endpoint_live.cjs`,
`test_live_protocol_e2e.cjs`; endpoint template, scoped CSS, monitoring and
Capacity JS. The preexisting endpoint_detail hardware changes are preserved.
No jobs engine, Updater/Uninstaller implementation, enrollment or release
signature changes are introduced by DEC-03.
