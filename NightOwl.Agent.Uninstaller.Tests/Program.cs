using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Jobs;
using System.Diagnostics;

try
{
    TestIsolatedUninstallerRunnerRemovesBinariesAndPreservesPersistentData();
    TestBinaryRemoveRetriesAfterUnauthorizedAccess();
    TestBinaryRemoveRetriesAfterIOException();
    TestBinaryRemovePersistentFailureUsesSpecificError();
    TestUninstallerSourceMarkers();

    string dir = Path.Combine(Path.GetTempPath(), "NightOwlUninstallerTests", Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(dir);
    try
    {
        string path = Path.Combine(dir, "receipt.json");
        NightOwlFileStore.WriteAllText(path, "{\"status\":\"started\"}");
        NightOwlFileStore.WriteAllText(path, "{\"status\":\"completed\"}");
        Require(File.ReadAllText(path).Contains("completed", StringComparison.Ordinal), "Safe overwrite should persist final receipt.");
        Require(Directory.GetFiles(dir, "*.tmp").Length == 0, "Safe overwrite should not leave temp files.");
    }
    finally
    {
        try { Directory.Delete(dir, recursive: true); } catch { }
    }

    Console.WriteLine("NightOwl uninstaller tests passed.");
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex);
    Environment.Exit(1);
}

static void TestIsolatedUninstallerRunnerRemovesBinariesAndPreservesPersistentData()
{
    string dir = Path.Combine(Path.GetTempPath(), "NightOwlUninstallerTests", Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(dir);
    try
    {
        string rootPath = Path.Combine(dir, "NightOwl");
        string sourcePayload = Path.Combine(dir, "installed-payload");
        string runnerPath = Path.Combine(dir, "runner");
        string installPath = Path.Combine(rootPath, "AgentDotNet");
        string configDir = Path.Combine(rootPath, "Config");
        Directory.CreateDirectory(sourcePayload);
        Directory.CreateDirectory(installPath);
        Directory.CreateDirectory(configDir);
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Windows.exe"), "installed binary");
        File.WriteAllText(Path.Combine(configDir, "agent.config.json"), "{\"machineId\":\"test-machine\",\"agentToken\":\"\",\"jobsResultUrl\":\"\"}");

        CopyUninstallerBuildOutput(sourcePayload);
        int copied = JobExecutor.CopyUninstallerRunnerPayload(sourcePayload, runnerPath);
        Require(copied > 0, "Runner payload should copy uninstaller build output.");

        string runner = Path.Combine(runnerPath, "NightOwl.Agent.Uninstaller.exe");
        Require(File.Exists(runner), "Isolated runner should contain NightOwl.Agent.Uninstaller.exe.");
        string jobId = Guid.NewGuid().ToString();
        using Process process = Process.Start(new ProcessStartInfo
        {
            FileName = runner,
            Arguments = string.Join(" ", new[]
            {
                "uninstall",
                "--job-id", jobId,
                "--mode", "uninstall",
                "--config-path", Path.Combine(configDir, "agent.config.json"),
                "--root-path", rootPath,
                "--install-path", installPath,
                "--service-name", "NightOwlMissingServiceForUninstallerTest",
                "--quiet",
                "--json-output"
            }.Select(QuoteArg)),
            WorkingDirectory = runnerPath,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        }) ?? throw new InvalidOperationException("Failed to start isolated uninstaller runner.");

        string stdout = process.StandardOutput.ReadToEnd();
        string stderr = process.StandardError.ReadToEnd();
        Require(process.WaitForExit(30000), "Isolated uninstaller runner should exit.");
        Require(process.ExitCode == 0, $"Isolated uninstaller runner should succeed. stdout={stdout} stderr={stderr}");
        Require(!Directory.Exists(installPath), "Uninstall mode should remove binary install path.");
        Require(File.Exists(Path.Combine(configDir, "agent.config.json")), "Uninstall mode should preserve config.");
        Require(File.Exists(Path.Combine(rootPath, "State", "agent.state.json")), "Uninstall mode should mark persistent state.");
        string[] reports = Directory.GetFiles(Path.Combine(rootPath, "Diagnostics"), "uninstall-job-*.json");
        Require(reports.Length == 1, "Uninstaller should write diagnostics report.");
        string report = File.ReadAllText(reports[0]);
        Require(report.Contains("\"status\": \"completed\"", StringComparison.OrdinalIgnoreCase), "Uninstaller should emit completed only after binary removal succeeds.");
        Require(report.Contains("\"binary_removed\": true", StringComparison.OrdinalIgnoreCase), "Completed uninstaller report should confirm binary_removed=true.");
        Require(report.Contains("\"persistent_data_preserved\": true", StringComparison.OrdinalIgnoreCase), "Uninstall mode should preserve persistent data.");
    }
    finally
    {
        try { Directory.Delete(dir, recursive: true); } catch { }
    }
}

static void TestBinaryRemoveRetriesAfterUnauthorizedAccess()
{
    using TempTree tree = TempTree.Create();
    string installPath = tree.CreateDirectory("AgentDotNet");
    File.WriteAllText(Path.Combine(installPath, "Accessibility.dll"), "tray dependency");
    int attempts = 0;

    int completedAttempts = NightOwl.Agent.Uninstaller.Program.RemoveDirectoryWithRetryForTest(
        installPath,
        TimeSpan.FromSeconds(5),
        path =>
        {
            attempts++;
            if (attempts == 1)
            {
                throw new UnauthorizedAccessException("Access to the path 'Accessibility.dll' is denied.");
            }
            Directory.Delete(path, recursive: true);
        });

    Require(completedAttempts == 2, "Binary remove should retry after transient UnauthorizedAccessException.");
    Require(!Directory.Exists(installPath), "Binary remove retry should remove install path after transient UnauthorizedAccessException.");
}

static void TestBinaryRemoveRetriesAfterIOException()
{
    using TempTree tree = TempTree.Create();
    string installPath = tree.CreateDirectory("AgentDotNet");
    File.WriteAllText(Path.Combine(installPath, "Accessibility.dll"), "tray dependency");
    int attempts = 0;
    int waitCalls = 0;

    int completedAttempts = NightOwl.Agent.Uninstaller.Program.RemoveDirectoryWithRetryForTest(
        installPath,
        TimeSpan.FromSeconds(5),
        path =>
        {
            attempts++;
            if (attempts == 1)
            {
                throw new IOException("The process cannot access the file 'Accessibility.dll'.");
            }
            Directory.Delete(path, recursive: true);
        },
        waitForProcesses: () => waitCalls++);

    Require(completedAttempts == 2, "Binary remove should retry after transient IOException.");
    Require(waitCalls >= 1, "Binary remove retry should revalidate NightOwl processes between attempts.");
    Require(!Directory.Exists(installPath), "Binary remove retry should remove install path after transient IOException.");
}

static void TestBinaryRemovePersistentFailureUsesSpecificError()
{
    using TempTree tree = TempTree.Create();
    string installPath = tree.CreateDirectory("AgentDotNet");
    File.WriteAllText(Path.Combine(installPath, "Accessibility.dll"), "tray dependency");

    Exception ex = ExpectThrows(() => NightOwl.Agent.Uninstaller.Program.RemoveDirectoryWithRetryForTest(
        installPath,
        TimeSpan.FromMilliseconds(700),
        _ => throw new UnauthorizedAccessException("Access to the path 'Accessibility.dll' is denied.")));

    Require(ex.Message.Contains("UNINSTALL_BINARY_REMOVE_FAILED", StringComparison.OrdinalIgnoreCase), "Persistent binary removal failure should use UNINSTALL_BINARY_REMOVE_FAILED.");
    Require(Directory.Exists(installPath), "Persistent binary removal failure should not claim success.");
}

static void TestUninstallerSourceMarkers()
{
    string source = FindRepoRootFile("NightOwl.Agent.Uninstaller", "Program.cs");
    string text = File.ReadAllText(source);
    foreach (string marker in new[]
    {
        "uninstall.tray.stop_requested",
        "uninstall.tray.stopped",
        "uninstall.tray.stop_timeout",
        "uninstall.binary_remove.started",
        "uninstall.binary_remove.retry",
        "uninstall.binary_remove.completed",
        "uninstall.binary_remove.failed",
        "WaitForNightOwlProcessesToExit",
        "UNINSTALL_BINARY_REMOVE_FAILED",
        "WriteUninstalledState(rootPath)"
    })
    {
        Require(text.Contains(marker, StringComparison.Ordinal), $"Uninstaller source missing marker: {marker}");
    }

    Require(text.IndexOf("RemoveDirectoryWithRetry(installPath", StringComparison.Ordinal) < text.IndexOf("WriteUninstalledState(rootPath)", StringComparison.Ordinal), "Uninstaller should remove AgentDotNet before writing uninstalled state.");
    Require(text.Contains("process.Kill(entireProcessTree: false)", StringComparison.Ordinal), "Uninstaller should stop only the Tray process, not the process tree containing the uninstaller.");
    Require(!text.Contains("process.Kill(entireProcessTree: true)", StringComparison.Ordinal), "Uninstaller must not kill the Tray process tree because it may contain the uninstaller itself.");
}

static void CopyUninstallerBuildOutput(string destination)
{
    string source = AppContext.BaseDirectory;
    foreach (string file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
    {
        string relative = Path.GetRelativePath(source, file);
        string target = Path.Combine(destination, relative);
        Directory.CreateDirectory(Path.GetDirectoryName(target)!);
        File.Copy(file, target, overwrite: true);
    }
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
    throw new InvalidOperationException("Expected exception was not thrown.");
}

static string FindRepoRootFile(params string[] relativeParts)
{
    DirectoryInfo? dir = new(AppContext.BaseDirectory);
    while (dir is not null)
    {
        string candidate = Path.Combine(new[] { dir.FullName }.Concat(relativeParts).ToArray());
        if (File.Exists(candidate))
        {
            return candidate;
        }
        dir = dir.Parent;
    }
    throw new FileNotFoundException("Could not find repository file.", Path.Combine(relativeParts));
}

static string QuoteArg(string value)
{
    if (value.Length == 0)
    {
        return "\"\"";
    }
    return "\"" + value.Replace("\\", "\\\\", StringComparison.Ordinal).Replace("\"", "\\\"", StringComparison.Ordinal) + "\"";
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
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
        string root = Path.Combine(Path.GetTempPath(), "NightOwlUninstallerTests", Guid.NewGuid().ToString("N"));
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
