using UpdaterProgram = NightOwl.Agent.Updater.Program;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Diagnostics;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;

try
{
    Require(
        UpdaterProgram.CompareVersions("0.1.1.0-rc3", "0.1.1.0-rc2") > 0,
        "RC3 should compare newer than RC2.");
    Require(
        UpdaterProgram.DecideVersionAction("0.1.1.0-rc2", "0.1.1.0-rc3", force: false) == UpdaterProgram.VersionUpdateAction.UpdateAllowed,
        "RC2 to RC3 should continue to update.");
    Require(
        UpdaterProgram.DecideVersionAction("0.1.1.0-rc3", "0.1.1.0-rc3", force: false) == UpdaterProgram.VersionUpdateAction.AlreadyCurrent,
        "Same installed and target version should be already_current.");
    Require(
        UpdaterProgram.DecideVersionAction("0.1.1.0-rc3", "0.1.1.0-rc2", force: false) == UpdaterProgram.VersionUpdateAction.DowngradeBlocked,
        "Downgrade without force should be blocked.");
    Require(
        UpdaterProgram.DecideVersionAction("0.1.1.0-rc3", "0.1.1.0-rc2", force: true) == UpdaterProgram.VersionUpdateAction.UpdateAllowed,
        "Downgrade with force should be allowed by updater version decision.");

    using RSA signingKey = RSA.Create(2048);
    string publicXml = signingKey.ToXmlString(false);
    byte[] manifestBytes = Encoding.UTF8.GetBytes("{\"channel\":\"development\",\"key_id\":\"nightowl-test\",\"version\":\"0.1.1.0-rc6\"}");
    byte[] signature = signingKey.SignData(
        manifestBytes,
        HashAlgorithmName.SHA256,
        RSASignaturePadding.Pss);
    Require(
        UpdaterProgram.VerifyReleaseManifestSignatureForTest(manifestBytes, signature, publicXml),
        "Updater should accept a valid RSA-PSS/SHA-256 release manifest signature.");

    byte[] tamperedManifest = (byte[])manifestBytes.Clone();
    tamperedManifest[0] ^= 0x01;
    Require(
        !UpdaterProgram.VerifyReleaseManifestSignatureForTest(tamperedManifest, signature, publicXml),
        "Updater should reject a tampered release manifest.");

    using RSA differentKey = RSA.Create(2048);
    Require(
        !UpdaterProgram.VerifyReleaseManifestSignatureForTest(manifestBytes, signature, differentKey.ToXmlString(false)),
        "Updater should reject a release manifest signature verified with a different key.");

    TestSignedManifestChannelGate();

    TestCopyNormal();
    TestBackupManifestIsNotManaged();
    TestTemporaryLockRetry();
    TestPermanentLockTimeout();
    TestUnauthorizedAccess();
    TestProcessQuiesce();
    TestUnrelatedProcessIgnored();
    TestCurrentProcessIsIgnored();
    TestRollbackOriginalErrorPreserved();
    TestHealthCheckOrderingAndOutcomes();
    TestInterruptedHealthCheckRecovery();
    TestOfficialJobRecovery();
    TestRollbackStageRecovery();
    TestTerminalReplayWithoutStaging();
    TestCrashAfterRollbackRestore();
    TestTrayLifecycleSourceMarkers();

    Console.WriteLine("NightOwl updater version decision tests passed.");
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex.Message);
    Environment.Exit(1);
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}

static void TestCopyNormal()
{
    using TempTree tree = TempTree.Create();
    string staged = tree.CreateDirectory("staged");
    string install = tree.CreateDirectory("install");
    File.WriteAllText(Path.Combine(staged, "a.dll"), "new");

    UpdaterProgram.CopyStagedFilesWithRetryForTest(staged, install, TimeSpan.FromSeconds(3));

    Require(File.ReadAllText(Path.Combine(install, "a.dll")) == "new", "Normal copy should write staged file.");
}

static void TestBackupManifestIsNotManaged()
{
    foreach (string residualName in new[] { "backup-manifest.json", "BACKUP-MANIFEST.JSON" })
    {
        using TempTree tree = TempTree.Create();
        string install = tree.CreateDirectory("install");
        string backup = Path.Combine(tree.Root, "backup");
        const string updateId = "synthetic-backup-update";
        const string previousVersion = "0.1.1.0-rc39";
        string[] requiredFiles = {
            "NightOwl.Agent.Windows.exe", "NightOwl.Agent.Updater.exe",
            "NightOwl.Agent.Tray.exe", "agent.version.json"
        };
        foreach (string name in requiredFiles)
        {
            File.WriteAllText(Path.Combine(install, name), name);
        }
        string library = Path.Combine(install, "lib", "helper.dll");
        Directory.CreateDirectory(Path.GetDirectoryName(library)!);
        File.WriteAllText(library, "agent-v1");
        File.WriteAllText(Path.Combine(install, residualName), new string('x', 91547));

        UpdaterProgram.CreateBackupForTest(install, backup, updateId, previousVersion);

        string manifestPath = Path.Combine(backup, "backup-manifest.json");
        Require(File.Exists(manifestPath), "Backup must create its own manifest.");
        using JsonDocument manifest = JsonDocument.Parse(File.ReadAllText(manifestPath));
        JsonElement.ArrayEnumerator entries = manifest.RootElement.GetProperty("files").EnumerateArray();
        List<JsonElement> files = entries.ToList();
        string[] paths = files.Select(file => file.GetProperty("path").GetString() ?? "").ToArray();
        Require(!paths.Contains(residualName, StringComparer.OrdinalIgnoreCase), "Residual backup manifest must not be managed.");
        Require(files.Count == requiredFiles.Length + 1, "Only real agent files should be backed up.");
        foreach (string name in requiredFiles)
        {
            Require(paths.Contains(name), $"Backup should preserve {name}.");
        }
        JsonElement libraryEntry = files.Single(file => file.GetProperty("path").GetString() == "lib/helper.dll");
        string backedUpLibrary = Path.Combine(backup, "lib", "helper.dll");
        Require(File.Exists(backedUpLibrary), "Managed library should be copied.");
        Require(libraryEntry.GetProperty("size").GetInt64() == new FileInfo(backedUpLibrary).Length,
            "Managed library size must be recorded.");
        Require(libraryEntry.GetProperty("sha256").GetString() == Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(backedUpLibrary))).ToLowerInvariant(),
            "Managed library hash must be recorded.");
        UpdaterProgram.ValidateBackupForTest(backup, updateId, previousVersion);

        File.WriteAllText(backedUpLibrary, "agent-v1-extra");
        Require(ExpectThrows(() => UpdaterProgram.ValidateBackupForTest(backup, updateId, previousVersion))
            .Message.Contains("Tamanho invalido", StringComparison.Ordinal), "Size mismatch must still fail validation.");
        File.WriteAllText(backedUpLibrary, "agent-v2");
        Require(ExpectThrows(() => UpdaterProgram.ValidateBackupForTest(backup, updateId, previousVersion))
            .Message.Contains("SHA256 invalido", StringComparison.Ordinal), "Hash mismatch must still fail validation.");

        string staged = tree.CreateDirectory("staged");
        string copied = tree.CreateDirectory("copied");
        File.WriteAllText(Path.Combine(staged, residualName), "stale metadata");
        File.WriteAllText(Path.Combine(staged, "new.dll"), "new agent file");
        UpdaterProgram.CopyStagedFilesWithRetryForTest(staged, copied, TimeSpan.FromSeconds(3));
        Require(!File.Exists(Path.Combine(copied, residualName)), "Staged backup metadata must not enter the installation.");
        Require(File.Exists(Path.Combine(copied, "new.dll")), "Managed staged file should still be installed.");
    }
}

static void TestSignedManifestChannelGate()
{
    DirectoryInfo? root = new(AppContext.BaseDirectory);
    while (root is not null && !File.Exists(Path.Combine(root.FullName, "NightOwl.Agent.Updater", "Program.cs")))
    {
        root = root.Parent;
    }

    Require(root is not null, "Updater source should be available to the contract test.");
    string source = File.ReadAllText(Path.Combine(root!.FullName, "NightOwl.Agent.Updater", "Program.cs"));
    Require(source.Contains(
        "if (!signedManifest.Channel.Equals(manifest.Channel, StringComparison.OrdinalIgnoreCase))",
        StringComparison.Ordinal), "Updater must keep the signed manifest channel equality gate.");
    Require(source.Contains("UpdateErrorCodes.ReleaseChannelMismatch", StringComparison.Ordinal),
        "Updater must reject channel mismatch with RELEASE_CHANNEL_MISMATCH.");
    Require(string.Equals("development", "development", StringComparison.OrdinalIgnoreCase),
        "An artifact channel matching the signed manifest should pass.");
    Require(!string.Equals("pilot", "development", StringComparison.OrdinalIgnoreCase),
        "An administrative channel differing from the signed artifact channel must not pass.");
}

static void TestTemporaryLockRetry()
{
    using TempTree tree = TempTree.Create();
    string staged = tree.CreateDirectory("staged");
    string install = tree.CreateDirectory("install");
    File.WriteAllText(Path.Combine(staged, "clrjit.dll"), "new");
    string target = Path.Combine(install, "clrjit.dll");
    File.WriteAllText(target, "old");

    using FileStream locked = new(target, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
    Task releaser = Task.Run(async () =>
    {
        await Task.Delay(900);
        locked.Dispose();
    });

    UpdaterProgram.CopyStagedFilesWithRetryForTest(staged, install, TimeSpan.FromSeconds(5));
    releaser.Wait();

    Require(File.ReadAllText(target) == "new", "Copy should retry until temporary lock is released.");
}

static void TestPermanentLockTimeout()
{
    using TempTree tree = TempTree.Create();
    string staged = tree.CreateDirectory("staged");
    string install = tree.CreateDirectory("install");
    File.WriteAllText(Path.Combine(staged, "clrjit.dll"), "new");
    string target = Path.Combine(install, "clrjit.dll");
    File.WriteAllText(target, "old");

    using FileStream locked = new(target, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
    Exception ex = ExpectThrows(() => UpdaterProgram.CopyStagedFilesWithRetryForTest(staged, install, TimeSpan.FromMilliseconds(600)));

    Require(ex.Message.Contains(UpdateErrorCodes.UpdateFileLockTimeout, StringComparison.OrdinalIgnoreCase), "Permanent lock should surface UPDATE_FILE_LOCK_TIMEOUT.");
}

static void TestUnauthorizedAccess()
{
    using TempTree tree = TempTree.Create();
    string staged = tree.CreateDirectory("staged");
    string install = tree.CreateDirectory("install");
    File.WriteAllText(Path.Combine(staged, "blocked.dll"), "new");
    string target = Path.Combine(install, "blocked.dll");
    File.WriteAllText(target, "old");
    File.SetAttributes(target, FileAttributes.ReadOnly);

    try
    {
        Exception ex = ExpectThrows(() => UpdaterProgram.CopyStagedFilesWithRetryForTest(staged, install, TimeSpan.FromMilliseconds(600)));

        Require(ex.Message.Contains(UpdateErrorCodes.UpdateFileAccessDenied, StringComparison.OrdinalIgnoreCase), "Access denied should surface UPDATE_FILE_ACCESS_DENIED.");
    }
    finally
    {
        File.SetAttributes(target, FileAttributes.Normal);
    }
}

static void TestProcessQuiesce()
{
    string? powershell = GetWindowsPowerShellPath();
    if (string.IsNullOrWhiteSpace(powershell) || !File.Exists(powershell))
    {
        return;
    }

    using TempTree tree = TempTree.Create();
    string install = tree.CreateDirectory("install");
    string copied = Path.Combine(install, "NightOwl.Agent.Windows.exe");
    File.Copy(powershell, copied);
    using Process process = Process.Start(new ProcessStartInfo
    {
        FileName = copied,
        Arguments = "-NoProfile -Command \"Start-Sleep -Milliseconds 800\"",
        UseShellExecute = false
    }) ?? throw new InvalidOperationException("Failed to start quiesce test process.");

    Thread.Sleep(150);
    Require(
        UpdaterProgram.IsNightOwlRelatedProcessForTest(process, new[] { install }, Environment.ProcessId),
        "Copied NightOwl process under install root should be related.");

    UpdaterProgram.WaitForNightOwlProcessesToExitForTest(new[] { install }, TimeSpan.FromSeconds(5));
    Require(process.HasExited, "Quiesce should wait until NightOwl process exits.");
}

static void TestUnrelatedProcessIgnored()
{
    string? powershell = GetWindowsPowerShellPath();
    if (string.IsNullOrWhiteSpace(powershell) || !File.Exists(powershell))
    {
        return;
    }

    using TempTree tree = TempTree.Create();
    using Process process = Process.Start(new ProcessStartInfo
    {
        FileName = powershell,
        Arguments = "-NoProfile -Command \"Start-Sleep -Seconds 3\"",
        UseShellExecute = false
    }) ?? throw new InvalidOperationException("Failed to start unrelated process.");

    try
    {
        UpdaterProgram.WaitForNightOwlProcessesToExitForTest(new[] { tree.Root }, TimeSpan.FromMilliseconds(600));
        Require(!process.HasExited, "Unrelated process should not be killed or waited on.");
    }
    finally
    {
        try { process.Kill(entireProcessTree: true); } catch { }
    }
}

static string GetWindowsPowerShellPath()
{
    string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
    return File.Exists(path) ? path : "";
}

static void TestCurrentProcessIsIgnored()
{
    using Process current = Process.GetCurrentProcess();
    Require(
        !UpdaterProgram.IsNightOwlRelatedProcessForTest(current, new[] { AppContext.BaseDirectory }, Environment.ProcessId),
        "Current updater process should be ignored.");
}

static void TestRollbackOriginalErrorPreserved()
{
    UpdateState state = UpdateState.Create("update-test", "job-test", "0.1.1.0-rc8", "0.1.1.0-rc9");
    state.MarkRollbackRequired(UpdateStages.ReplacingFiles, UpdateErrorCodes.UpdateFileLockTimeout, "The process cannot access clrjit.dll.");
    state.MarkStage(UpdateStages.RollbackStarting);
    state.MarkStage(UpdateStages.RolledBack);

    Require(state.ErrorCode == UpdateErrorCodes.UpdateFileLockTimeout, "Rollback should preserve original error code.");
    Require(state.ErrorMessage.Contains("clrjit.dll", StringComparison.OrdinalIgnoreCase), "Rollback should preserve original error message.");
}

static void TestHealthCheckOrderingAndOutcomes()
{
    string directory = Path.Combine(Path.GetTempPath(), "nightowl-healthcheck-tests-" + Guid.NewGuid());
    Directory.CreateDirectory(directory);
    try
    {
        UpdateStateStore store = new(Path.Combine(directory, "update-state.json"));
        UpdateState state = UpdateState.Create("early", "job-early", "rc41", "rc42");
        state.MarkStage(UpdateStages.WaitingHealthCheck);
        store.Save(state);
        UpdateState stale = store.Load()!;
        store.TryTransitionNonTerminal("early", current => {
            current.MarkStage(UpdateStages.Completed);
            return true;
        }, out _);
        stale.MarkStage(UpdateStages.WaitingHealthCheck);
        Require(!store.Save(stale), "Early confirmation must not be overwritten by the updater.");
        Require(UpdaterProgram.WaitForHealthCheckCore(
            () => store.Load()!, () => "Running", false, TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(5))
            == UpdaterProgram.HealthCheckWaitResult.Completed, "Early confirmation should finish without timeout or rollback.");

        state = UpdateState.Create("slow", "job-slow", "rc41", "rc42");
        state.MarkStage(UpdateStages.WaitingHealthCheck);
        store.Save(state);
        Task confirmation = Task.Run(async () => {
            await Task.Delay(40);
            store.TryTransitionNonTerminal("slow", current => {
                current.MarkStage(UpdateStages.Completed);
                return true;
            }, out _);
        });
        Require(UpdaterProgram.WaitForHealthCheckCore(
            () => store.Load()!, () => "Running", false, TimeSpan.FromSeconds(1), TimeSpan.FromMilliseconds(5))
            == UpdaterProgram.HealthCheckWaitResult.Completed, "Delayed confirmation should finish normally.");
        confirmation.GetAwaiter().GetResult();

        state = UpdateState.Create("timeout", "job-timeout", "rc41", "rc42");
        state.MarkStage(UpdateStages.WaitingHealthCheck);
        store.Save(state);
        Require(UpdaterProgram.WaitForHealthCheckCore(
            () => store.Load()!, () => "Running", false, TimeSpan.FromMilliseconds(50), TimeSpan.FromMilliseconds(5))
            == UpdaterProgram.HealthCheckWaitResult.Timeout, "Missing confirmation must produce a real timeout.");
        Require(store.TryTransitionNonTerminal("timeout", current => {
            current.MarkRollbackRequired(UpdateStages.WaitingHealthCheck, UpdateErrorCodes.UpdateHealthcheckTimeout, "Timed out.");
            return true;
        }, out _), "Real timeout must leave rollback available.");

        state = UpdateState.Create("rollback", "job-rollback", "rc41", "rc42");
        state.MarkStage(UpdateStages.RollbackWaitingHealthCheck);
        store.Save(state);
        stale = store.Load()!;
        store.TryTransitionNonTerminal("rollback", current => {
            current.MarkStage(UpdateStages.RolledBack);
            return true;
        }, out _);
        stale.MarkStage(UpdateStages.RollbackWaitingHealthCheck);
        Require(!store.Save(stale), "Immediate rollback confirmation must remain terminal.");
        Require(UpdaterProgram.WaitForHealthCheckCore(
            () => store.Load()!, () => "Running", true, TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(5))
            == UpdaterProgram.HealthCheckWaitResult.RolledBack, "Rollback should complete without second confirmation.");
    }
    finally
    {
        Directory.Delete(directory, recursive: true);
    }
}

static void TestInterruptedHealthCheckRecovery()
{
    const string previousVersion = "0.1.1.0-rc41";
    const string targetVersion = "0.1.1.0-rc42";
    string directory = Path.Combine(Path.GetTempPath(), "nightowl-recovery-tests-" + Guid.NewGuid());
    Directory.CreateDirectory(directory);
    try
    {
        UpdateStateStore store = new(Path.Combine(directory, "state.json"));
        UpdateState NewState(string id, string stage)
        {
            UpdateState state = UpdateState.Create(id, "job-" + id, previousVersion, targetVersion);
            state.MarkStage(stage);
            store.Save(state);
            return state;
        }
        int started = 0;
        int rolledBack = 0;
        int? Resume(UpdateState state, string version, string service, bool confirm = true,
            Action? start = null) => UpdaterProgram.ResumeInterruptedHealthCheck(
                state, store, version, () => service,
                start ?? (() => started++),
                (current, restoring) => {
                    if (confirm)
                    {
                        store.TryTransitionNonTerminal(current.UpdateId, persisted => {
                            persisted.MarkStage(restoring ? UpdateStages.RolledBack : UpdateStages.Completed);
                            return true;
                        }, out _);
                    }
                    return UpdaterProgram.WaitForHealthCheckCore(() => store.Load()!, () => "Running", restoring,
                        TimeSpan.FromMilliseconds(30), TimeSpan.FromMilliseconds(2));
                }, current => { rolledBack++; return 1; });

        UpdateState waiting = NewState("before-start", UpdateStages.WaitingHealthCheck);
        Require(Resume(waiting, targetVersion, "Stopped") == 0 && started == 1
            && store.Load()?.CurrentStage == UpdateStages.Completed,
            "Crash before StartService must start and confirm the existing update.");
        UpdateState alreadyStarted = NewState("after-start", UpdateStages.WaitingHealthCheck);
        Require(Resume(alreadyStarted, targetVersion, "Running") == 0 && started == 1,
            "Crash after StartService must wait without starting or reinstalling.");
        UpdateState serviceStarted = NewState("service-started", UpdateStages.ServiceStarted);
        Require(Resume(serviceStarted, targetVersion, "Running") == 0 && store.Load()?.CurrentStage == UpdateStages.Completed,
            "Legacy ServiceStarted must recover to health confirmation.");
        UpdateState completed = NewState("completed", UpdateStages.WaitingHealthCheck);
        store.TryTransitionNonTerminal(completed.UpdateId, state => { state.MarkStage(UpdateStages.Completed); return true; }, out _);
        Require(!store.Save(completed) && store.Load()?.CurrentStage == UpdateStages.Completed,
            "Updater crash after agent completion must not reopen the state.");
        UpdateState persistedCompleted = store.Load()!;
        Require(ReferenceEquals(UpdaterProgram.ResolveRunnerState(persistedCompleted, persistedCompleted.UpdateId,
            persistedCompleted.JobId), persistedCompleted),
            "Runner must return the persisted Completed state for the same update_id.");
        UpdateState restoring = NewState("rollback-before-start", UpdateStages.RollbackWaitingHealthCheck);
        Require(Resume(restoring, previousVersion, "Stopped") == 0 && started == 2
            && store.Load()?.CurrentStage == UpdateStages.RolledBack,
            "Rollback health check must start restored service and confirm rollback.");
        UpdateState terminalRollback = store.Load()!;
        Require(!store.Save(terminalRollback) && store.Load()?.CurrentStage == UpdateStages.RolledBack,
            "RolledBack must remain terminal on retry.");
        Require(ReferenceEquals(UpdaterProgram.ResolveRunnerState(terminalRollback, terminalRollback.UpdateId,
            terminalRollback.JobId), terminalRollback),
            "Runner must return the persisted RolledBack state for the same update_id.");
        UpdateState failedStart = NewState("start-failure", UpdateStages.WaitingHealthCheck);
        Require(Resume(failedStart, targetVersion, "Stopped", start: () => throw new IOException("Synthetic start failure.")) == 1
            && rolledBack == 1 && store.Load()?.RollbackRequired == true,
            "StartService failure must mark rollback required and invoke rollback.");
        UpdateState failedRollbackStart = NewState("rollback-start-failure", UpdateStages.RollbackWaitingHealthCheck);
        Require(Resume(failedRollbackStart, previousVersion, "Stopped", start: () => throw new IOException("Synthetic start failure.")) == 1
            && store.Load()?.CurrentStage == UpdateStages.RollbackFailed,
            "Rollback StartService failure must become terminal RollbackFailed.");
        UpdateState mismatch = NewState("version-mismatch", UpdateStages.WaitingHealthCheck);
        Require(Resume(mismatch, previousVersion, "Running") == 1 && rolledBack == 2
            && store.Load()?.RollbackRequired == true,
            "Version mismatch must enter rollback rather than already_current.");
    }
    finally
    {
        Directory.Delete(directory, recursive: true);
    }
}

static string[] OfficialJobArgs(string jobId = "JOB-A", string target = "0.1.1.0-rc43",
    string releaseId = "release-43", string sha = "synthetic-sha-43")
{
    AgentJobRequest job = new()
    {
        Id = jobId,
        Type = "update_agent",
        Payload = new Dictionary<string, object?>
        {
            ["channel"] = "development", ["target_version"] = target,
            ["release_id"] = releaseId, ["package_url"] = "https://example.invalid/rc43.zip",
            ["sha256"] = sha
        }
    };
    return JobExecutor.BuildUpdaterArguments(job).ToArray();
}

static UpdateState NewOfficialState(string updateId, string stage)
{
    UpdateState state = UpdateState.Create(updateId, "JOB-A", "0.1.1.0-rc42", "0.1.1.0-rc43");
    state.Source = "job";
    state.Channel = "development";
    state.ReleaseId = "release-43";
    state.PackageUrl = "https://example.invalid/rc43.zip";
    state.ExpectedSha256 = "synthetic-sha-43";
    state.MarkStage(stage);
    return state;
}

static void TestOfficialJobRecovery()
{
    using TempTree tree = TempTree.Create();
    UpdateStateStore store = new(Path.Combine(tree.Root, "state.json"));
    UpdateState waiting = NewOfficialState("persisted-update-id", UpdateStages.WaitingHealthCheck);
    DateTimeOffset startedAt = waiting.StartedAt;
    Require(store.Save(waiting), "Active job state should persist.");
    string[] jobArgs = OfficialJobArgs(); // JobExecutor does not supply --update-id.
    UpdateState persisted = store.Load()!;
    Require(UpdaterProgram.DecideInvocation(persisted, jobArgs) == UpdaterProgram.InvocationDecision.Resume,
        "The normal JobExecutor invocation must recognize the same persisted execution.");
    string[] runnerArgs = [.. jobArgs, "--runner", "--update-id", persisted.UpdateId];
    UpdateState runnerState = UpdaterProgram.PrepareRunnerState(store, runnerArgs);
    Require(runnerState.UpdateId == waiting.UpdateId && runnerState.StartedAt == startedAt && runnerState.Attempt == 1,
        "Resume must retain update_id, started_at, and attempt.");
    int downloads = 0;
    int? result = UpdaterProgram.DispatchRecovery(runnerState, store, () => "0.1.1.0-rc43",
        () => "Running", () => throw new InvalidOperationException("No service start expected."),
        (state, rollback) => {
            store.TryTransitionNonTerminal(state.UpdateId, current => { current.MarkStage(UpdateStages.Completed); return true; }, out _);
            return UpdaterProgram.WaitForHealthCheckCore(() => store.Load()!, () => "Running", false,
                TimeSpan.FromMilliseconds(30), TimeSpan.FromMilliseconds(2));
        }, _ => { downloads++; return 1; });
    Require(result == 0 && downloads == 0 && store.Load()?.CurrentStage == UpdateStages.Completed,
        "Official-path recovery must confirm health without download or reinstall.");

    Require(UpdaterProgram.DecideInvocation(waiting, OfficialJobArgs(jobId: "JOB-B")) == UpdaterProgram.InvocationDecision.Conflict,
        "A different job must not resume the active update.");
    Require(UpdaterProgram.DecideInvocation(waiting, OfficialJobArgs(target: "0.1.1.0-rc44")) == UpdaterProgram.InvocationDecision.Conflict,
        "Same job with a different target must fail closed.");
    Require(UpdaterProgram.DecideInvocation(waiting, OfficialJobArgs(releaseId: "release-other")) == UpdaterProgram.InvocationDecision.Conflict,
        "Same job with a different release must fail closed.");
    Require(UpdaterProgram.DecideInvocation(waiting, OfficialJobArgs(sha: "different-sha")) == UpdaterProgram.InvocationDecision.Conflict,
        "Same job with a different package hash must fail closed.");
    Require(UpdaterProgram.DecideInvocation(waiting,
        [.. OfficialJobArgs(), "--update-id", "different-update-id"]) == UpdaterProgram.InvocationDecision.Conflict,
        "Explicit update_id must not override the active job identity.");
    string[] manualArgs = OfficialJobArgs();
    manualArgs[Array.IndexOf(manualArgs, "--source") + 1] = "manual";
    Require(UpdaterProgram.DecideInvocation(waiting, manualArgs) == UpdaterProgram.InvocationDecision.Conflict,
        "The job source must not be changed during recovery.");
    Require(ExpectThrows(() => UpdaterProgram.PrepareRunnerState(store,
        [.. OfficialJobArgs(jobId: "JOB-B"), "--runner", "--update-id", waiting.UpdateId])) is InvalidOperationException,
        "Runner entrypoint must reject a mismatched job even with the update_id.");
}

static void TestRollbackStageRecovery()
{
    Require(UpdaterProgram.ClassifyRecovery(NewOfficialState("failed", UpdateStages.Failed))
        == UpdaterProgram.RecoveryAction.Terminal, "Failed must remain terminal.");
    Require(UpdaterProgram.ClassifyRecovery(NewOfficialState("rollback-failed", UpdateStages.RollbackFailed))
        == UpdaterProgram.RecoveryAction.Terminal, "RollbackFailed must remain terminal.");
    foreach (string stage in new[] { UpdateStages.RollbackRequired, UpdateStages.RollbackStarting,
        UpdateStages.RollbackStoppingService, UpdateStages.RollbackRestoringFiles,
        UpdateStages.RollbackStartingService })
    {
        using TempTree tree = TempTree.Create();
        UpdateStateStore store = new(Path.Combine(tree.Root, "state.json"));
        UpdateState state = NewOfficialState("rollback-" + stage, stage);
        state.BackupPath = Path.Combine(tree.Root, "backup");
        state.RollbackAttempt = stage == UpdateStages.RollbackRequired ? 0 : 1;
        Require(store.Save(state), "Rollback state should persist.");
        int rollbackCalls = 0;
        int? result = UpdaterProgram.DispatchRecovery(store.Load()!, store,
            () => throw new InvalidOperationException("Rollback must not check target version."),
            () => "Stopped", () => { }, (_, _) => throw new InvalidOperationException("No early health check."),
            current => {
                rollbackCalls++;
                Require(UpdaterProgram.TryBeginRollback(store, current.UpdateId, out UpdateState? resumed),
                    "Rollback should start or resume at " + stage);
                Require(resumed?.CurrentStage == UpdateStages.RollbackStarting && resumed.RollbackAttempt == 1,
                    "Rollback retry must not increment attempt or return to normal update.");
                return 0;
            });
        Require(result == 0 && rollbackCalls == 1, "Rollback stage must dispatch to rollback: " + stage);
    }

    foreach (string service in new[] { "Stopped", "Running" })
    {
        using TempTree tree = TempTree.Create();
        UpdateStateStore store = new(Path.Combine(tree.Root, "state.json"));
        UpdateState state = NewOfficialState("rollback-wait-" + service, UpdateStages.RollbackWaitingHealthCheck);
        state.RollbackAttempt = 1;
        store.Save(state);
        int starts = 0;
        int? result = UpdaterProgram.DispatchRecovery(store.Load()!, store, () => state.FromVersion,
            () => service, () => starts++, (current, restoring) => {
                Require(restoring, "Rollback wait must expect the previous version.");
                store.TryTransitionNonTerminal(current.UpdateId,
                    persisted => { persisted.MarkStage(UpdateStages.RolledBack); return true; }, out _);
                return UpdaterProgram.WaitForHealthCheckCore(() => store.Load()!, () => "Running", true,
                    TimeSpan.FromMilliseconds(30), TimeSpan.FromMilliseconds(2));
            }, _ => throw new InvalidOperationException("Restore must not run again."));
        Require(result == 0 && starts == (service == "Stopped" ? 1 : 0)
            && store.Load()?.CurrentStage == UpdateStages.RolledBack,
            "Rollback wait must start stopped service and confirm rollback.");
    }
}

static void TestTerminalReplayWithoutStaging()
{
    foreach (string terminal in new[] { UpdateStages.Completed, UpdateStages.RolledBack })
    {
        using TempTree tree = TempTree.Create();
        UpdateStateStore store = new(Path.Combine(tree.Root, "state.json"));
        UpdateState state = NewOfficialState("terminal-" + terminal, terminal);
        Require(store.Save(state), "Terminal state should persist.");
        int trayCalls = 0;
        string absentStaging = Path.Combine(tree.Root, "missing-staging");
        Require(!Directory.Exists(absentStaging), "Test staging must be absent.");
        Require(UpdaterProgram.TryReturnStagedTerminal(store, state.UpdateId, tree.Root,
            _ => { trayCalls++; throw new IOException("Synthetic Tray failure."); }, out int result)
            && result == 0 && trayCalls == 1 && store.Load()?.CurrentStage == terminal,
            "Terminal apply-staged replay must ignore staging and keep Tray best-effort.");
    }
}

static void TestCrashAfterRollbackRestore()
{
    using TempTree tree = TempTree.Create();
    string install = tree.CreateDirectory("install");
    string backup = Path.Combine(tree.Root, "backup");
    UpdateStateStore store = new(Path.Combine(tree.Root, "state.json"));
    UpdateState state = NewOfficialState("crash-after-restore", UpdateStages.RollbackStartingService);
    state.BackupPath = backup;
    state.RollbackAttempt = 1;
    foreach (string name in new[] { "NightOwl.Agent.Windows.exe", "NightOwl.Agent.Updater.exe",
        "NightOwl.Agent.Tray.exe", "agent.version.json" })
    {
        File.WriteAllText(Path.Combine(install, name), "previous-" + name);
    }
    UpdaterProgram.CreateBackupForTest(install, backup, state.UpdateId, state.FromVersion);
    File.WriteAllText(Path.Combine(install, "NightOwl.Agent.Windows.exe"), "target");
    UpdaterProgram.RestoreManagedFilesForTest(install, backup, state.UpdateId, state.FromVersion);
    store.Save(state); // Crash after restore, before StartService.
    int? dispatch = UpdaterProgram.DispatchRecovery(store.Load()!, store,
        () => throw new InvalidOperationException("Never enter CheckingVersion."),
        () => "Stopped", () => { }, (_, _) => throw new InvalidOperationException("Not waiting yet."),
        current => {
            Require(UpdaterProgram.TryBeginRollback(store, current.UpdateId, out _), "Interrupted restore must resume.");
            UpdaterProgram.RestoreManagedFilesForTest(install, backup, current.UpdateId, current.FromVersion);
            store.TryTransitionNonTerminal(current.UpdateId,
                persisted => { persisted.MarkStage(UpdateStages.RollbackWaitingHealthCheck); return true; }, out _);
            return 0;
        });
    Require(dispatch == 0 && File.ReadAllText(Path.Combine(install, "NightOwl.Agent.Windows.exe"))
        == "previous-NightOwl.Agent.Windows.exe", "Repeated validated restore must retain previous files.");
    int starts = 0;
    int? health = UpdaterProgram.DispatchRecovery(store.Load()!, store, () => state.FromVersion,
        () => "Stopped", () => starts++, (current, restoring) => {
            store.TryTransitionNonTerminal(current.UpdateId,
                persisted => { persisted.MarkStage(UpdateStages.RolledBack); return true; }, out _);
            return UpdaterProgram.WaitForHealthCheckCore(() => store.Load()!, () => "Running", restoring,
                TimeSpan.FromMilliseconds(30), TimeSpan.FromMilliseconds(2));
        }, _ => throw new InvalidOperationException("No second file restore expected."));
    Require(health == 0 && starts == 1 && store.Load()?.CurrentStage == UpdateStages.RolledBack,
        "Crash after restore must start the service and finish rollback.");
    UpdaterProgram.ValidateBackupForTest(backup, state.UpdateId, state.FromVersion);
}

static void TestTrayLifecycleSourceMarkers()
{
    string repo = FindRepoRoot();
    string sourcePath = Path.Combine(repo, "NightOwl.Agent.Updater", "Program.cs");
    string source = File.ReadAllText(sourcePath);
    Require(source.Contains("EnsureTrayLifecycleAfterUpdate(installPath)", StringComparison.Ordinal), "Updater should validate Tray lifecycle after update and rollback.");
    Require(source.Contains("Start-ScheduledTask -TaskName 'NightOwl Agent Tray'", StringComparison.Ordinal), "Updater should start Tray through the scheduled task bridge.");
    Require(source.Contains("tray.task.created", StringComparison.Ordinal), "Updater should log Tray task creation.");
    Require(source.Contains("tray.task.validated", StringComparison.Ordinal), "Updater should log Tray task validation.");
    Require(source.Contains("tray.shortcut.created", StringComparison.Ordinal), "Updater should create Start Menu shortcut.");
    Require(source.Contains("tray.shortcut.repaired", StringComparison.Ordinal), "Updater should repair Start Menu shortcut.");
    Require(source.Contains("tray.start.deferred", StringComparison.Ordinal), "Updater should defer Tray start when no interactive session exists.");
    Require(source.Contains("no_interactive_session", StringComparison.Ordinal), "Updater should distinguish no interactive session from failure.");
    Require(!source.Contains("FileName = tray", StringComparison.Ordinal), "Updater must not launch Tray UI directly from service/session 0.");
}

static string FindRepoRoot()
{
    DirectoryInfo? current = new(Environment.CurrentDirectory);
    while (current is not null)
    {
        if (File.Exists(Path.Combine(current.FullName, "NightOwl.Agent.Updater", "Program.cs")))
        {
            return current.FullName;
        }
        current = current.Parent;
    }
    throw new InvalidOperationException("Repository root not found.");
}

static Exception ExpectThrows(Action action)
{
    try
    {
        action();
    }
    catch (Exception ex)
    {
        return ex;
    }
    throw new InvalidOperationException("Expected action to throw.");
}

internal sealed class TempTree : IDisposable
{
    public string Root { get; }

    private TempTree(string root)
    {
        Root = root;
    }

    public static TempTree Create()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-updater-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return new TempTree(root);
    }

    public string CreateDirectory(string name)
    {
        string path = Path.Combine(Root, name);
        Directory.CreateDirectory(path);
        return path;
    }

    public void Dispose()
    {
        try { Directory.Delete(Root, recursive: true); } catch { }
    }
}
