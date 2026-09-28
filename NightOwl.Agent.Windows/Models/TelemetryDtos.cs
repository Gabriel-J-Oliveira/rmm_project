using System.Text.Json.Serialization;

namespace NightOwl.Agent.Windows.Models;

public sealed class TelemetryBatch
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; } = 1;

    [JsonPropertyName("machine_id")]
    public string MachineId { get; init; } = "";

    [JsonPropertyName("samples")]
    public List<TelemetrySample> Samples { get; init; } = new();
}

public sealed class TelemetrySample
{
    [JsonPropertyName("sample_id")]
    public Guid SampleId { get; init; } = Guid.NewGuid();

    [JsonPropertyName("collected_at")]
    public DateTimeOffset CollectedAt { get; init; }

    [JsonPropertyName("uptime_seconds")]
    public long UptimeSeconds { get; init; }

    [JsonPropertyName("cpu")]
    public TelemetryCpu Cpu { get; init; } = new();

    [JsonPropertyName("memory")]
    public TelemetryMemory Memory { get; init; } = new();

    [JsonPropertyName("disk")]
    public TelemetryDisk Disk { get; init; } = new();

    [JsonPropertyName("network")]
    public TelemetryNetwork Network { get; init; } = new();

    [JsonPropertyName("top_processes")]
    public TelemetryProcesses TopProcesses { get; set; } = new();

    [JsonPropertyName("collection_duration_ms")]
    public long CollectionDurationMs { get; set; }

    [JsonPropertyName("agent_working_set_bytes")]
    public long? AgentWorkingSetBytes { get; set; }

    [JsonPropertyName("telemetry_errors_count")]
    public int TelemetryErrorsCount { get; set; }
}

public sealed class TelemetryCpu
{
    [JsonPropertyName("usage_percent")]
    public double? UsagePercent { get; set; }
}

public sealed class TelemetryMemory
{
    [JsonPropertyName("total_bytes")]
    public ulong? TotalBytes { get; set; }

    [JsonPropertyName("available_bytes")]
    public ulong? AvailableBytes { get; set; }

    [JsonPropertyName("used_percent")]
    public double? UsedPercent { get; set; }

    [JsonPropertyName("committed_bytes")]
    public ulong? CommittedBytes { get; set; }

    [JsonPropertyName("commit_limit_bytes")]
    public ulong? CommitLimitBytes { get; set; }

    [JsonPropertyName("committed_percent")]
    public double? CommittedPercent { get; set; }
}

public sealed class TelemetryDisk
{
    [JsonPropertyName("active_percent")]
    public double? ActivePercent { get; set; }

    [JsonPropertyName("queue_length")]
    public double? QueueLength { get; set; }

    [JsonPropertyName("read_bytes_per_sec")]
    public double? ReadBytesPerSec { get; set; }

    [JsonPropertyName("write_bytes_per_sec")]
    public double? WriteBytesPerSec { get; set; }

    [JsonPropertyName("read_latency_ms")]
    public double? ReadLatencyMs { get; set; }

    [JsonPropertyName("write_latency_ms")]
    public double? WriteLatencyMs { get; set; }
}

public sealed class TelemetryNetwork
{
    [JsonPropertyName("received_bytes")]
    public long? ReceivedBytes { get; set; }

    [JsonPropertyName("sent_bytes")]
    public long? SentBytes { get; set; }
}

public sealed class TelemetryProcesses
{
    [JsonPropertyName("cpu")]
    public List<TelemetryProcess> Cpu { get; set; } = new();

    [JsonPropertyName("memory")]
    public List<TelemetryProcess> Memory { get; set; } = new();
}

public sealed class TelemetryProcess
{
    [JsonPropertyName("process_name")]
    public string ProcessName { get; init; } = "";

    [JsonPropertyName("pid")]
    public int Pid { get; init; }

    [JsonPropertyName("cpu_percent")]
    public double? CpuPercent { get; init; }

    [JsonPropertyName("working_set_bytes")]
    public long WorkingSetBytes { get; init; }
}
