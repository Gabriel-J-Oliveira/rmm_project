using System.Text.Json.Serialization;

namespace NightOwl.Agent.Windows.Models;

public sealed class AgentHeartbeatPayload
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("agent_id")]
    public string AgentId { get; set; } = "";

    [JsonPropertyName("machine_id")]
    public string MachineId { get; set; } = "";

    [JsonPropertyName("hostname")]
    public string Hostname { get; set; } = "";

    [JsonPropertyName("fqdn")]
    public string Fqdn { get; set; } = "";

    [JsonPropertyName("domain")]
    public string Domain { get; set; } = "";

    [JsonPropertyName("logged_user")]
    public string LoggedUser { get; set; } = "";

    [JsonPropertyName("username")]
    public string Username { get; set; } = "";

    [JsonPropertyName("ip_address")]
    public string IpAddress { get; set; } = "";

    [JsonPropertyName("os_name")]
    public string OsName { get; set; } = "";

    [JsonPropertyName("os_version")]
    public string OsVersion { get; set; } = "";

    [JsonPropertyName("windows_build")]
    public string WindowsBuild { get; set; } = "";

    [JsonPropertyName("agent_version")]
    public string AgentVersion { get; set; } = "";

    [JsonPropertyName("tray_version")]
    public string TrayVersion { get; set; } = "";

    [JsonPropertyName("updater_version")]
    public string UpdaterVersion { get; set; } = "";

    [JsonPropertyName("agent_mode")]
    public string AgentMode { get; set; } = "dotnet-service";

    [JsonPropertyName("install_mode")]
    public string InstallMode { get; set; } = "dotnet-service";

    [JsonPropertyName("ips")]
    public List<string> Ips { get; set; } = new();

    [JsonPropertyName("os")]
    public Dictionary<string, object?> Os { get; set; } = new();

    [JsonPropertyName("hardware")]
    public Dictionary<string, object?> Hardware { get; set; } = new();

    [JsonPropertyName("uptime_seconds")]
    public long UptimeSeconds { get; set; }

    [JsonPropertyName("agent")]
    public Dictionary<string, object?> Agent { get; set; } = new();

    [JsonPropertyName("heartbeat_at")]
    public DateTimeOffset HeartbeatAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("timestamp")]
    public DateTimeOffset Timestamp { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class AgentCollectPayload
{
    [JsonPropertyName("machine_id")]
    public string MachineId { get; set; } = "";

    [JsonPropertyName("agent_version")]
    public string AgentVersion { get; set; } = "0.1.0.6";

    [JsonPropertyName("agent_mode")]
    public string AgentMode { get; set; } = "dotnet-service";

    [JsonPropertyName("collected_at")]
    public DateTimeOffset CollectedAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("system")]
    public Dictionary<string, object?> System { get; set; } = new();

    [JsonPropertyName("hardware")]
    public Dictionary<string, object?> Hardware { get; set; } = new();

    [JsonPropertyName("network")]
    public Dictionary<string, object?> Network { get; set; } = new();

    [JsonPropertyName("disks")]
    public List<Dictionary<string, object?>> Disks { get; set; } = new();

    [JsonPropertyName("software")]
    public List<Dictionary<string, object?>> Software { get; set; } = new();

    [JsonPropertyName("security")]
    public Dictionary<string, object?> Security { get; set; } = new();

    [JsonPropertyName("patches")]
    public Dictionary<string, object?> Patches { get; set; } = new();
}

public sealed class AgentJobsPullResponse
{
    [JsonPropertyName("jobs")]
    public List<AgentJobRequest> Jobs { get; set; } = new();
}

public sealed class AgentJobRequest
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("type")]
    public string Type { get; set; } = "";

    [JsonPropertyName("payload")]
    public Dictionary<string, object?> Payload { get; set; } = new();

    [JsonPropertyName("created_at")]
    public DateTimeOffset? CreatedAt { get; set; }

    [JsonPropertyName("timeout_seconds")]
    public int TimeoutSeconds { get; set; } = 300;
}

public sealed class JobExecutionResult
{
    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "completed";

    [JsonPropertyName("started_at")]
    public DateTimeOffset StartedAt { get; set; }

    [JsonPropertyName("finished_at")]
    public DateTimeOffset FinishedAt { get; set; }

    [JsonPropertyName("duration_seconds")]
    public double DurationSeconds { get; set; }

    [JsonPropertyName("exit_code")]
    public int ExitCode { get; set; }

    [JsonPropertyName("stdout")]
    public string Stdout { get; set; } = "";

    [JsonPropertyName("stderr")]
    public string Stderr { get; set; } = "";

    [JsonPropertyName("result")]
    public object? Result { get; set; }

    [JsonPropertyName("error_message")]
    public string ErrorMessage { get; set; } = "";
}
