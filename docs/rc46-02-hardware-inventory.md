# RC46 / DEC-02 - Hardware inventory

Date: 2026-10-29. Local implementation only.

- Worktree: `C:\Users\gabriel.oliveira\.codex\worktrees\rc46-agent-core\rmm_project`
- Branch: `feature/rc46-agent-core`
- Base HEAD: `0d5d8ceb28928b6a580cdff15a5144b373093296`
- DEC-01 pending changes preserved in the same worktree. No commit, release,
  signing, publication, deployment or endpoint operation.

## Cadence and ownership

| Group | First authenticated service cycle | Periodic cadence | On demand |
| --- | --- | --- | --- |
| Basic system, manufacturer/model/serial, system RAM total | Yes | 24 hours | Existing force_inventory |
| Detailed CPU/board/BIOS/OEM, physical RAM, battery, physical disks | Yes | 7 days | Existing force_inventory |
| Mounted volumes/free space, volume health and BitLocker projection | Yes | 1 hour | Existing collect_disks / force_inventory |
| Full native network interfaces, configured DNS | Yes | 24 hours | Existing force_inventory |
| Software/security/patch sections | Yes | Existing collectSeconds (default 1 hour) | Existing jobs preserved |
| Heartbeat | DEC-01, independent | Existing heartbeat setting | No inventory calls |

Initial inventory is the first Worker cycle with a valid enrolled credential;
it is not an unauthenticated enrollment side effect. Cadences use independently
persisted successful group checkpoints. New/legacy state without checkpoints
collects all groups once. Future checkpoints become due after clock correction.
Group checkpoints move only after HTTP acknowledgement and successful collection
of that group. Partial failure does not complete the detailed checkpoint.

The main loop still polls normally; it does not sleep for a whole cadence.
Pending failed groups have a five-minute admission floor after an acknowledged
partial report (or the shorter existing collectSeconds when explicitly configured);
HTTP failures also encounter the existing main-loop delay.
There is no new retry engine or unbounded collection queue. Config `collectSeconds`
continues to drive software/security/patch collection; it does not override the
new hardware/network/volume cadences.
An on-demand force_inventory retains its full payload/contract and does not move
the periodic Worker checkpoints, so a nearby periodic round can still occur.

The DEC-01 nonblocking/reentrant gate still owns actual deep collection work.
The main inventory/job loop remains sequential. Heartbeat isolation tests block
and fail synthetic inventory and block a force_inventory job while heartbeats
continue. This is not a DEC-04 job scheduler or a shutdown redesign.

## Fields and evidence

No useful inventory field was deliberately removed. Existing flat aliases,
force_inventory, collect_disks and existing payloads remain supported.

- CPU: model/manufacturer, physical and logical counts, max_clock_mhz, socket,
  processor_id retained. Added max_clock_ghz (division by 1000 only), source,
  clock_kind, per-socket processors, status. Core/thread totals sum processors;
  model/max-clock flat aliases refer to the first reported CPU, with originals
  preserved per processor. No instantaneous frequency or benchmark is inferred.
- RAM: modules, capacity, manufacturer, Part Number, serial, type/form factor,
  raw Speed and ConfiguredClockSpeed retained. Installed module sum preferred
  when complete; system-reported total kept separately. New maximum_supported_bytes
  uses MaxCapacityEx only, converted from Windows KB by 1024. Legacy MaxCapacity
  remains in raw array evidence but is not converted into a capacity claim.
  Only system-memory arrays (Use=3) count. Inconsistent counts/unknown topology
  produce null slots/free counts, not a fabricated upgrade opportunity.
- `speed_mhz` and `configured_speed_mhz` remain compatibility aliases. New
  `speed_reported_value` is raw/unverified-unit evidence; no inferred MT/s,
  clock-halving or doubling. `configured_clock_mhz` has explicit Windows MHz
  semantics. The UI separates the two instead of labelling both as measured MHz.
- Board: structured manufacturer/product/version/serial/source/status added,
  preserving the existing motherboard string. BIOS version/date remain evidence,
  never equipment age, purchase date or manufacturing proof.
- OEM: system SKU/family plus ComputerSystemProduct name/vendor/version/SKU and
  identifying_number. These are auxiliary inventory references, not enrollment
  identities or automatic compatibility checks. No warranty/purchase data invented.
- Battery: presence, status, design/full mWh, health and cycles retained. Added
  reported manufacturer/model/serial/device ID, source and collection status.
  Part Number remains null unless actually supplied; capacity is not a part number.
  Root WMI capacity/identity enrichment requires a single unambiguous battery;
  multiple packs are not cross-associated or aggregated arbitrarily.
- Network: all nonloopback adapters, including disconnected/IPv6-only adapters,
  with IPv4/IPv6/MAC/gateway/link speed/configured DNS retained. Active adapters
  alone supply the primary IP candidate. Adapter DNS now appears in the UI;
  it is not confused with resolved AD A records. Link bps converted to Mbps.

Removed only misleading fallbacks: process architecture is not a CPU model,
process CPU quota is not an OEM processor count, SYSTEM is not the logged-on user,
and an unavailable TPM/battery query is not proof of absence.

## Collection cost changes

- System and core hardware share one PowerShell process/platform snapshot.
  Win32_ComputerSystem, Win32_OperatingSystem and Win32_BIOS each queried once,
  instead of twice in an aggregate round.
- Win32_Battery queried once in the detailed round, instead of in both core and
  enrichment. No battery query in daily basic-only rounds.
- CPU/board/product/TPM queried only in detailed rounds.
- Registry software enumeration reused by security in an aggregate round.
- BitLocker result reused between volume and security projections.
- Volume-only rounds skip platform, physical RAM/storage, battery and full-network
  inventory. Software/security/patch run when their existing interval is also due.
- PowerShell stdout/stderr drained concurrently, avoiding pipe-buffer deadlock
  for larger RAM/storage JSON. Process timeout and nonzero exit still fail safely.

This does not remove every PowerShell process: independent existing security,
patch and disk-provider sections retain their own bounded calls.

## Backend and presentation

Scheduled reports use `collection_scope=scheduled` and `section_states`.
The existing full-inventory ingestion endpoint is reused; no new endpoint/model
or migration. `collections.scheduled_inventory` records the actual attempt;
it never replaces the historical full_inventory reference with an hourly report.

`collections.hardware/system/network` are evidence projections. Empty, null,
unknown and failed readings do not replace the previous valid values. An
affirmative `not_present` battery observation can clear old battery capacity.
Module OEM fields are inherited only for a unique matching locator AND identical
nonempty serial; not across a confirmed module replacement.

`inventory_observations` records per-section source, current status, last attempt
and last success. Nested CPU/board/BIOS/RAM/battery retain separate evidence dates.
Legacy payload contents are not silently augmented with nested timestamp fields.
Existing snapshots remain untouched; each report creates its normal new snapshot.
Identity hardening still runs before writes/merging. No identity rule weakened.

Details now projects CPU, memory modules, board/OEM and battery fields. It renders
data through the existing escaped factList. Unknown != not_present != failed.
Historical available RAM is labelled as a hardware-collection observation,
not a current performance measurement. Capacity accepts merged scheduled evidence
and uses the original hardware/detailed date, never the heartbeat date.

## Synthetic, abbreviated payload examples

Desktop, not a real endpoint:

```json
{
  "machine_id": "synthetic-desktop",
  "collection_scope": "scheduled",
  "collected_at": "2026-10-29T12:00:00Z",
  "hardware": {
    "cpu": {"name": "Synthetic CPU", "physical_cores": 6,
      "logical_processors": 12, "max_clock_mhz": 3600, "max_clock_ghz": 3.6,
      "clock_kind": "reported_maximum_not_instantaneous"},
    "baseboard": {"manufacturer": "Synthetic OEM", "product": "SYNTH-BOARD", "version": "A1"},
    "memory": {"total_bytes": 25769803776, "slots_total": 2, "slots_used": 2,
      "slots_free": 0, "maximum_supported_bytes": 68719476736,
      "modules": [
        {"capacity_bytes": 8589934592, "part_number": "SYNTH-A", "memory_type": "ddr4",
         "speed_reported_value": 3200, "configured_clock_mhz": 2666},
        {"capacity_bytes": 17179869184, "part_number": "SYNTH-B", "memory_type": "ddr4"}
      ]},
    "battery": {"present": false, "collection_status": "not_present"}
  },
  "section_states": {"memory": "available", "battery": "not_present"}
}
```

Notebook, not a real endpoint:

```json
{
  "machine_id": "synthetic-notebook",
  "collection_scope": "scheduled",
  "hardware": {
    "system_sku": "SYNTH-SKU", "system_family": "Synthetic Family",
    "battery": {"present": true, "collection_status": "available",
      "manufacturer": "Synthetic OEM", "model": "SYNTH-BAT", "part_number": null,
      "design_capacity_mwh": 50000, "full_charge_capacity_mwh": 40000,
      "health_percent": 80, "cycle_count": 123}
  },
  "network": {"adapters": [{"name": "Wi-Fi", "status": "Up",
    "ipv4_addresses": ["192.0.2.20"], "ipv6_addresses": ["2001:db8::20"],
    "dns_servers": ["192.0.2.53"], "gateway": "192.0.2.1", "link_speed": 866000000}]}
}
```

## Files changed by DEC-02

Agent:
`Collectors/InventoryCadence.cs`, `Collectors/WindowsInventoryCollector.cs`,
`Collectors/HardwareInventoryNormalizer.cs`, `Models/AgentConfig.cs`,
`Models/AgentDtos.cs`, `Worker.cs` under NightOwl.Agent.Windows.

Backend/UI:
`agents/services.py`, `dashboard/views.py`, `dashboard/capacity_views.py`,
`static/js/endpoint_detail.js`, `static/css/nightowl.css` (scoped hardware text wrapping).

Tests/docs:
`NightOwl.Agent.Windows.Tests/HardwareCadenceTests.cs`,
`NightOwl.Agent.Windows.Tests/Program.cs`, `agents/test_hardware_cadence.py`,
`tests/Test-HardwareInventoryCadence.ps1`,
`tests/frontend/test_endpoint_hardware.cjs`, this document.

DEC-01 files remain pending, including its serializers, heartbeat tests and
deployment-confirmation regression. They were not discarded or committed.

## Validation and commands

Use the existing isolated test settings, never production DB credentials:

```powershell
$env:DJANGO_SECRET_KEY='isolated-rc46-test-key'
$env:DJANGO_DEBUG='True'
$env:DATABASE_URL='sqlite:///:memory:'
$env:AD_AUTH_ENABLED='False'
$python='C:\Users\gabriel.oliveira\Downloads\rmm_project\venv\Scripts\python.exe'
& $python manage.py test agents config dashboard.test_agent_install_workflow dashboard.test_remote_install_enrollment dashboard.test_capacity_dashboard --settings=config.settings_test --noinput --verbosity 0
& $python manage.py check --settings=config.settings_test
& $python manage.py makemigrations --check --dry-run --settings=config.settings_test
dotnet run --project NightOwl.Agent.Windows.Tests -c Release -- --hardware-only
dotnet run --project NightOwl.Agent.Shared.Tests -c Release
dotnet run --project NightOwl.Agent.Updater.Tests -c Release
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/Test-HardwareInventoryCadence.ps1
pwsh -NoProfile -File tests/Test-HardwareInventoryCadence.ps1
node tests/frontend/test_endpoint_hardware.cjs
node tests/frontend/test_endpoint_detail_jobs_static.js
node tests/frontend/test_endpoint_detail_update_modal.js
node --check static/js/endpoint_detail.js
git diff --check
```

Results: Django 646 tests OK, 29 skipped (including PostgreSQL-only checks).
Final focused hardware/heartbeat/legacy inventory/capacity rerun: 69 tests OK.
Nine new Django cases, including direct legacy partial/failed hardware payloads.
.NET hardware/cadence tests, existing hardware normalization/isolation tests,
DEC-01 heartbeat isolation tests, Shared and Updater suites: PASS.
Embedded PowerShell scripts tested with synthetic CIM on PowerShell 5.1 and 7:
PASS. No physical OEM/endpoint measurements claimed.
Node renderer/XSS and existing detail tests: PASS. Chrome/Playwright fixture
screenshots at 1920x1080 and 390x844: no horizontal overflow; inspected visually.
Screenshots are synthetic, not evidence of a production deployment.

Full Windows suite: FAIL at existing
`TestRepairRunnerScriptPersistsCompletedResult`, assertion
`Repair runner pending result should be completed.` Same known baseline gate
reproduced on clean main during DEC-01. No test removed or weakened. No official
builder, packaging or release gate declared approved.

## Limits / next decision

- Firmware can omit or misreport topology, SKU, clock, battery ID or capacity.
  Null/unknown is intentional; obtain OEM documentation before ordering parts.
- Battery Part Number is not guaranteed. No unsupported battery-pair matching.
- Slots are firmware-reported devices, not proof of accessible replaceable slots.
  Soldered memory and VMs need OEM/operator review.
- No automatic CPU/RAM/CMOS compatibility, age, warranty or physical-change
  history platform. Historical snapshots are preserved.
- PostgreSQL-specific checks and real desktop/notebook/VM OEM homologation remain
  pending. Backend supporting partial reports must precede a future agent update.
- RC46 release remains blocked by the Windows gate and pending decisions. DEC-03
  performance telemetry and DEC-04 jobs were not started or redesigned here.

## Unit references

Microsoft documents ConfiguredClockSpeed in MHz, while Speed still carries a
legacy nanoseconds qualifier and SMBIOS origin. Therefore this increment preserves
Speed raw rather than claiming a measured DDR clock or converting it to MT/s:
[Win32_PhysicalMemory](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-physicalmemory).

MaxCapacityEx is documented in KB; legacy MaxCapacity has version-dependent
semantics and is not a safe fallback here:
[Win32_PhysicalMemoryArray](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-physicalmemoryarray).
