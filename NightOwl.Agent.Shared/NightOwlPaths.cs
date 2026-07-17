using System.Diagnostics;
using System.Text.Json;

namespace NightOwl.Agent.Shared;

public sealed class NightOwlPaths
{
    public const string ServiceName = "NightOwlAgentDotNet";
    public const string TrayProcessName = "NightOwl.Agent.Tray";
    public const string DefaultServerUrl = "https://nightowl.controlsul.com.br";

    public string Root { get; }
    public string InstallDir { get; }
    public string ConfigDir { get; }
    public string ConfigPath { get; }
    public string LegacyConfigPath { get; }
    public string IdentityDir { get; }
    public string IdentityPath { get; }
    public string StateDir { get; }
    public string StatePath { get; }
    public string LegacyStatePath { get; }
    public string UpdateStatePath { get; }
    public string PendingResultsDir { get; }
    public string LogsDir { get; }
    public string AgentLogPath { get; }
    public string UpdaterLogPath { get; }
    public string TrayLogPath { get; }
    public string ServiceInstallLogPath { get; }
    public string ServiceStdoutLogPath { get; }
    public string ServiceStderrLogPath { get; }
    public string UpdatesDir { get; }
    public string UpdatesDownloadsDir { get; }
    public string UpdatesStagingDir { get; }
    public string UpdatesBackupDir { get; }
    public string UpdatesPendingDir { get; }
    public string UpdatesRunnerDir { get; }
    public string DiagnosticsDir { get; }
    public string PackagesDir { get; }
    public string CacheDir { get; }
    public string VersionPath { get; }

    public static NightOwlPaths Current { get; } = FromEnvironment();

    public NightOwlPaths(string root)
    {
        Root = FullPath(root);
        InstallDir = Path.Combine(Root, "AgentDotNet");
        ConfigDir = Path.Combine(Root, "Config");
        ConfigPath = Path.Combine(ConfigDir, "agent.config.json");
        LegacyConfigPath = Path.Combine(InstallDir, "agent.config.json");
        IdentityDir = Path.Combine(Root, "Identity");
        IdentityPath = Path.Combine(IdentityDir, "agent.identity.json");
        StateDir = Path.Combine(Root, "State");
        StatePath = Path.Combine(StateDir, "agent.state.json");
        LegacyStatePath = Path.Combine(InstallDir, "agent-dotnet.state.json");
        UpdateStatePath = Path.Combine(StateDir, "update-state.json");
        PendingResultsDir = Path.Combine(StateDir, "pending-results");
        LogsDir = Path.Combine(Root, "Logs");
        AgentLogPath = Path.Combine(LogsDir, "agent-dotnet.jsonl");
        UpdaterLogPath = Path.Combine(LogsDir, "agent-updater.jsonl");
        TrayLogPath = Path.Combine(LogsDir, "agent-tray.jsonl");
        ServiceInstallLogPath = Path.Combine(LogsDir, "service-install.log");
        ServiceStdoutLogPath = Path.Combine(LogsDir, "service-stdout.log");
        ServiceStderrLogPath = Path.Combine(LogsDir, "service-stderr.log");
        UpdatesDir = Path.Combine(Root, "Updates");
        UpdatesDownloadsDir = Path.Combine(UpdatesDir, "Downloads");
        UpdatesStagingDir = Path.Combine(UpdatesDir, "Staging");
        UpdatesBackupDir = Path.Combine(UpdatesDir, "Backup");
        UpdatesPendingDir = Path.Combine(UpdatesDir, "Pending");
        UpdatesRunnerDir = Path.Combine(UpdatesDir, "Runner");
        DiagnosticsDir = Path.Combine(Root, "Diagnostics");
        PackagesDir = Path.Combine(Root, "Packages");
        CacheDir = Path.Combine(Root, "Cache");
        VersionPath = Path.Combine(InstallDir, "agent.version.json");
    }

    public static NightOwlPaths FromEnvironment()
    {
        string? root = Environment.GetEnvironmentVariable("NIGHTOWL_HOME");
        if (string.IsNullOrWhiteSpace(root))
        {
            string programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            root = string.IsNullOrWhiteSpace(programData)
                ? @"C:\ProgramData\NightOwl"
                : Path.Combine(programData, "NightOwl");
        }

        return new NightOwlPaths(root);
    }

    public IReadOnlyList<string> RequiredDirectories => new[]
    {
        Root,
        InstallDir,
        ConfigDir,
        IdentityDir,
        StateDir,
        PendingResultsDir,
        LogsDir,
        UpdatesDir,
        UpdatesDownloadsDir,
        UpdatesStagingDir,
        UpdatesBackupDir,
        UpdatesPendingDir,
        UpdatesRunnerDir,
        DiagnosticsDir,
        PackagesDir,
        CacheDir
    };

    public void Bootstrap(string component, bool applyAcl = true)
    {
        foreach (string directory in RequiredDirectories)
        {
            EnsureDirectory(directory, component);
        }

        if (applyAcl)
        {
            ProtectPersistentDirectories(component);
        }

        MigrateConfig(component);
        MigrateState(component);
        MigratePendingResults(component);
        EnsureIdentity(component);
        WriteLog(component, "path.migration.completed", "NightOwl path bootstrap completed.", new
        {
            root = Root,
            install_dir = InstallDir,
            config_path = ConfigPath,
            state_path = StatePath,
            pending_results_dir = PendingResultsDir
        });
    }

    public void EnsureDirectory(string directory, string component)
    {
        try
        {
            bool existed = Directory.Exists(directory);
            Directory.CreateDirectory(directory);
            if (!existed)
            {
                WriteLog(component, "path.directory.created", "Directory created.", new { path = directory });
            }
        }
        catch (Exception ex)
        {
            WriteLog(component, "path.access.failed", "Failed to create directory.", new { path = directory, error = ex.Message }, "error");
            throw;
        }
    }

    public void WriteLog(string component, string eventType, string message, object? data = null, string level = "info")
    {
        try
        {
            Directory.CreateDirectory(LogsDir);
            string logPath = component.Equals("updater", StringComparison.OrdinalIgnoreCase)
                ? UpdaterLogPath
                : component.Equals("tray", StringComparison.OrdinalIgnoreCase)
                    ? TrayLogPath
                    : AgentLogPath;
            var record = new
            {
                timestamp = DateTimeOffset.UtcNow,
                level,
                event_type = eventType,
                message,
                data = data ?? new { }
            };
            File.AppendAllText(logPath, JsonSerializer.Serialize(record, JsonOptions) + Environment.NewLine);
        }
        catch
        {
            // Path bootstrap logging must not prevent agent startup.
        }
    }

    public string ResolveConfigPath()
    {
        string? fromEnv = Environment.GetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG");
        if (!string.IsNullOrWhiteSpace(fromEnv))
        {
            return FullPath(fromEnv);
        }

        if (File.Exists(ConfigPath))
        {
            return ConfigPath;
        }

        if (File.Exists(LegacyConfigPath))
        {
            return LegacyConfigPath;
        }

        string local = Path.Combine(AppContext.BaseDirectory, "agent.config.json");
        if (File.Exists(local))
        {
            return local;
        }

        return ConfigPath;
    }

    private void MigrateConfig(string component)
    {
        try
        {
            if (!File.Exists(LegacyConfigPath))
            {
                return;
            }

            if (!File.Exists(ConfigPath))
            {
                Directory.CreateDirectory(ConfigDir);
                File.Copy(LegacyConfigPath, ConfigPath, overwrite: false);
                WriteLog(component, "path.file.migrated", "Legacy config copied to Config.", new { source = LegacyConfigPath, destination = ConfigPath });
                return;
            }

            int legacyScore = JsonCompletenessScore(LegacyConfigPath);
            int currentScore = JsonCompletenessScore(ConfigPath);
            if (legacyScore > currentScore)
            {
                string backupPath = ConfigPath + ".preserved-" + DateTimeOffset.UtcNow.ToString("yyyyMMddHHmmss");
                File.Copy(ConfigPath, backupPath, overwrite: false);
                File.Copy(LegacyConfigPath, ConfigPath, overwrite: true);
                WriteLog(component, "path.migration.conflict", "Legacy config was more complete; current config preserved and legacy copied.", new
                {
                    source = LegacyConfigPath,
                    destination = ConfigPath,
                    preserved = backupPath,
                    legacy_score = legacyScore,
                    current_score = currentScore
                }, "warning");
            }
            else
            {
                WriteLog(component, "path.legacy.preserved", "Legacy config preserved; Config copy is equal or more complete.", new
                {
                    legacy_path = LegacyConfigPath,
                    config_path = ConfigPath,
                    legacy_score = legacyScore,
                    current_score = currentScore
                });
            }
        }
        catch (Exception ex)
        {
            WriteLog(component, "path.migration.failed", "Config migration failed.", new { error = ex.Message }, "error");
        }
    }

    private void MigrateState(string component)
    {
        try
        {
            if (!File.Exists(LegacyStatePath))
            {
                return;
            }

            if (!File.Exists(StatePath))
            {
                Directory.CreateDirectory(StateDir);
                File.Copy(LegacyStatePath, StatePath, overwrite: false);
                WriteLog(component, "path.file.migrated", "Legacy state copied to State.", new { source = LegacyStatePath, destination = StatePath });
                return;
            }

            WriteLog(component, "path.legacy.preserved", "Legacy state preserved; State file already exists.", new { legacy_path = LegacyStatePath, state_path = StatePath });
        }
        catch (Exception ex)
        {
            WriteLog(component, "path.migration.failed", "State migration failed.", new { error = ex.Message }, "error");
        }
    }

    private void MigratePendingResults(string component)
    {
        string legacyJobsDir = Path.Combine(Root, "Jobs");
        string legacyPendingDir = Path.Combine(legacyJobsDir, "Pending");
        string legacyPendingUpdate = Path.Combine(legacyJobsDir, "pending-update-result.json");
        try
        {
            foreach (string source in EnumerateExistingFiles(legacyPendingDir, "*.json").Concat(File.Exists(legacyPendingUpdate) ? new[] { legacyPendingUpdate } : Array.Empty<string>()))
            {
                string destination = Path.Combine(PendingResultsDir, Path.GetFileName(source));
                if (File.Exists(destination))
                {
                    WriteLog(component, "path.legacy.preserved", "Legacy pending result preserved; destination already exists.", new { source, destination });
                    continue;
                }

                File.Copy(source, destination, overwrite: false);
                WriteLog(component, "path.file.migrated", "Pending job result copied to State pending-results.", new { source, destination });
            }
        }
        catch (Exception ex)
        {
            WriteLog(component, "path.migration.failed", "Pending result migration failed.", new { error = ex.Message }, "error");
        }
    }

    private void EnsureIdentity(string component)
    {
        try
        {
            if (File.Exists(IdentityPath))
            {
                return;
            }

            string machineId = ReadMachineId(ConfigPath);
            string source = "config";
            if (string.IsNullOrWhiteSpace(machineId))
            {
                machineId = ReadMachineId(StatePath);
                source = "state";
            }

            if (string.IsNullOrWhiteSpace(machineId))
            {
                return;
            }

            Directory.CreateDirectory(IdentityDir);
            File.WriteAllText(IdentityPath, JsonSerializer.Serialize(new
            {
                machine_id = machineId,
                source,
                created_at = DateTimeOffset.UtcNow
            }, JsonOptions));
            WriteLog(component, "path.file.migrated", "Identity file prepared from existing data.", new { identity_path = IdentityPath, source });
        }
        catch (Exception ex)
        {
            WriteLog(component, "path.migration.failed", "Identity preparation failed.", new { error = ex.Message }, "error");
        }
    }

    private void ProtectPersistentDirectories(string component)
    {
        foreach (string directory in new[] { ConfigDir, IdentityDir, StateDir })
        {
            TryRunIcacls(directory, "/inheritance:r", component);
            TryRunIcacls(directory, "/grant:r", component, "SYSTEM:(OI)(CI)(F)", "Administrators:(OI)(CI)(F)", "Users:(OI)(CI)(RX)");
        }

        foreach (string directory in new[] { UpdatesDir, DiagnosticsDir })
        {
            TryRunIcacls(directory, "/inheritance:r", component);
            TryRunIcacls(directory, "/grant:r", component, "SYSTEM:(OI)(CI)(F)", "Administrators:(OI)(CI)(F)");
        }

        foreach (string directory in new[] { LogsDir })
        {
            TryRunIcacls(directory, "/grant:r", component, "SYSTEM:(OI)(CI)(M)", "Administrators:(OI)(CI)(F)", "Users:(OI)(CI)(RX)");
        }
    }

    private void TryRunIcacls(string path, string mode, string component, params string[] grants)
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        try
        {
            List<string> arguments = new() { Quote(path), mode };
            foreach (string grant in grants)
            {
                arguments.Add(grant);
            }

            using Process process = Process.Start(new ProcessStartInfo
            {
                FileName = "icacls.exe",
                Arguments = string.Join(" ", arguments),
                CreateNoWindow = true,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            }) ?? throw new InvalidOperationException("icacls.exe did not start.");
            process.WaitForExit(10000);
            if (process.ExitCode != 0)
            {
                WriteLog(component, "path.acl.failed", "Failed to apply ACL.", new { path, mode, stderr = process.StandardError.ReadToEnd() }, "warning");
            }
        }
        catch (Exception ex)
        {
            WriteLog(component, "path.acl.failed", "Failed to apply ACL.", new { path, mode, error = ex.Message }, "warning");
        }
    }

    private static IEnumerable<string> EnumerateExistingFiles(string directory, string pattern)
    {
        if (!Directory.Exists(directory))
        {
            return Array.Empty<string>();
        }

        return Directory.GetFiles(directory, pattern, SearchOption.TopDirectoryOnly);
    }

    private static int JsonCompletenessScore(string path)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
            if (document.RootElement.ValueKind != JsonValueKind.Object)
            {
                return 0;
            }

            int score = document.RootElement.EnumerateObject().Count();
            foreach (string important in new[] { "agentToken", "machineId", "serverBaseUrl", "heartbeatUrl", "jobsPullUrl", "jobsResultUrl" })
            {
                if (document.RootElement.TryGetProperty(important, out JsonElement value)
                    && value.ValueKind == JsonValueKind.String
                    && !string.IsNullOrWhiteSpace(value.GetString()))
                {
                    score += 10;
                }
            }
            return score;
        }
        catch
        {
            return 0;
        }
    }

    private static string ReadMachineId(string path)
    {
        try
        {
            if (!File.Exists(path))
            {
                return "";
            }

            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
            foreach (string name in new[] { "machine_id", "machineId", "MachineId", "agent_id", "agentId" })
            {
                if (document.RootElement.TryGetProperty(name, out JsonElement value) && value.ValueKind == JsonValueKind.String)
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

    private static string FullPath(string path) => Path.GetFullPath(Environment.ExpandEnvironmentVariables(path));

    private static string Quote(string value) => "\"" + value.Replace("\"", "\\\"") + "\"";

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };
}
