using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Jobs;
using System.Diagnostics;

try
{
    TestIsolatedUninstallerRunnerRemovesBinariesAndPreservesPersistentData();

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
        Require(Directory.GetFiles(Path.Combine(rootPath, "Diagnostics"), "uninstall-job-*.json").Length == 1, "Uninstaller should write diagnostics report.");
    }
    finally
    {
        try { Directory.Delete(dir, recursive: true); } catch { }
    }
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
