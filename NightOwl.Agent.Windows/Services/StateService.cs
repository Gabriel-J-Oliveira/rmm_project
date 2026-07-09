using System.Text.Json;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class StateService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    public AgentState Load(AgentConfig config)
    {
        if (!File.Exists(config.StatePath))
        {
            return new AgentState();
        }

        string json = File.ReadAllText(config.StatePath);
        AgentState state = JsonSerializer.Deserialize<AgentState>(json, JsonOptions) ?? new AgentState();
        if (string.IsNullOrWhiteSpace(state.MachineId))
        {
            state.MachineId = config.MachineId;
        }
        return state;
    }

    public async Task SaveAsync(AgentConfig config, AgentState state, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(state.MachineId))
        {
            state.MachineId = config.MachineId;
        }
        Directory.CreateDirectory(Path.GetDirectoryName(config.StatePath) ?? ".");
        string json = JsonSerializer.Serialize(state, JsonOptions);
        await File.WriteAllTextAsync(config.StatePath, json, ct);
    }
}
