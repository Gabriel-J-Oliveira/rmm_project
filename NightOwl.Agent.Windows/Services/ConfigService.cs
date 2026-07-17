using System.Text.Json;
using System.Reflection;
using Microsoft.Win32;
using NightOwl.Agent.Shared;
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
        NightOwlPaths paths = NightOwlPaths.Current;
        paths.Bootstrap("agent");
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
        config.AgentVersion = GetRunningAgentVersion(config.AgentVersion);
        MachineIdentity identity = ResolveMachineIdentity(config, configPath);
        config.MachineId = identity.MachineId;
        config.MachineIdSource = identity.Source;
        PersistCanonicalConfig(configPath, config);
        Directory.CreateDirectory(Path.GetDirectoryName(config.LogPath) ?? ".");
        Directory.CreateDirectory(Path.GetDirectoryName(config.StatePath) ?? ".");
        Directory.CreateDirectory(config.InstallPath);
        Directory.CreateDirectory(config.PackagesPath);
        Directory.CreateDirectory(config.CachePath);
        Directory.CreateDirectory(config.JobsPath);
        Directory.CreateDirectory(config.PendingResultsPath);
        EnsureStateMachineId(config, identity.MachineId);
        Current = config;
        return config;
    }

    private static string GetRunningAgentVersion(string fallback)
    {
        try
        {
            string? informational = typeof(ConfigService).Assembly
                .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?
                .InformationalVersion;
            string version = (informational ?? typeof(ConfigService).Assembly.GetName().Version?.ToString() ?? "").Split('+')[0];
            return string.IsNullOrWhiteSpace(version) ? fallback : version;
        }
        catch
        {
            return fallback;
        }
    }

    private static string ResolveConfigPath()
    {
        return NightOwlPaths.Current.ResolveConfigPath();
    }

    private static void Normalize(AgentConfig config)
    {
        NightOwlPaths paths = NightOwlPaths.Current;
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
        if (string.IsNullOrWhiteSpace(config.StatePath) || SamePath(config.StatePath, paths.LegacyStatePath))
        {
            config.StatePath = paths.StatePath;
        }
        if (string.IsNullOrWhiteSpace(config.InstallPath))
        {
            config.InstallPath = paths.InstallDir;
        }
        if (string.IsNullOrWhiteSpace(config.LogPath))
        {
            config.LogPath = paths.AgentLogPath;
        }
        if (string.IsNullOrWhiteSpace(config.PendingResultsPath))
        {
            config.PendingResultsPath = paths.PendingResultsDir;
        }
        if (string.IsNullOrWhiteSpace(config.JobsPath) || SamePath(config.JobsPath, Path.Combine(paths.Root, "Jobs")))
        {
            config.JobsPath = paths.StateDir;
        }
        if (string.IsNullOrWhiteSpace(config.PackagesPath))
        {
            config.PackagesPath = paths.PackagesDir;
        }
        if (string.IsNullOrWhiteSpace(config.CachePath))
        {
            config.CachePath = paths.CacheDir;
        }
    }

    private static void PersistCanonicalConfig(string configPath, AgentConfig config)
    {
        try
        {
            if (SamePath(configPath, NightOwlPaths.Current.ConfigPath))
            {
                File.WriteAllText(configPath, JsonSerializer.Serialize(config, JsonOptions));
            }
        }
        catch
        {
            // Config normalization is retried on next startup.
        }
    }

    private static bool SamePath(string left, string right)
    {
        if (string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right))
        {
            return false;
        }

        return string.Equals(Path.GetFullPath(left).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
            Path.GetFullPath(right).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
            StringComparison.OrdinalIgnoreCase);
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
            (NightOwlPaths.Current.IdentityPath, "identity"),
            (NightOwlPaths.Current.LegacyStatePath, "legacy_dotnet_state"),
            (Path.Combine(NightOwlPaths.Current.Root, "Agent", "agent.state.json"), "powershell_state"),
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
