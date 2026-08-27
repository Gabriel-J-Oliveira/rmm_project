using UpdaterProgram = NightOwl.Agent.Updater.Program;
using System.Security.Cryptography;
using System.Text;
using System.Diagnostics;
using NightOwl.Agent.Shared;

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

    TestCopyNormal();
    TestTemporaryLockRetry();
    TestPermanentLockTimeout();
    TestUnauthorizedAccess();
    TestProcessQuiesce();
    TestUnrelatedProcessIgnored();
    TestCurrentProcessIsIgnored();
    TestRollbackOriginalErrorPreserved();
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
