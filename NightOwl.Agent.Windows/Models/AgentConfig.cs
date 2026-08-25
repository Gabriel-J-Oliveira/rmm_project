using System.Text.Json.Serialization;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Windows.Models;

public sealed class AgentConfig
{
    [JsonPropertyName("agentToken")]
    public string AgentToken { get; set; } = "";

    [JsonPropertyName("machineId")]
    public string MachineId { get; set; } = "";

    [JsonPropertyName("agentVersion")]
    public string AgentVersion { get; set; } = "0.1.0.7";

    [JsonPropertyName("configMigrationVersion")]
    public int ConfigMigrationVersion { get; set; } = 1;

    [JsonPropertyName("serverBaseUrl")]
    public string ServerBaseUrl { get; set; } = "";

    [JsonPropertyName("heartbeatUrl")]
    public string HeartbeatUrl { get; set; } = "";

    [JsonPropertyName("collectUrl")]
    public string CollectUrl { get; set; } = "";

    [JsonPropertyName("jobsPullUrl")]
    public string JobsPullUrl { get; set; } = "";

    [JsonPropertyName("jobsResultUrl")]
    public string JobsResultUrl { get; set; } = "";

    [JsonPropertyName("intervals")]
    public AgentIntervals Intervals { get; set; } = new();

    [JsonPropertyName("logPath")]
    public string LogPath { get; set; } = NightOwlPaths.Current.AgentLogPath;

    [JsonPropertyName("statePath")]
    public string StatePath { get; set; } = NightOwlPaths.Current.StatePath;

    [JsonPropertyName("pendingResultsPath")]
    public string PendingResultsPath { get; set; } = NightOwlPaths.Current.PendingResultsDir;

    [JsonPropertyName("packagesPath")]
    public string PackagesPath { get; set; } = NightOwlPaths.Current.PackagesDir;

    [JsonPropertyName("cachePath")]
    public string CachePath { get; set; } = NightOwlPaths.Current.CacheDir;

    [JsonPropertyName("jobsPath")]
    public string JobsPath { get; set; } = NightOwlPaths.Current.StateDir;

    [JsonPropertyName("installPath")]
    public string InstallPath { get; set; } = NightOwlPaths.Current.InstallDir;

    [JsonPropertyName("allowedJobTypes")]
    public List<string> AllowedJobTypes { get; set; } = new()
    {
        "ping",
        "collect_logs",
        "collect_disks",
        "collect_software",
        "collect_security",
        "windows_update_scan",
        "force_inventory",
        "update_agent",
        "update_trusted_release_keys",
        "repair_agent",
        "uninstall_agent",
        "restart_agent"
    };

    [JsonIgnore]
    public string MachineIdSource { get; set; } = "";

    [JsonIgnore]
    public bool HasValidToken => !IsPlaceholderToken(AgentToken);

    public static bool IsPlaceholderToken(string? token)
    {
        string value = (token ?? "").Trim();
        return string.IsNullOrWhiteSpace(value)
            || value.Equals("COLE_O_TOKEN_DO_ENDPOINT_AQUI", StringComparison.OrdinalIgnoreCase)
            || value.Equals("TOKEN", StringComparison.OrdinalIgnoreCase)
            || value.Equals("PLACEHOLDER", StringComparison.OrdinalIgnoreCase)
            || value.Contains("COLE_", StringComparison.OrdinalIgnoreCase);
    }
}

public sealed class AgentIntervals
{
    [JsonPropertyName("heartbeatSeconds")]
    public int HeartbeatSeconds { get; set; } = 300;

    [JsonPropertyName("collectSeconds")]
    public int CollectSeconds { get; set; } = 3600;

    [JsonPropertyName("jobsSeconds")]
    public int JobsSeconds { get; set; } = 45;
}

public sealed class AgentState
{
    [JsonPropertyName("machine_id")]
    public string MachineId { get; set; } = "";

    [JsonPropertyName("machineId")]
    public string MachineIdCamel
    {
        get => MachineId;
        set => MachineId = value;
    }

    [JsonPropertyName("lastHeartbeatAt")]
    public DateTimeOffset? LastHeartbeatAt { get; set; }

    [JsonPropertyName("lastCollectionAt")]
    public DateTimeOffset? LastCollectionAt { get; set; }

    [JsonPropertyName("lastJobPullAt")]
    public DateTimeOffset? LastJobPullAt { get; set; }

    [JsonPropertyName("recentJobIds")]
    public List<string> RecentJobIds { get; set; } = new();

    public void RememberJob(string jobId)
    {
        if (string.IsNullOrWhiteSpace(jobId) || RecentJobIds.Contains(jobId))
        {
            return;
        }

        RecentJobIds.Insert(0, jobId);
        if (RecentJobIds.Count > 200)
        {
            RecentJobIds = RecentJobIds.Take(200).ToList();
        }
    }
}
