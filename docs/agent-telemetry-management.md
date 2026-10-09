# Agent telemetry management

## Contract

`configure_telemetry` uses the existing authenticated agent Jobs Pull/Result
channel. Only technical operators can create it from an endpoint. The exact
payload is:

```json
{
  "telemetryEnabled": true,
  "telemetrySampleSeconds": 300,
  "telemetryFlushSeconds": 900
}
```

All three keys are required; additional keys are rejected. Enabled must be a
boolean, sample an integer from 60 to 3600 seconds, flush an integer from 60 to
86400 seconds. Backend and agent independently validate these constraints.
RC45 is incompatible. RC46 or later must also report the capability in a
heartbeat before the backend will create or dispatch this job.

## Application and recovery

The job is exclusive with updates, restarts, repair and uninstall. It does not
invoke PowerShell, WinRM or an arbitrary command. ConfigService patches only
the three keys in the existing JSON and uses File.Replace to retain the
destination ACL. Identity, credentials, URLs and unknown properties remain.

A state journal stores job ID, machine ID, previous/requested telemetry values
and eventually the sanitized result, never tokens. The Worker stops the old
pipeline before reopening the same buffer and confirms readiness only after
loading the persisted buffer. Disabled telemetry stops sampling/flush without
deleting buffered samples. The next enabled pipeline reloads that buffer.
No Windows service restart is required for this operation.

Failure restores the previous file and runtime configuration. An interrupted
application/rollback remains owned by the journal and is recovered at startup
or a later Worker cycle, before a final result is acknowledged. A durable
result uses the existing critical PendingResultQueue. Retransmission does not
repeat application. The updater healthcheck remains independent of telemetry
readiness; telemetry startup errors do not become an update rollback trigger.

Success requires a matching job/configuration ID, machine ID, requested and
effective values, confirmed flag and timezone-aware application time. A
completed job is NOT proof of samples: Monitoramento displays both states.

## First canary, only after a signed compatible RC is published

1. Keep the candidate release paused, development channel, rollout zero.
   Do not alter RC45 or the stable release.
2. Choose only CS-CVEL-0253, verify its exact endpoint/machine identity,
   fresh heartbeat, update eligibility and absence of pending lifecycle jobs.
3. From the existing explicit release update action, create exactly one
   `update_agent` job targeting the signed compatible RC. Wait for updater
   healthcheck, completed result and a fresh heartbeat reporting the new
   version and `configure_telemetry` capability. Do not retry an unknown update.
4. Open endpoint > Monitoramento. Record reported settings and last sample
   receipt. Request enabled=true, sample=300, flush=900. Create one job only.
5. Wait for completed/confirmed result. Compare requested and effective fields,
   configuration ID and application time. Identity/version must remain stable.
6. Wait for real samples collected after application and received by the backend
   (up to one flush cycle plus collection/network delays). Verify fresh CPU/RAM
   evidence in Capacity using the same endpoint ID, not hostname alone.
7. If rollback/failure is reported, review its sanitized result. Do not create
   a second configuration while recovery is pending. To restore after a
   successful change, submit a new job with the recorded previous three values.

No canary jobs or updates are created during implementation/deployment.
Fresh installations inherit enabled/300/900 defaults. Updating existing agents
preserves explicit enabled=false and existing intervals until an operator
submits this job.

## Release gate

The official builder must pass its complete tests before signing/publication.
The current environment has a separately reproduced baseline failure in
`TestRepairRunnerScriptPersistsCompletedResult`. Never bypass it with SkipTests.
Until a compatible signed RC passes publication, RC45 remains blocked and the
end-to-end acceptance gate is pending.
