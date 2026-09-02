using System.Text.Json;
using NightOwl.Agent.Shared;
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
