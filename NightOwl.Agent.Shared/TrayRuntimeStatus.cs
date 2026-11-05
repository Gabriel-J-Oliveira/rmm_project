namespace NightOwl.Agent.Shared;

// Public local projection. Never serialize AgentConfig into the Users-readable logs directory.
public sealed record TrayRuntimeStatus
{
    public int SchemaVersion { get; init; } = 1;
    public string MachineId { get; init; } = "";
    public string EndpointId { get; init; } = "";
    public string AgentVersion { get; init; } = "";
    public string ServerBaseUrl { get; init; } = "";
    public string InstallPath { get; init; } = "";
    public string LogPath { get; init; } = "";
    public DateTimeOffset? LastHeartbeatAt { get; init; }
    public static string PathFor(NightOwlPaths paths) => Path.Combine(paths.LogsDir, "tray-status.json");
}
