using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class ApprovedScriptTests
{
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private static AgentJobRequest Job(object? reference = null) => new()
    {
        Id = Guid.NewGuid().ToString(), Type = "operational_action", TimeoutSeconds = 5,
        Payload = new() { ["action_id"] = "run_approved_script", ["action_version"] = 1,
            ["parameters"] = reference ?? new { script_id = ApprovedScripts.Id, script_version = 1,
                sha256 = ApprovedScripts.Sha256, parameters = new { } } }
    };

    internal static async Task RunAsync()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-approved-script-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            AgentConfig config = new() { MachineId = Guid.NewGuid().ToString(), JobsPath = Path.Combine(root, "jobs"),
                StatePath = Path.Combine(root, "state.json"), PendingResultsPath = Path.Combine(root, "results"),
                LogPath = Path.Combine(root, "log.jsonl"), OperationalActionsEnabled = true };
            config.AllowedJobTypes.Add("operational_action");
            config.AllowedOperationalActions.Add("run_approved_script");
            JobExecutionPolicy policy = new(new JobStore(config.JobsPath));
            Check(!policy.Prepare(config, Job()).ShouldExecute && !OperationalActions.Capabilities(config).Any(), "Default script gate open");
            config.ApprovedScriptsEnabled = true;
            Check(!policy.Prepare(config, Job()).ShouldExecute, "Unapproved local version ran");
            config.AllowedScriptVersions.Add("environment_diagnostic:1");
            Check(OperationalActions.Capabilities(config).Contains(ApprovedScripts.Capability), "Pinned capability missing");
            var good = new Dictionary<string, object?> { ["script_id"] = ApprovedScripts.Id,
                ["script_version"] = 1, ["sha256"] = ApprovedScripts.Sha256, ["parameters"] = new { } };
            foreach (var (field, value) in new (string, object?)[] { ("script_id", "unknown"), ("script_version", 2),
                ("script_version", true), ("script_version", "1"), ("sha256", new string('0', 64)),
                ("parameters", new { command = "whoami" }), ("content", "whoami"), ("runtime", "cmd.exe"), ("path", "C:\\anything.ps1") })
            {
                var bad = new Dictionary<string, object?>(good) { [field] = value };
                Check(!policy.Prepare(config, Job(bad)).ShouldExecute, "Untrusted script reference accepted");
            }
            string content = ApprovedScripts.Content();
            Check(Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(content))).ToLowerInvariant() == ApprovedScripts.Sha256,
                "Actual embedded bytes mismatch");
            await ProcessLimits();
            JsonlLogger logger = new(config.LogPath);
            JobExecutor executor = new(new WindowsInventoryCollector(logger, _ => throw new Exception("No inventory")), logger, policy);
            var job = Job();
            JobExecutionResult result = await executor.ExecuteAsync(config, job, CancellationToken.None);
            Check(result.Status == "completed" && result.ExitCode == 0 && result.Stderr == "" && result.Stdout.Contains("powershell_version"),
                "Actual diagnostic script failed: " + JsonSerializer.Serialize(result.Result));
            using var output = JsonDocument.Parse(JsonSerializer.Serialize(result.Result));
            Check(output.RootElement.GetProperty("output").GetProperty("sha256").GetString() == ApprovedScripts.Sha256,
                "Execution provenance lost");
            Check(result.DurationSeconds >= 0 && policy.Store.Load(job.Id)!.Status == "completed", "Result not persisted");
            Check((await executor.ExecuteAsync(config, job, CancellationToken.None)).Status == "duplicate", "Duplicate script executed");
            config.AllowedScriptVersions.Clear();
            Check(!policy.Prepare(config, Job()).ShouldExecute && !OperationalActions.Capabilities(config).Any(), "Local revocation ignored");
            Console.WriteLine("PASS approved scripts: pinned embedded bytes, explicit gates/schema, actual PS5.1 diagnostic, persistence/duplicate, revocation, cancellation/timeout, bounded output and child denial.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static async Task ProcessLimits()
    {
        string runtime = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
        string[] args = { "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-" };
        string? previous = Environment.GetEnvironmentVariable("NIGHTOWL_SCRIPT_TEST_SECRET");
        try
        {
            Environment.SetEnvironmentVariable("NIGHTOWL_SCRIPT_TEST_SECRET", "synthetic-never-output");
            var clean = await OwnedProcess.CaptureAsync(runtime, args,
                "if($env:NIGHTOWL_SCRIPT_TEST_SECRET){exit 9}; [Console]::WriteLine('ENVIRONMENT_CLEAN')\n", 1024, CancellationToken.None);
            Check(clean.ExitCode == 0 && clean.Stdout.Contains("ENVIRONMENT_CLEAN"), "Parent secret environment inherited");
        }
        finally { Environment.SetEnvironmentVariable("NIGHTOWL_SCRIPT_TEST_SECRET", previous); }
        // Synthetic helper inputs are test-only, never exposed by the approved script API.
        var large = await OwnedProcess.CaptureAsync(runtime, args, "[Console]::Write(('x' * 100000))\n", 1024, CancellationToken.None);
        Check(large.OutputLimitReached && Encoding.UTF8.GetByteCount(large.Stdout + large.Stderr) <= 1024, "Output not bounded during read");
        var failure = await OwnedProcess.CaptureAsync(runtime, args, "[Console]::Error.WriteLine('synthetic-error'); exit 7\n", 1024, CancellationToken.None);
        Check(failure.ExitCode == 7 && failure.Stderr.Contains("synthetic-error"), "Exit/stderr not captured");
        foreach (var capture in new[] { new OwnedProcess.Capture(7, "synthetic-private-output", "synthetic-private-error", false),
            new OwnedProcess.Capture(0, "{\"secret\":\"synthetic-private-output\"}", "", false),
            new OwnedProcess.Capture(1, "synthetic-private-output", "", true) })
        {
            try { ApprovedScripts.Confirm(capture); throw new Exception("Invalid result accepted"); }
            catch (ScriptExecutionFailure error)
            {
                Check(error.ExitCode == capture.ExitCode && error.Message.StartsWith("SCRIPT_") && !error.ToString().Contains("synthetic-private"),
                    "Exit code lost or private output leaked");
            }
        }
        foreach (bool timeout in new[] { false, true })
        {
            using CancellationTokenSource stop = new();
            var pending = OwnedProcess.CaptureAsync(runtime, args, "while ($true) { Start-Sleep -Milliseconds 50 }\n\n", 1024, stop.Token);
            if (timeout) stop.CancelAfter(700); else { await Task.Delay(700); stop.Cancel(); }
            Stopwatch elapsed = Stopwatch.StartNew();
            try { await pending; throw new Exception("Cancelled process returned success"); }
            catch (OperationCanceledException) { }
            Check(elapsed.Elapsed < TimeSpan.FromSeconds(10), "Process cancellation did not complete");
        }
        var child = await OwnedProcess.CaptureAsync(runtime, args,
            "$ErrorActionPreference='Stop'; try { $p=[Diagnostics.Process]::Start($PSHOME+'\\powershell.exe','-NoProfile -NonInteractive -Command exit'); if($null -ne $p){ $p.WaitForExit(); [Console]::WriteLine('CHILD_RAN') } } catch { [Console]::WriteLine('CHILD_DENIED') }\n\n",
            4096, CancellationToken.None);
        Check(child.Stdout.Contains("CHILD_DENIED") && !child.Stdout.Contains("CHILD_RAN"), "Single-process limit not enforced");
    }
}
