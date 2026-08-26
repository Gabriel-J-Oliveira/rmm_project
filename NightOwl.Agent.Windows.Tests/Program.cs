using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Services;
using NightOwl.Agent.Shared;
using System.Diagnostics;
using System.Text.Json;

try
{
    TestNewConfigContainsTrustedReleaseKeys();
    TestLegacyDefaultConfigReceivesTrustedReleaseKeys();
    TestMigrationIsIdempotent();
    TestMigrationDoesNotDuplicateJobTypes();
    TestPreservesIdentityAndEndpoints();
    TestPreservesIntervals();
    TestPreservesCustomAllowedJobTypes();
    TestMigratedConfigDoesNotRestoreExplicitRemoval();
    TestMigrationResultDoesNotExposeSecrets();
    TestMigrationPersistsAndReloads();
    TestPersistedMigrationIsNotReapplied();
    TestPersistenceFailureDoesNotCorruptExistingConfig();
    TestMigrationLogPayloadDoesNotExposeSecrets();
    TestUninstallerRunnerPayloadCopiesSelfContainedFiles();
    TestRepairRunnerScriptUsesPinnedReleaseAndNoEnrollment();
    TestExternalRepairRunnerSkipsInterruptedRecoveryUntilTimeout();
    TestExternalRepairRunnerTimeoutAllowsInterruptedRecovery();
    TestRepairRunnerScriptPersistsCompletedResult();
    TestRepairRunnerScriptPersistsFailedResult();
    TestRepairRunnerScriptWritesDiagnosticWhenResultPersistenceFails();

    Console.WriteLine("NightOwl agent config migration tests passed.");
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex);
    Environment.Exit(1);
}

static AgentConfig LegacyConfig()
{
    return new AgentConfig
    {
        ConfigMigrationVersion = 1,
        AgentToken = "super-secret-token",
        MachineId = "machine-taxcel",
        ServerBaseUrl = "https://nightowl.controlsul.com.br",
        HeartbeatUrl = "https://nightowl.controlsul.com.br/api/agent/heartbeat/",
        CollectUrl = "https://nightowl.controlsul.com.br/api/agent/collect/",
        JobsPullUrl = "https://nightowl.controlsul.com.br/api/agent/jobs/pull/",
        JobsResultUrl = "https://nightowl.controlsul.com.br/api/agent/jobs/result/",
        Intervals = new AgentIntervals
        {
            HeartbeatSeconds = 123,
            CollectSeconds = 456,
            JobsSeconds = 78
        },
        AllowedJobTypes = new List<string>
        {
            "ping",
            "collect_logs",
            "collect_disks",
            "collect_software",
            "collect_security",
            "windows_update_scan",
            "force_inventory",
            "update_agent",
            "repair_agent",
            "restart_agent"
        }
    };
}

static void TestNewConfigContainsTrustedReleaseKeys()
{
    AgentConfig config = new();
    ConfigService.ApplyConfigMigrations(config);

    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "New config should allow update_trusted_release_keys.");
    Require(config.AllowedJobTypes.Contains("uninstall_agent", StringComparer.OrdinalIgnoreCase), "New config should allow uninstall_agent.");
    Require(config.AllowedJobTypes.Contains("repair_agent", StringComparer.OrdinalIgnoreCase), "New config should allow repair_agent.");
    Require(config.ConfigMigrationVersion == ConfigService.CurrentConfigMigrationVersion, "New config should persist current migration version.");
}

static void TestLegacyDefaultConfigReceivesTrustedReleaseKeys()
{
    AgentConfig config = LegacyConfig();

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);

    Require(result.Applied, "Legacy config should receive migration.");
    Require(result.FromVersion == 1, "Legacy migration should start at v1.");
    Require(result.ToVersion == ConfigService.CurrentConfigMigrationVersion, "Legacy migration should finish at current version.");
    Require(result.AddedAllowedJobTypes.Contains("update_trusted_release_keys"), "Migration result should report added job type.");
    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Legacy config should allow update_trusted_release_keys.");
    Require(config.AllowedJobTypes.Contains("uninstall_agent", StringComparer.OrdinalIgnoreCase), "Legacy config should allow uninstall_agent.");
    Require(config.AllowedJobTypes.Contains("repair_agent", StringComparer.OrdinalIgnoreCase), "Legacy config should allow repair_agent.");
}

static void TestMigrationIsIdempotent()
{
    AgentConfig config = LegacyConfig();
    ConfigService.ApplyConfigMigrations(config);
    List<string> afterFirst = config.AllowedJobTypes.ToList();

    ConfigMigrationResult second = ConfigService.ApplyConfigMigrations(config);

    Require(!second.AddedAllowedJobTypes.Any(), "Second migration should not add job types.");
    Require(afterFirst.SequenceEqual(config.AllowedJobTypes), "Second migration should not alter allowed job types.");
}

static void TestMigrationDoesNotDuplicateJobTypes()
{
    AgentConfig config = LegacyConfig();
    config.AllowedJobTypes.Add("update_trusted_release_keys");
    config.AllowedJobTypes.Add("UPDATE_TRUSTED_RELEASE_KEYS");
    config.AllowedJobTypes.Add("uninstall_agent");
    config.AllowedJobTypes.Add("UNINSTALL_AGENT");
    config.AllowedJobTypes.Add("repair_agent");
    config.AllowedJobTypes.Add("REPAIR_AGENT");

    ConfigService.ApplyConfigMigrations(config);

    Require(config.AllowedJobTypes.Count(job => job.Equals("update_trusted_release_keys", StringComparison.OrdinalIgnoreCase)) == 1, "Migration should not duplicate update_trusted_release_keys.");
    Require(config.AllowedJobTypes.Count(job => job.Equals("uninstall_agent", StringComparison.OrdinalIgnoreCase)) == 1, "Migration should not duplicate uninstall_agent.");
    Require(config.AllowedJobTypes.Count(job => job.Equals("repair_agent", StringComparison.OrdinalIgnoreCase)) == 1, "Migration should not duplicate repair_agent.");
}

static void TestPreservesIdentityAndEndpoints()
{
    AgentConfig config = LegacyConfig();

    ConfigService.ApplyConfigMigrations(config);

    Require(config.MachineId == "machine-taxcel", "Migration should preserve machineId.");
    Require(config.AgentToken == "super-secret-token", "Migration should preserve agentToken.");
    Require(config.ServerBaseUrl == "https://nightowl.controlsul.com.br", "Migration should preserve serverBaseUrl.");
    Require(config.HeartbeatUrl.EndsWith("/api/agent/heartbeat/"), "Migration should preserve heartbeatUrl.");
    Require(config.JobsPullUrl.EndsWith("/api/agent/jobs/pull/"), "Migration should preserve jobsPullUrl.");
}

static void TestPreservesIntervals()
{
    AgentConfig config = LegacyConfig();

    ConfigService.ApplyConfigMigrations(config);

    Require(config.Intervals.HeartbeatSeconds == 123, "Migration should preserve heartbeat interval.");
    Require(config.Intervals.CollectSeconds == 456, "Migration should preserve collect interval.");
    Require(config.Intervals.JobsSeconds == 78, "Migration should preserve jobs interval.");
}

static void TestPreservesCustomAllowedJobTypes()
{
    AgentConfig config = LegacyConfig();
    config.AllowedJobTypes.Remove("collect_logs");
    config.AllowedJobTypes.Add("custom_local_job");

    ConfigService.ApplyConfigMigrations(config);

    Require(!config.AllowedJobTypes.Contains("collect_logs", StringComparer.OrdinalIgnoreCase), "Migration should preserve explicit legacy removal of unrelated job types.");
    Require(config.AllowedJobTypes.Contains("custom_local_job", StringComparer.OrdinalIgnoreCase), "Migration should preserve custom allowed job types.");
    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Migration should add the new known default to legacy configs.");
    Require(config.AllowedJobTypes.Contains("uninstall_agent", StringComparer.OrdinalIgnoreCase), "Migration should add uninstall_agent to legacy configs.");
    Require(config.AllowedJobTypes.Contains("repair_agent", StringComparer.OrdinalIgnoreCase), "Migration should add repair_agent to legacy configs.");
}

static void TestMigratedConfigDoesNotRestoreExplicitRemoval()
{
    AgentConfig config = LegacyConfig();
    config.ConfigMigrationVersion = ConfigService.CurrentConfigMigrationVersion;
    config.AllowedJobTypes.Remove("update_trusted_release_keys");

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);

    Require(!result.AddedAllowedJobTypes.Any(), "Already migrated config should not add removed job types.");
    Require(!config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Already migrated config should preserve explicit removal.");
}

static void TestMigrationResultDoesNotExposeSecrets()
{
    AgentConfig config = LegacyConfig();

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);
    string text = string.Join("|", result.AddedAllowedJobTypes) + "|" + result.FromVersion + "|" + result.ToVersion;

    Require(!text.Contains(config.AgentToken, StringComparison.OrdinalIgnoreCase), "Migration result should not expose agent token.");
    Require(!text.Contains(config.MachineId, StringComparison.OrdinalIgnoreCase), "Migration result should not expose machine id.");
}

static void TestMigrationPersistsAndReloads()
{
    string dir = CreateTempDir();
    try
    {
        string path = Path.Combine(dir, "agent.config.json");
        AgentConfig config = LegacyConfig();
        ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);

        Require(ConfigService.PersistConfigAtomic(path, config, out string error), $"Migrated config should persist atomically: {error}");

        AgentConfig reloaded = JsonSerializer.Deserialize<AgentConfig>(File.ReadAllText(path), new JsonSerializerOptions(JsonSerializerDefaults.Web)) ?? throw new InvalidOperationException("Persisted config could not be reloaded.");
        Require(result.Applied, "Legacy config should have been migrated before persistence.");
        Require(reloaded.ConfigMigrationVersion == ConfigService.CurrentConfigMigrationVersion, "Persisted config should keep migration version v2.");
    Require(reloaded.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Persisted config should include update_trusted_release_keys.");
    Require(reloaded.AllowedJobTypes.Contains("repair_agent", StringComparer.OrdinalIgnoreCase), "Persisted config should include repair_agent.");
        Require(reloaded.AgentToken == "super-secret-token", "Persisted migration should preserve agent token.");
        Require(reloaded.MachineId == "machine-taxcel", "Persisted migration should preserve machine id.");
        Require(reloaded.ServerBaseUrl == "https://nightowl.controlsul.com.br", "Persisted migration should preserve server URL.");
        Require(reloaded.Intervals.HeartbeatSeconds == 123, "Persisted migration should preserve intervals.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestPersistedMigrationIsNotReapplied()
{
    AgentConfig config = LegacyConfig();
    ConfigService.ApplyConfigMigrations(config);

    ConfigMigrationResult second = ConfigService.ApplyConfigMigrations(config);

    Require(!second.Applied, "Reloaded v2 config should not apply migration again.");
    Require(!second.AddedAllowedJobTypes.Any(), "Reloaded v2 config should not add job types again.");
}

static void TestPersistenceFailureDoesNotCorruptExistingConfig()
{
    string dir = CreateTempDir();
    try
    {
        string path = Path.Combine(dir, "agent.config.json");
        AgentConfig existing = LegacyConfig();
        existing.AgentToken = "existing-token";
        Require(ConfigService.PersistConfigAtomic(path, existing, out string initialError), $"Initial config should persist: {initialError}");
        string before = File.ReadAllText(path);

        AgentConfig migrated = LegacyConfig();
        ConfigService.ApplyConfigMigrations(migrated);
        bool persisted;
        string error;
        using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.None))
        {
            persisted = ConfigService.PersistConfigAtomic(path, migrated, out error);
        }

        Require(!persisted, "Persistence should report failure when destination cannot be replaced.");
        Require(!string.IsNullOrWhiteSpace(error), "Persistence failure should expose a sanitized error code/message.");
        Require(File.ReadAllText(path) == before, "Persistence failure should not corrupt or partially replace existing config.");
        AgentConfig reloaded = JsonSerializer.Deserialize<AgentConfig>(File.ReadAllText(path), new JsonSerializerOptions(JsonSerializerDefaults.Web)) ?? throw new InvalidOperationException("Existing config should remain valid JSON.");
        Require(reloaded.AgentToken == "existing-token", "Existing config should remain unchanged after failed persistence.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestMigrationLogPayloadDoesNotExposeSecrets()
{
    AgentConfig config = LegacyConfig();
    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);
    string line = ConfigService.BuildConfigMigrationLogLine(result, "config.migration.persist_failed", $"agentToken={config.AgentToken}");

    Require(!line.Contains(config.AgentToken, StringComparison.OrdinalIgnoreCase), "Migration log line should redact agent token.");
    Require(line.Contains("config.migration.persist_failed", StringComparison.OrdinalIgnoreCase), "Migration log line should include event type.");
}

static void TestUninstallerRunnerPayloadCopiesSelfContainedFiles()
{
    string dir = CreateTempDir();
    try
    {
        string installPath = Path.Combine(dir, "AgentDotNet");
        string runnerPath = Path.Combine(dir, "Runner", "uninstall-job");
        Directory.CreateDirectory(Path.Combine(installPath, "runtimes", "win-x64", "native"));
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Uninstaller.exe"), "fake exe");
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Uninstaller.dll"), "fake dll");
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Shared.dll"), "shared");
        File.WriteAllText(Path.Combine(installPath, "hostfxr.dll"), "runtime");
        File.WriteAllText(Path.Combine(installPath, "runtimes", "win-x64", "native", "dependency.dll"), "native");

        int copied = JobExecutor.CopyUninstallerRunnerPayload(installPath, runnerPath);

        Require(copied >= 5, "Runner payload copy should include all self-contained files.");
        Require(File.Exists(Path.Combine(runnerPath, "NightOwl.Agent.Uninstaller.exe")), "Runner payload should include uninstaller exe.");
        Require(File.Exists(Path.Combine(runnerPath, "hostfxr.dll")), "Runner payload should include runtime dependency.");
        Require(File.Exists(Path.Combine(runnerPath, "runtimes", "win-x64", "native", "dependency.dll")), "Runner payload should include nested dependencies.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestRepairRunnerScriptUsesPinnedReleaseAndNoEnrollment()
{
    AgentConfig config = new()
    {
        ServerBaseUrl = "https://nightowl.controlsul.com.br",
        InstallPath = @"C:\ProgramData\NightOwl\AgentDotNet",
        PendingResultsPath = @"C:\ProgramData\NightOwl\State\pending-results",
        AgentVersion = "0.1.1.0-rc19",
        MachineId = "machine-repair-test",
        AgentToken = "super-secret-token"
    };
    AgentJobRequest job = new()
    {
        Id = Guid.NewGuid().ToString(),
        Type = "repair_agent",
        CorrelationId = Guid.NewGuid().ToString(),
    };

    string script = JobExecutor.BuildRepairRunnerScript(
        job,
        config,
        DateTimeOffset.UtcNow,
        @"C:\ProgramData\NightOwl\AgentDotNet\Install-NightOwlAgentDotNet.ps1",
        @"C:\ProgramData\NightOwl\Trust\release-public-keys.json",
        "0.1.1.0-rc19",
        "0.1.1.0-rc19",
        "development",
        "release-id-123",
        "https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc19/NightOwl.Agent.Windows.zip",
        new string('a', 64),
        new string('b', 64),
        new string('c', 64),
        "nightowl-release-2026-02");

    Require(script.Contains("'-Repair'", StringComparison.OrdinalIgnoreCase), "Repair runner should call installer with -Repair.");
    Require(script.Contains("'-InstallAsService'", StringComparison.OrdinalIgnoreCase), "Repair runner should repair service installation.");
    Require(script.Contains("'-TrustedPublicKeysPath'", StringComparison.OrdinalIgnoreCase), "Repair runner should pass local trust bundle.");
    Require(script.Contains("'-ExpectedVersion'", StringComparison.OrdinalIgnoreCase), "Repair runner should pin expected version.");
    Require(script.Contains("0.1.1.0-rc19", StringComparison.OrdinalIgnoreCase), "Repair runner should include pinned version.");
    Require(script.Contains("release-id-123", StringComparison.OrdinalIgnoreCase), "Repair runner should include release id.");
    Require(script.Contains("enrollment_performed = $false", StringComparison.OrdinalIgnoreCase), "Repair result should state enrollment was not performed.");
    Require(!script.Contains("super-secret-token", StringComparison.OrdinalIgnoreCase), "Repair runner script must not contain the agent token.");
    Require(!script.Contains("-EnrollmentToken", StringComparison.OrdinalIgnoreCase), "Repair runner must not pass enrollment token.");
    Require(script.Contains("function Write-JsonAtomic", StringComparison.OrdinalIgnoreCase), "Repair runner should persist JSON through an atomic helper.");
    Require(script.Contains("[datetimeoffset]::UtcNow", StringComparison.OrdinalIgnoreCase), "Repair runner should use DateTimeOffset for finish timestamps.");
    Require(script.Contains("Complete-RepairJobState", StringComparison.OrdinalIgnoreCase), "Repair runner should finalize the local external-runner marker.");
    Require(script.Contains("Save-MinimalRepairFailure", StringComparison.OrdinalIgnoreCase), "Repair runner should have a non-recursive minimal failure path.");
}

static void TestExternalRepairRunnerSkipsInterruptedRecoveryUntilTimeout()
{
    JobStateRecord record = new()
    {
        JobId = Guid.NewGuid().ToString(),
        JobType = "repair_agent",
        Status = "running",
        ExternalRunnerActive = true,
        ExternalRunnerStartedAt = DateTimeOffset.UtcNow.AddMinutes(-3),
        ExternalRunnerTimeoutSeconds = 900,
        ExternalRunnerPath = @"C:\ProgramData\NightOwl\Updates\Runner\repair-test\Run-NightOwlAgentRepair.ps1",
    };

    Require(
        JobExecutionCoordinator.ShouldSkipInterruptedRecoveryForExternalRunner(record, DateTimeOffset.UtcNow),
        "Active repair external runner should not be marked interrupted during service restart.");
}

static void TestExternalRepairRunnerTimeoutAllowsInterruptedRecovery()
{
    JobStateRecord record = new()
    {
        JobId = Guid.NewGuid().ToString(),
        JobType = "repair_agent",
        Status = "running",
        ExternalRunnerActive = true,
        ExternalRunnerStartedAt = DateTimeOffset.UtcNow.AddMinutes(-30),
        ExternalRunnerTimeoutSeconds = 900,
        ExternalRunnerPath = @"C:\ProgramData\NightOwl\Updates\Runner\repair-test\Run-NightOwlAgentRepair.ps1",
    };

    Require(
        !JobExecutionCoordinator.ShouldSkipInterruptedRecoveryForExternalRunner(record, DateTimeOffset.UtcNow),
        "Expired repair external runner marker should allow JOB_INTERRUPTED recovery.");
}

static void TestRepairRunnerScriptPersistsCompletedResult()
{
    RunRepairRunnerScriptFunctionalTest(installerExitCode: 0, breakPendingDirectory: false, expectedStatus: JobFinalStatuses.Completed);
}

static void TestRepairRunnerScriptPersistsFailedResult()
{
    RunRepairRunnerScriptFunctionalTest(installerExitCode: 7, breakPendingDirectory: false, expectedStatus: JobFinalStatuses.Failed);
}

static void TestRepairRunnerScriptWritesDiagnosticWhenResultPersistenceFails()
{
    RepairRunnerTestResult result = RunRepairRunnerScriptFunctionalTest(installerExitCode: 0, breakPendingDirectory: true, expectedStatus: "");
    string runnerLog = Path.Combine(result.RunnerDir, "repair.runner.log");
    Require(File.Exists(runnerLog), "Repair runner should write a diagnostic log when result persistence fails.");
    string log = File.ReadAllText(runnerLog);
    Require(log.Contains("runner_failed", StringComparison.OrdinalIgnoreCase) || log.Contains("minimal_result_persist_failed", StringComparison.OrdinalIgnoreCase), "Repair runner diagnostic should include a failure stage.");
    Require(log.Contains(result.JobId, StringComparison.OrdinalIgnoreCase), "Repair runner diagnostic should include job_id.");
}

static RepairRunnerTestResult RunRepairRunnerScriptFunctionalTest(int installerExitCode, bool breakPendingDirectory, string expectedStatus)
{
    string powershell = GetWindowsPowerShellPath();
    Require(File.Exists(powershell), "Windows PowerShell 5.1 is required for repair runner functional tests.");

    string root = CreateTempDir();
    try
    {
        string installPath = Path.Combine(root, "AgentDotNet");
        string pendingDir = Path.Combine(root, "pending-results");
        string jobsDir = Path.Combine(root, "jobs");
        string runnerDir = Path.Combine(root, "runner");
        string trustPath = Path.Combine(root, "Trust", "release-public-keys.json");
        Directory.CreateDirectory(installPath);
        Directory.CreateDirectory(pendingDir);
        Directory.CreateDirectory(jobsDir);
        Directory.CreateDirectory(runnerDir);
        Directory.CreateDirectory(Path.GetDirectoryName(trustPath)!);
        File.WriteAllText(trustPath, "{}");
        File.WriteAllText(Path.Combine(installPath, "agent.config.json"), "{}");

        string installerPath = Path.Combine(root, "FakeInstall-NightOwlAgentDotNet.ps1");
        File.WriteAllText(installerPath, @"
param(
    [switch]$Repair,
    [switch]$InstallAsService,
    [string]$ServerUrl,
    [string]$PackageUrl,
    [string]$TrustedPublicKeysPath,
    [string]$ExpectedVersion,
    [string]$ExpectedChannel,
    [string]$ExpectedPackageSha256,
    [string]$ExpectedReleaseId,
    [switch]$RunCheck,
    [switch]$NonInteractive
)
Set-Content -Path '" + Path.Combine(installPath, "agent.version.json").Replace("'", "''") + @"' -Value ('{""version"":""' + $ExpectedVersion + '""}') -Encoding UTF8
Write-Output 'status=completed'
Write-Output 'operation=repair'
Write-Output ('installed_version=' + $ExpectedVersion)
exit " + installerExitCode.ToString(System.Globalization.CultureInfo.InvariantCulture) + @"
");

        if (breakPendingDirectory)
        {
            Directory.Delete(pendingDir);
            File.WriteAllText(pendingDir, "not a directory");
        }

        string jobId = Guid.NewGuid().ToString();
        JobStore store = new(jobsDir);
        store.Mark(jobId, "repair_agent", "running", 1, "corr-repair");
        string jobStatePath = store.PathFor(jobId);
        store.MarkExternalRunnerStarted(jobId, "repair_agent", Path.Combine(runnerDir, "Run-NightOwlAgentRepair.ps1"), 900);

        AgentConfig config = new()
        {
            ServerBaseUrl = "https://nightowl.controlsul.com.br",
            InstallPath = installPath,
            PendingResultsPath = pendingDir,
            AgentVersion = "0.1.1.0-rc23",
            MachineId = "machine-repair-test",
            AgentToken = "super-secret-token"
        };
        AgentJobRequest job = new()
        {
            Id = jobId,
            Type = "repair_agent",
            Attempt = 1,
            CorrelationId = "corr-repair",
        };
        string script = JobExecutor.BuildRepairRunnerScript(
            job,
            config,
            DateTimeOffset.UtcNow.AddSeconds(-1),
            installerPath,
            trustPath,
            "0.1.1.0-rc23",
            "0.1.1.0-rc23",
            "development",
            "release-id-123",
            "https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc23/NightOwl.Agent.Windows.zip",
            new string('a', 64),
            new string('b', 64),
            new string('c', 64),
            "nightowl-release-2026-02",
            jobStatePath);
        string runnerScript = Path.Combine(runnerDir, "Run-NightOwlAgentRepair.ps1");
        File.WriteAllText(runnerScript, script);

        using Process process = Process.Start(new ProcessStartInfo
        {
            FileName = powershell,
            Arguments = "-NoProfile -ExecutionPolicy Bypass -File " + QuoteArg(runnerScript),
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        }) ?? throw new InvalidOperationException("Failed to start repair runner functional test.");
        string stdout = process.StandardOutput.ReadToEnd();
        string stderr = process.StandardError.ReadToEnd();
        process.WaitForExit(15000);
        Require(process.HasExited, "Repair runner script did not exit.");

        if (!breakPendingDirectory)
        {
            string resultPath = Path.Combine(pendingDir, $"job-result-{jobId}.json");
            Require(File.Exists(resultPath), "Repair runner should write pending result.");
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(resultPath));
            JsonElement rootElement = document.RootElement;
            Require(rootElement.GetProperty("status").GetString() == expectedStatus, $"Repair runner pending result should be {expectedStatus}.");
            Require(rootElement.GetProperty("duration_seconds").ValueKind == JsonValueKind.Number, "Repair runner duration_seconds should be numeric.");
            Require(rootElement.GetProperty("duration_seconds").GetDouble() >= 0, "Repair runner duration_seconds should be non-negative.");
            JsonElement resultElement = rootElement.GetProperty("result");
            Require(resultElement.GetProperty("type").GetString() == "repair_agent", "Repair runner result type mismatch.");
            Require(resultElement.GetProperty("installed_version").GetString() == "0.1.1.0-rc23", "Repair runner installed version mismatch.");

            JobStateRecord state = store.Load(jobId) ?? throw new InvalidOperationException("Repair job state was not reloaded.");
            Require(!state.ExternalRunnerActive, "Repair runner should finalize external runner marker.");
        }

        return new RepairRunnerTestResult(root, runnerDir, jobId, stdout, stderr);
    }
    catch
    {
        DeleteTempDir(root);
        throw;
    }
}

static string QuoteArg(string value)
{
    return "\"" + value.Replace("\"", "\\\"") + "\"";
}

static string GetWindowsPowerShellPath()
{
    string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
    if (File.Exists(path))
    {
        return path;
    }
    return "powershell.exe";
}

static string CreateTempDir()
{
    string path = Path.Combine(Path.GetTempPath(), "NightOwlConfigTests", Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(path);
    return path;
}

static void DeleteTempDir(string path)
{
    try
    {
        Directory.Delete(path, recursive: true);
    }
    catch
    {
        // Best-effort cleanup for local tests.
    }
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}

sealed record RepairRunnerTestResult(string RootDir, string RunnerDir, string JobId, string Stdout, string Stderr);
