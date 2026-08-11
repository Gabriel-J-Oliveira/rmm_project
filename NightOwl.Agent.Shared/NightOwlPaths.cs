using System.Diagnostics;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Json;

namespace NightOwl.Agent.Shared;

public sealed class NightOwlPaths
{
    public const string ServiceName = "NightOwlAgentDotNet";
    public const string TrayProcessName = "NightOwl.Agent.Tray";
    public const string DefaultServerUrl = "https://nightowl.controlsul.com.br";
    public const string SystemSid = "S-1-5-18";
    public const string AdministratorsSid = "S-1-5-32-544";
    public const string UsersSid = "S-1-5-32-545";
    public const string EveryoneSid = "S-1-1-0";
    public const string AuthenticatedUsersSid = "S-1-5-11";

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
    public string TrustDir { get; }
    public string TrustBundlePath { get; }
    public string TrustSignaturePath { get; }
    public string TrustMetadataPath { get; }
    public string TrustStatePath { get; }
    public string TrustBackupsDir { get; }
    public string TrustDownloadsDir { get; }
    public string LegacyTrustBundlePath { get; }
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
        TrustDir = Path.Combine(Root, "Trust");
        TrustBundlePath = Path.Combine(TrustDir, "release-public-keys.json");
        TrustSignaturePath = Path.Combine(TrustDir, "release-public-keys.sig");
        TrustMetadataPath = Path.Combine(TrustDir, "release-public-keys.meta.json");
        TrustStatePath = Path.Combine(TrustDir, "state.json");
        TrustBackupsDir = Path.Combine(TrustDir, "Backups");
        TrustDownloadsDir = Path.Combine(TrustDir, "Downloads");
        LegacyTrustBundlePath = Path.Combine(InstallDir, "release-public-keys.json");
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
        TrustDir,
        TrustBackupsDir,
        TrustDownloadsDir,
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
        MigrateTrustBundle(component);
        EnsureIdentity(component);
        WriteLog(component, "path.migration.completed", "NightOwl path bootstrap completed.", new
        {
            root = Root,
            install_dir = InstallDir,
            config_path = ConfigPath,
            state_path = StatePath,
            pending_results_dir = PendingResultsDir,
            trust_dir = TrustDir,
            trust_bundle_path = TrustBundlePath
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

    private void MigrateTrustBundle(string component)
    {
        try
        {
            if (!File.Exists(LegacyTrustBundlePath))
            {
                return;
            }

            Directory.CreateDirectory(TrustDir);
            Directory.CreateDirectory(TrustBackupsDir);
            string legacyHash = ComputeSha256(LegacyTrustBundlePath);
            if (!File.Exists(TrustBundlePath))
            {
                File.Copy(LegacyTrustBundlePath, TrustBundlePath, overwrite: false);
                string backup = Path.Combine(TrustBackupsDir, $"legacy-release-public-keys-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}.json");
                File.Copy(LegacyTrustBundlePath, backup, overwrite: false);
                WriteLog(component, "trust.legacy.migrated", "Legacy release public keys copied to Trust directory.", new
                {
                    source = LegacyTrustBundlePath,
                    destination = TrustBundlePath,
                    backup_path = backup,
                    sha256 = legacyHash
                });
                return;
            }

            string currentHash = ComputeSha256(TrustBundlePath);
            if (!legacyHash.Equals(currentHash, StringComparison.OrdinalIgnoreCase))
            {
                WriteLog(component, "trust.legacy.preserved", "Legacy release public keys preserved because Trust bundle already exists with different content.", new
                {
                    legacy_path = LegacyTrustBundlePath,
                    trust_bundle_path = TrustBundlePath,
                    legacy_sha256 = legacyHash,
                    trust_sha256 = currentHash
                }, "warning");
            }
        }
        catch (Exception ex)
        {
            WriteLog(component, "trust.legacy.migration_failed", "Legacy release public key migration failed.", new { error = ex.Message }, "error");
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

    public IReadOnlyList<NightOwlAclPolicy> GetAclPolicies() => new[]
    {
        NightOwlAclPolicy.UsersRead(Root, "root"),
        NightOwlAclPolicy.UsersRead(InstallDir, "install", normalizeChildren: true),
        NightOwlAclPolicy.UsersRead(ConfigDir, "config"),
        NightOwlAclPolicy.UsersRead(IdentityDir, "identity"),
        NightOwlAclPolicy.UsersRead(StateDir, "state"),
        NightOwlAclPolicy.UsersRead(PendingResultsDir, "pending-results"),
        NightOwlAclPolicy.UsersRead(TrustDir, "trust", normalizeChildren: true),
        NightOwlAclPolicy.AdminOnly(TrustBackupsDir, "trust-backups", normalizeChildren: true),
        NightOwlAclPolicy.AdminOnly(TrustDownloadsDir, "trust-downloads", normalizeChildren: true),
        NightOwlAclPolicy.UsersRead(LogsDir, "logs", systemRights: "M"),
        NightOwlAclPolicy.AdminOnly(UpdatesDir, "updates", normalizeChildren: true),
        NightOwlAclPolicy.AdminOnly(UpdatesDownloadsDir, "updates-downloads", normalizeChildren: true),
        NightOwlAclPolicy.AdminOnly(UpdatesStagingDir, "updates-staging", normalizeChildren: true),
        NightOwlAclPolicy.AdminOnly(UpdatesBackupDir, "updates-backup", normalizeChildren: true),
        NightOwlAclPolicy.AdminOnly(UpdatesPendingDir, "updates-pending", normalizeChildren: true),
        NightOwlAclPolicy.AdminOnly(UpdatesRunnerDir, "updates-runner", normalizeChildren: true),
        NightOwlAclPolicy.AdminOnly(DiagnosticsDir, "diagnostics"),
        NightOwlAclPolicy.AdminOnly(PackagesDir, "packages"),
        NightOwlAclPolicy.AdminOnly(CacheDir, "cache")
    };

    public void ProtectReleaseTrustDirectories(string component)
    {
        ProtectAclPolicies(component, policy => policy.Scope.StartsWith("trust", StringComparison.OrdinalIgnoreCase));
    }

    private void ProtectPersistentDirectories(string component)
    {
        ProtectAclPolicies(component, _ => true);
    }

    private void ProtectAclPolicies(string component, Func<NightOwlAclPolicy, bool> filter)
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        foreach (NightOwlAclPolicy policy in GetAclPolicies().Where(filter))
        {
            ApplyAclPolicyTarget(policy, component);
            if (policy.NormalizeChildren)
            {
                NormalizeExistingChildren(policy, component);
            }
        }
    }

    private void ApplyAclPolicyTarget(NightOwlAclPolicy policy, string component)
    {
        try
        {
            if (Directory.Exists(policy.Path))
            {
                ApplyDirectoryAcl(policy.Path, policy);
            }
            else if (File.Exists(policy.Path))
            {
                ApplyFileAcl(policy.Path, policy);
            }
        }
        catch (Exception ex)
        {
            WriteLog(component, "acl.apply.failed", "Failed to apply declared ACL policy.", new
            {
                path = policy.Path,
                scope = policy.Scope,
                error = ex.GetType().Name,
                message = ex.Message
            }, "warning");
        }
    }

    private void NormalizeExistingChildren(NightOwlAclPolicy policy, string component)
    {
        if (!Directory.Exists(policy.Path))
        {
            return;
        }

        int filesProcessed = 0;
        int directoriesProcessed = 0;
        int reparseSkipped = 0;
        int failures = 0;
        List<object> failureSamples = new();
        Stack<string> pending = new();
        pending.Push(policy.Path);

        while (pending.Count > 0)
        {
            string current = pending.Pop();
            if (!IsWithinRoot(current))
            {
                failures++;
                AddFailureSample(failureSamples, current, "OutsideRoot", "ACL traversal attempted to leave NightOwl root.");
                continue;
            }

            IEnumerable<string> children;
            try
            {
                children = Directory.EnumerateFileSystemEntries(current).ToArray();
            }
            catch (Exception ex)
            {
                failures++;
                AddFailureSample(failureSamples, current, ex.GetType().Name, ex.Message);
                continue;
            }

            foreach (string child in children)
            {
                try
                {
                    FileAttributes attributes = File.GetAttributes(child);
                    if (ShouldSkipAclTraversal(attributes))
                    {
                        reparseSkipped++;
                        WriteLog(component, "acl.recursive.skipped_reparse_point", "Skipped reparse point during ACL normalization.", new
                        {
                            path = child,
                            scope = policy.Scope
                        }, "warning");
                        continue;
                    }

                    if (attributes.HasFlag(FileAttributes.Directory))
                    {
                        ApplyDirectoryAcl(child, policy);
                        directoriesProcessed++;
                        pending.Push(child);
                    }
                    else
                    {
                        ApplyFileAcl(child, policy);
                        filesProcessed++;
                    }
                }
                catch (Exception ex)
                {
                    failures++;
                    AddFailureSample(failureSamples, child, ex.GetType().Name, ex.Message);
                }
            }
        }

        string level = failures == 0 ? "info" : "warning";
        WriteLog(component, failures == 0 ? "acl.recursive.completed" : "acl.apply.failed", "ACL recursive normalization completed.", new
        {
            path = policy.Path,
            scope = policy.Scope,
            files_processed = filesProcessed,
            directories_processed = directoriesProcessed,
            reparse_points_skipped = reparseSkipped,
            failures,
            failure_samples = failureSamples
        }, level);
    }

    public static bool ShouldSkipAclTraversal(FileAttributes attributes) => attributes.HasFlag(FileAttributes.ReparsePoint);

    private static void ApplyDirectoryAcl(string path, NightOwlAclPolicy policy)
    {
        DirectorySecurity security = new();
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        AddDirectoryRule(security, SystemSid, policy.SystemRights);
        AddDirectoryRule(security, AdministratorsSid, FileSystemRights.FullControl);
        if (policy.AllowUsersRead)
        {
            AddDirectoryRule(security, UsersSid, FileSystemRights.ReadAndExecute);
        }
        new DirectoryInfo(path).SetAccessControl(security);
    }

    private static void ApplyFileAcl(string path, NightOwlAclPolicy policy)
    {
        FileSecurity security = new();
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        AddFileRule(security, SystemSid, policy.SystemRights);
        AddFileRule(security, AdministratorsSid, FileSystemRights.FullControl);
        if (policy.AllowUsersRead)
        {
            AddFileRule(security, UsersSid, FileSystemRights.ReadAndExecute);
        }
        new FileInfo(path).SetAccessControl(security);
    }

    private static void AddDirectoryRule(DirectorySecurity security, string sid, FileSystemRights rights)
    {
        security.AddAccessRule(new FileSystemAccessRule(
            new SecurityIdentifier(sid),
            rights,
            InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
            PropagationFlags.None,
            AccessControlType.Allow));
    }

    private static void AddFileRule(FileSecurity security, string sid, FileSystemRights rights)
    {
        security.AddAccessRule(new FileSystemAccessRule(
            new SecurityIdentifier(sid),
            rights,
            InheritanceFlags.None,
            PropagationFlags.None,
            AccessControlType.Allow));
    }

    private bool IsWithinRoot(string path)
    {
        string root = Path.GetFullPath(Root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string candidate = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        return candidate.StartsWith(root, StringComparison.OrdinalIgnoreCase);
    }

    private static void AddFailureSample(List<object> samples, string path, string error, string message)
    {
        if (samples.Count >= 10)
        {
            return;
        }
        samples.Add(new { path, error, message });
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

    private static string ComputeSha256(string path)
    {
        using FileStream stream = File.OpenRead(path);
        return Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };
}

public sealed record NightOwlAclPolicy(string Path, string Scope, IReadOnlyList<string> Grants, bool AllowUsersRead, FileSystemRights SystemRights, bool NormalizeChildren)
{
    public static NightOwlAclPolicy UsersRead(string path, string scope, string systemRights = "F", bool normalizeChildren = false) => new(
        path,
        scope,
        new[]
        {
            $"*{NightOwlPaths.SystemSid}:(OI)(CI)({systemRights})",
            $"*{NightOwlPaths.AdministratorsSid}:(OI)(CI)(F)",
            $"*{NightOwlPaths.UsersSid}:(OI)(CI)(RX)"
        },
        AllowUsersRead: true,
        ParseRights(systemRights),
        normalizeChildren);

    public static NightOwlAclPolicy AdminOnly(string path, string scope, string systemRights = "F", bool normalizeChildren = false) => new(
        path,
        scope,
        new[]
        {
            $"*{NightOwlPaths.SystemSid}:(OI)(CI)({systemRights})",
            $"*{NightOwlPaths.AdministratorsSid}:(OI)(CI)(F)"
        },
        AllowUsersRead: false,
        ParseRights(systemRights),
        normalizeChildren);

    private static FileSystemRights ParseRights(string rights) => rights.Equals("M", StringComparison.OrdinalIgnoreCase)
        ? FileSystemRights.Modify
        : FileSystemRights.FullControl;
}
