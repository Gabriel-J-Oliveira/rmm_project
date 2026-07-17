using System.Text.Json;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Tray;

internal sealed class AgentLocalState
{
    public string InstallPath { get; init; } = NightOwlPaths.Current.InstallDir;
    public string ConfigPath { get; init; } = NightOwlPaths.Current.ConfigPath;
    public string StatePath { get; init; } = NightOwlPaths.Current.StatePath;
    public string LogPath { get; init; } = NightOwlPaths.Current.AgentLogPath;
    public string ServiceName { get; init; } = NightOwlPaths.ServiceName;
    public string ServerBaseUrl { get; init; } = NightOwlPaths.DefaultServerUrl;
    public string MachineId { get; init; } = "";
    public string EndpointId { get; init; } = "";
    public string AgentVersion { get; init; } = "";
    public DateTimeOffset? LastHeartbeatAt { get; init; }

    public static AgentLocalState Load()
    {
        NightOwlPaths paths = NightOwlPaths.Current;
        paths.Bootstrap("tray", applyAcl: false);
        string installPath = paths.InstallDir;
        string configPath = paths.ResolveConfigPath();
        string statePath = paths.StatePath;
        string logPath = paths.AgentLogPath;
        string server = NightOwlPaths.DefaultServerUrl;
        string machineId = "";
        string endpointId = "";
        string version = "";

        JsonDocument? config = ReadJson(configPath);
        if (config is not null)
        {
            using (config)
            {
                JsonElement root = config.RootElement;
                server = GetString(root, "serverBaseUrl", server);
                machineId = GetString(root, "machineId", machineId);
                endpointId = GetString(root, "endpointId", endpointId);
                version = GetString(root, "agentVersion", version);
                logPath = GetString(root, "logPath", logPath);
                statePath = GetString(root, "statePath", statePath);
                installPath = GetString(root, "installPath", installPath);
            }
        }

        DateTimeOffset? stateHeartbeat = null;
        JsonDocument? state = ReadJson(statePath) ?? ReadJson(paths.LegacyStatePath);
        if (state is not null)
        {
            using (state)
            {
                JsonElement root = state.RootElement;
                machineId = GetString(root, "machine_id", machineId);
                machineId = GetString(root, "machineId", machineId);
                endpointId = GetString(root, "endpoint_id", endpointId);
                endpointId = GetString(root, "endpointId", endpointId);
                stateHeartbeat = GetDate(root, "lastHeartbeatAt") ?? GetDate(root, "last_heartbeat_at");
            }
        }

        DateTimeOffset? logHeartbeat = ReadLastHeartbeatFromLog(logPath);
        DateTimeOffset? lastHeartbeat = Max(stateHeartbeat, logHeartbeat);
        JsonDocument? versionDocument = ReadJson(paths.VersionPath);
        if (versionDocument is not null)
        {
            using (versionDocument)
            {
                version = GetString(versionDocument.RootElement, "version", version);
            }
        }

        return new AgentLocalState
        {
            InstallPath = installPath,
            ConfigPath = configPath,
            StatePath = statePath,
            LogPath = logPath,
            ServiceName = NightOwlPaths.ServiceName,
            ServerBaseUrl = string.IsNullOrWhiteSpace(server) ? NightOwlPaths.DefaultServerUrl : server,
            MachineId = machineId,
            EndpointId = endpointId,
            AgentVersion = version,
            LastHeartbeatAt = lastHeartbeat
        };
    }

    private static JsonDocument? ReadJson(string path)
    {
        try
        {
            if (!File.Exists(path))
            {
                return null;
            }

            using FileStream stream = new(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            return JsonDocument.Parse(stream);
        }
        catch
        {
            return null;
        }
    }

    private static DateTimeOffset? ReadLastHeartbeatFromLog(string path)
    {
        try
        {
            if (!File.Exists(path))
            {
                return null;
            }

            List<string> lines = new(capacity: 400);
            using FileStream stream = new(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using StreamReader reader = new(stream);
            while (reader.ReadLine() is { } line)
            {
                lines.Add(line);
                if (lines.Count > 400)
                {
                    lines.RemoveAt(0);
                }
            }

            for (int i = lines.Count - 1; i >= 0; i--)
            {
                using JsonDocument? item = ParseLine(lines[i]);
                if (item is null)
                {
                    continue;
                }

                JsonElement root = item.RootElement;
                string eventType = GetString(root, "event_type", "");
                if (!eventType.Equals("heartbeat.sent", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                DateTimeOffset? timestamp = GetDate(root, "timestamp");
                if (timestamp is not null)
                {
                    return timestamp;
                }
            }
        }
        catch
        {
            return null;
        }

        return null;
    }

    private static JsonDocument? ParseLine(string line)
    {
        try
        {
            return JsonDocument.Parse(line);
        }
        catch
        {
            return null;
        }
    }

    private static string GetString(JsonElement root, string property, string fallback)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(property, out JsonElement value))
        {
            return fallback;
        }

        return value.ValueKind == JsonValueKind.String ? value.GetString() ?? fallback : value.ToString();
    }

    private static DateTimeOffset? GetDate(JsonElement root, string property)
    {
        string value = GetString(root, property, "");
        if (DateTimeOffset.TryParse(value, out DateTimeOffset parsed))
        {
            return parsed;
        }

        return null;
    }

    private static DateTimeOffset? Max(DateTimeOffset? left, DateTimeOffset? right)
    {
        if (left is null)
        {
            return right;
        }

        if (right is null)
        {
            return left;
        }

        return left > right ? left : right;
    }
}
