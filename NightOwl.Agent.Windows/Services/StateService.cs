using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class StateService
{
    private TrayRuntimeStatus? _publishedTrayStatus;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    public AgentState Load(AgentConfig config)
    {
        return Load(config.StatePath, config.MachineId);
    }

    internal static AgentState Load(string statePath, string machineId)
    {
        if (!File.Exists(statePath))
        {
            return new AgentState();
        }

        string json = File.ReadAllText(statePath);
        AgentState state = JsonSerializer.Deserialize<AgentState>(json, JsonOptions) ?? new AgentState();
        if (string.IsNullOrWhiteSpace(state.MachineId))
        {
            state.MachineId = machineId;
        }
        return state;
    }

    public async Task SaveAsync(AgentConfig config, AgentState state, CancellationToken ct)
    {
        await SaveAsync(config.StatePath, state, config.MachineId, ct);
        // Config/State remain admin-only. Tray gets only this allowlisted projection.
        try
        {
            UriBuilder? server = Uri.TryCreate(config.ServerBaseUrl, UriKind.Absolute, out Uri? uri)
                && uri.Scheme is "https" or "http" ? new UriBuilder(uri) { UserName = "", Password = "", Query = "", Fragment = "" } : null;
            TrayRuntimeStatus status = new()
            {
                MachineId = state.MachineId, EndpointId = ReadEndpointId(state.ExtensionData, config.ExtensionData), AgentVersion = config.AgentVersion,
                ServerBaseUrl = server?.Uri.AbsoluteUri.TrimEnd('/') ?? "",
                InstallPath = config.InstallPath, LogPath = config.LogPath, LastHeartbeatAt = state.LastHeartbeatAt
            };
            string path = TrayRuntimeStatus.PathFor(NightOwlPaths.Current);
            if (status == _publishedTrayStatus && File.Exists(path)) return;
            await NightOwlFileStore.WriteAllTextAsync(path, JsonSerializer.Serialize(status, JsonOptions), ct: ct);
            _publishedTrayStatus = status;
        }
        catch (Exception) when (!ct.IsCancellationRequested)
        {
            // A UI projection failure cannot invalidate durable agent state; retry next save.
        }
    }

    private static string ReadEndpointId(params Dictionary<string, JsonElement>[] sources)
    {
        foreach (Dictionary<string, JsonElement> source in sources)
            foreach (string name in new[] { "endpoint_id", "endpointId" })
                if (source.TryGetValue(name, out JsonElement value) && value.ValueKind == JsonValueKind.String
                    && Guid.TryParse(value.GetString(), out Guid id)) return id.ToString("D");
        return "";
    }

    internal static async Task SaveAsync(string statePath, AgentState state, string machineId, CancellationToken ct)
    {
        PrepareForSave(state, machineId);
        Directory.CreateDirectory(Path.GetDirectoryName(statePath) ?? ".");
        string json = JsonSerializer.Serialize(state, JsonOptions);
        await NightOwlFileStore.WriteAllTextAsync(statePath, json, ct: ct);
    }

    internal static void Save(string statePath, AgentState state, string machineId)
    {
        PrepareForSave(state, machineId);
        Directory.CreateDirectory(Path.GetDirectoryName(statePath) ?? ".");
        string json = JsonSerializer.Serialize(state, JsonOptions);
        NightOwlFileStore.WriteAllText(statePath, json);
    }

    private static void PrepareForSave(AgentState state, string machineId)
    {
        if (string.IsNullOrWhiteSpace(state.MachineId))
        {
            state.MachineId = machineId;
        }
    }
}
