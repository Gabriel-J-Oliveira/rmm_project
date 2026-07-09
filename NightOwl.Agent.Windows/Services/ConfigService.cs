using System.Text.Json;
using Microsoft.Win32;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class ConfigService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    public AgentConfig? Current { get; private set; }

    public AgentConfig Load()
    {
        string configPath = ResolveConfigPath();
        AgentConfig config;
        if (File.Exists(configPath))
        {
            string json = File.ReadAllText(configPath);
            config = JsonSerializer.Deserialize<AgentConfig>(json, JsonOptions) ?? new AgentConfig();
        }
        else
        {
            config = new AgentConfig();
            Directory.CreateDirectory(Path.GetDirectoryName(configPath) ?? ".");
            File.WriteAllText(configPath, JsonSerializer.Serialize(config, JsonOptions));
        }

        Normalize(config);
        MachineIdentity identity = ResolveMachineIdentity(config, configPath);
        config.MachineId = identity.MachineId;
        config.MachineIdSource = identity.Source;
        Directory.CreateDirectory(Path.GetDirectoryName(config.LogPath) ?? ".");
        Directory.CreateDirectory(Path.GetDirectoryName(config.StatePath) ?? ".");
        Directory.CreateDirectory(config.InstallPath);
        Directory.CreateDirectory(config.PackagesPath);
        Directory.CreateDirectory(config.CachePath);
        Directory.CreateDirectory(config.JobsPath);
        EnsureStateMachineId(config, identity.MachineId);
        Current = config;
        return config;
    }

    private static string ResolveConfigPath()
    {
        string? fromEnv = Environment.GetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG");
        if (!string.IsNullOrWhiteSpace(fromEnv))
        {
            return fromEnv;
        }

        string basePath = AppContext.BaseDirectory;
        string local = Path.Combine(basePath, "agent.config.json");
        if (File.Exists(local))
        {
            return local;
        }

        return @"C:\ProgramData\NightOwl\AgentDotNet\agent.config.json";
    }

    private static void Normalize(AgentConfig config)
    {
        config.ServerBaseUrl = (config.ServerBaseUrl ?? "").TrimEnd('/');
        if (string.IsNullOrWhiteSpace(config.ServerBaseUrl) && !string.IsNullOrWhiteSpace(config.HeartbeatUrl))
        {
            config.ServerBaseUrl = DeriveServerBaseUrl(config.HeartbeatUrl);
        }
        if (string.IsNullOrWhiteSpace(config.HeartbeatUrl))
        {
            config.HeartbeatUrl = $"{config.ServerBaseUrl}/api/agent/heartbeat/";
        }
        if (string.IsNullOrWhiteSpace(config.CollectUrl))
        {
            config.CollectUrl = $"{config.ServerBaseUrl}/api/agent/collect/";
        }
        if (string.IsNullOrWhiteSpace(config.JobsPullUrl))
        {
            config.JobsPullUrl = $"{config.ServerBaseUrl}/api/agent/jobs/pull/";
        }
        if (string.IsNullOrWhiteSpace(config.JobsResultUrl))
        {
            config.JobsResultUrl = $"{config.ServerBaseUrl}/api/agent/jobs/result/";
        }
        if (string.IsNullOrWhiteSpace(config.StatePath))
        {
            config.StatePath = @"C:\ProgramData\NightOwl\AgentDotNet\agent-dotnet.state.json";
        }
        if (string.IsNullOrWhiteSpace(config.InstallPath))
        {
            config.InstallPath = @"C:\ProgramData\NightOwl\AgentDotNet";
        }
    }

    private static string DeriveServerBaseUrl(string url)
    {
        string value = (url ?? "").Trim().TrimEnd('/');
        string suffix = "/api/agent/heartbeat";
        if (value.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
        {
            return value[..^suffix.Length].TrimEnd('/');
        }
        return value;
    }

    private static MachineIdentity ResolveMachineIdentity(AgentConfig config, string configPath)
    {
        string configured = NormalizeMachineId(config.MachineId);
        if (!string.IsNullOrWhiteSpace(configured))
        {
            return new MachineIdentity(configured, "config");
        }

        foreach ((string Path, string Source) candidate in new[]
        {
            (config.StatePath, "dotnet_state"),
            (@"C:\ProgramData\NightOwl\Agent\agent.state.json", "powershell_state"),
            (@"C:\RMM\agent.state.json", "legacy_rmm_state"),
        })
        {
            string stateMachineId = NormalizeMachineId(ReadMachineIdFromJson(candidate.Path));
            if (!string.IsNullOrWhiteSpace(stateMachineId))
            {
                return new MachineIdentity(stateMachineId, candidate.Source);
            }
        }

        string machineGuid = NormalizeMachineId(ReadMachineGuid());
        if (!string.IsNullOrWhiteSpace(machineGuid))
        {
            return new MachineIdentity(machineGuid, "machine_guid");
        }

        return new MachineIdentity(Guid.NewGuid().ToString(), "generated");
    }

    private static string NormalizeMachineId(string? value)
    {
        string machineId = (value ?? "").Trim();
        if (string.IsNullOrWhiteSpace(machineId))
        {
            return "";
        }
        if (machineId.Equals(Environment.MachineName, StringComparison.OrdinalIgnoreCase))
        {
            return "";
        }
        if (machineId.Equals("HOSTNAME", StringComparison.OrdinalIgnoreCase) || machineId.Equals("MACHINE_ID", StringComparison.OrdinalIgnoreCase))
        {
            return "";
        }
        return machineId;
    }

    private static string ReadMachineIdFromJson(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
        {
            return "";
        }
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
            JsonElement root = document.RootElement;
            foreach (string propertyName in new[] { "machine_id", "machineId", "MachineId", "agent_id", "agentId" })
            {
                if (root.TryGetProperty(propertyName, out JsonElement value) && value.ValueKind == JsonValueKind.String)
                {
                    return value.GetString() ?? "";
                }
            }
        }
        catch
        {
            return "";
        }
        return "";
    }

    private static string ReadMachineGuid()
    {
        try
        {
            using RegistryKey? key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Cryptography");
            return key?.GetValue("MachineGuid")?.ToString() ?? "";
        }
        catch
        {
            return "";
        }
    }

    private static void EnsureStateMachineId(AgentConfig config, string machineId)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(config.StatePath) ?? ".");
            AgentState state = new();
            if (File.Exists(config.StatePath))
            {
                state = JsonSerializer.Deserialize<AgentState>(File.ReadAllText(config.StatePath), JsonOptions) ?? new AgentState();
            }
            if (string.IsNullOrWhiteSpace(state.MachineId))
            {
                state.MachineId = machineId;
                File.WriteAllText(config.StatePath, JsonSerializer.Serialize(state, JsonOptions));
            }
        }
        catch
        {
            // State persistence is retried by StateService during normal execution.
        }
    }

    private sealed record MachineIdentity(string MachineId, string Source);
}
