# Lightweight telemetry core v1

This is an opt-in laboratory pipeline, not a fleet rollout. Existing agent
configurations deserialize with `telemetryEnabled=false`. The defaults are a
300-second sample interval, a 1,800-second flush interval, a 288-sample buffer
and a 48-hour age limit. The buffer is stored under the protected NightOwl
State directory and uses `NightOwlFileStore` for replacement. Oldest samples
are discarded first at the count/age limits. Failed HTTP sends retain the same
sample IDs; only a successful response removes those IDs.

The agent samples in an isolated task. One collection is allowed at a time,
with a 10-second budget. A timed-out collection is cancelled and no second
one starts while it is still running. Local errors delay the telemetry loop;
they do not block heartbeat, inventory, jobs or update handling. Batch size is
at most 24, and a full-enough buffer may flush sooner than 30 minutes.

Windows system CPU is computed from successive `GetSystemTimes` cumulative
idle/kernel/user values. Process CPU uses successive `TotalProcessorTime`
snapshots keyed by PID and process start time, divided by elapsed wall time
and logical processor count. First observations and invalid deltas are null.
Only top five CPU and working-set consumers are retained; no command line,
window title or user content is sampled. Physical and committed memory use
`GlobalMemoryStatusEx` and `GetPerformanceInfo`. Network deltas sum operational
non-loopback interfaces' cumulative IPv4 byte counters. Counter resets yield
null for that interval. Disk activity, queue, throughput and latency are null
in v1 because no sufficiently reliable low-cost physical-disk source has been
selected; volume capacity is not mislabeled as disk performance.

The authenticated agent POST is `/api/agent/telemetry/`. It accepts schema 1,
up to 24 samples and a 256 KiB body, verifies the bearer-bound machine ID,
and stores raw rows with a unique `(endpoint, sample_id)` constraint. The
technical GET `/api/agent/telemetry/<endpoint UUID>/` requires a logged-in
user with endpoint and performance-sample view permissions; `from`, `to` and
`limit` (1-500) constrain the read. Raw rows have no automatic retention job
in this MVP. A later phase must define retention/aggregation before wider use.

The pilot should measure collection duration, agent working set, payload
size, partial samples, skips, overflows and flush failures. No release,
deployment or endpoint configuration is changed by this implementation.
