# Telemetry, Inventory and Capacity Stabilization

New installations from the next signed candidate enable telemetry explicitly:
`telemetryEnabled=true`, `telemetrySampleSeconds=300`, `telemetryFlushSeconds=900`.
Heartbeat remains 300 seconds; full inventory remains 3600 seconds. Buffer limits,
authentication, retry and idempotency are unchanged. RC44/RC45 are immutable.

Config policy version 5 preserves existing explicit telemetry settings. An existing
config without `telemetryEnabled` remains disabled; absence is not administrative
consent. Its implicit hourly flush is preserved too. An update alone does not
enable telemetry on an existing endpoint.

Capacity selects real full inventory snapshots, not inherited heartbeat references.
Legacy snapshots are matched to the full collection timestamp. New snapshots carry
an explicit source marker. No historical records are edited. The API exposes the
snapshot ID, collection time, receipt time and available section timestamps under
`inventory_provenance`. `inventory_at` is collection time, not heartbeat receipt time.
Missing disk/RAM metrics remain unavailable, not fabricated zero measurements.

The frontend refreshes every five minutes while visible, resumes stale data on
visibility return, bypasses cache, serializes overview refreshes and rejects stale
period/detail responses. Filters, page, period and the open drawer remain selected.

## Controlled Opt-In for an Existing Canary

Run only with explicit operator approval, locally on the intended Windows endpoint
in elevated PowerShell. These commands are NOT executed by this delivery. Confirm
hostname and machine identity first; do not display the full config (it has secrets).
Keep the backup in the existing protected Config directory, never a shared folder.

```powershell
$path = 'C:\ProgramData\NightOwl\Config\agent.config.json'
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Canonical config missing: stop, do not guess another path.' }
$backup = "$path.telemetry-backup-$(Get-Date -Format yyyyMMddTHHmmss)"
Copy-Item -LiteralPath $path -Destination $backup -ErrorAction Stop
$config = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$config | Add-Member -NotePropertyName telemetryEnabled -NotePropertyValue $true -Force
$config | Add-Member -NotePropertyName telemetrySampleSeconds -NotePropertyValue 300 -Force
$config | Add-Member -NotePropertyName telemetryFlushSeconds -NotePropertyValue 900 -Force
$config | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $path -Encoding UTF8 -ErrorAction Stop
Restart-Service -Name NightOwlAgentDotNet -ErrorAction Stop
Get-Service -Name NightOwlAgentDotNet
```

Rollback in the SAME approved session, with `$backup` retained:

```powershell
Copy-Item -LiteralPath $backup -Destination $path -Force -ErrorAction Stop
Restart-Service -Name NightOwlAgentDotNet -ErrorAction Stop
```

Before opt-in, obtain the correct canonical path from the active service/agent
diagnostics. The above procedure stops if the normal path is absent. Do not change
an alternate/legacy config blindly. No token, identity, enrollment or policy changes.
Backups contain secrets and must retain restricted directory ACLs.

After approval and opt-in, observe collection and flush logs, then check production
samples associated with the exact endpoint ID. Allow a sample/flush cycle. A partial
24-hour window is expected; automated tests alone do not establish real E2E success.

## Release Gate

The official signed release builder remains responsible for immutable candidate
creation, checksum/manifest validation and publishing paused at zero rollout in
development. Never change the installer embedded in an existing release. Do not
change the remote-install selected release or initiate rollout as part of validation.
