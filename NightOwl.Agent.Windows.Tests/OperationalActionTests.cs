using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class OperationalActionTests
{
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private static AgentJobRequest Job(string action = "query_service", object? parameters = null, object? version = null) => new()
    {
        Id = Guid.NewGuid().ToString(), Type = "operational_action", TimeoutSeconds = 5,
        Payload = new() { ["action_id"] = action, ["action_version"] = version ?? 1,
            ["parameters"] = parameters ?? (action == "query_service" ? new { service_name = "EventLog" } : new { }) }
    };

    internal static async Task RunAsync()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-actions-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            AgentConfig config = new() { MachineId = Guid.NewGuid().ToString(), JobsPath = Path.Combine(root, "jobs"),
                StatePath = Path.Combine(root, "state.json"), PendingResultsPath = Path.Combine(root, "results"), LogPath = Path.Combine(root, "log.jsonl") };
            JobExecutionPolicy policy = new(new JobStore(config.JobsPath));
            Check(!policy.Prepare(config, Job()).ShouldExecute, "Disabled job type accepted");
            config.AllowedJobTypes.Add("operational_action");
            Check(!policy.Prepare(config, Job()).ShouldExecute, "Disabled catalog accepted");
            config.OperationalActionsEnabled = true;
            Check(!policy.Prepare(config, Job()).ShouldExecute, "Unapproved action accepted");
            config.AllowedOperationalActions.AddRange(new[] { "flush_dns", "query_service" });
            Check(OperationalActions.Capabilities(config).Count() == 2, "Versioned capabilities missing");
            foreach (var invalid in new[] { Job("unknown"), Job(version: 2), Job(version: true), Job(version: "1"),
                Job(parameters: new { service_name = "EventLog;whoami" }), Job(parameters: new { service_name = "../secret" }),
                Job(parameters: new { service_name = "" }), Job(parameters: new { service_name = new string('x', 257) }),
                Job(parameters: new { service_name = "EventLog", command = "whoami" }), Job("flush_dns", new { script = "whoami" }) })
                Check(!policy.Prepare(config, invalid).ShouldExecute, "Invalid action accepted");
            var extra = Job(); extra.Payload["script"] = "whoami";
            Check(!policy.Prepare(config, extra).ShouldExecute, "Unknown envelope field accepted");
            var bounded = Job(); bounded.TimeoutSeconds = 999;
            Check(policy.Prepare(config, bounded).TimeoutSeconds == 15, "Action duration not capped");

            Native native = new();
            JsonlLogger logger = new(config.LogPath);
            JobExecutor executor = new(new WindowsInventoryCollector(logger, _ => throw new Exception("No inventory")), logger, policy,
                operationalActions: new OperationalActions(native));
            var query = Job();
            var result = await executor.ExecuteAsync(config, query, CancellationToken.None);
            Check(result.Status == "completed" && native.Queries == 1, "Query not executed");
            string output = JsonSerializer.Serialize(result.Result);
            Check(output.Contains("Running") && output.Length < 1024 && !output.Contains("secret"), "Query output contract invalid");
            Check((await executor.ExecuteAsync(config, query, CancellationToken.None)).Status == "duplicate" && native.Queries == 1, "Duplicate reexecuted");
            Check(policy.Store.Load(query.Id)!.Status == "completed", "Final persistence missing");
            var denied = await executor.ExecuteAsync(config, Job("flush_dns"), CancellationToken.None);
            Check(denied.Status == "failed" && native.Flushes == 0 && denied.Stderr.Contains("action_admin_required"), "Admin protection missing");
            native.IsAdministrator = true;
            Check((await executor.ExecuteAsync(config, Job("flush_dns"), CancellationToken.None)).Status == "completed" && native.Flushes == 1, "Flush not dispatched");
            native.Throw = true;
            var failed = await executor.ExecuteAsync(config, Job(), CancellationToken.None);
            Check(failed.Status == "failed" && !JsonSerializer.Serialize(failed).Contains("secret"), "Native details leaked");
            native.Throw = false;
            using var blocked = new ManualResetEventSlim();
            native.Blocked = blocked;
            Task<JobExecutionResult> timeout = executor.ExecuteAsync(config, Job(), CancellationToken.None);
            try
            {
                await Task.Delay(5300);
                Check(!timeout.IsCompleted, "Ownership released while native operation was still active");
            }
            finally { blocked.Set(); }
            Check((await timeout).Status == "timed_out", "Timeout not persisted");
            native.Blocked = null;
            Check(JobExecutionCoordinator.GetCategory("operational_action") == JobCategories.Exclusive &&
                JobExecutionCoordinator.IsCritical("operational_action"), "Operational ownership/result retention weakened");
            var interrupted = Job("flush_dns");
            policy.Prepare(config, interrupted);
            policy.MarkRunning(interrupted);
            var recoveredPolicy = new JobExecutionPolicy(new JobStore(config.JobsPath));
            var pending = new PendingResultQueue(config.PendingResultsPath);
            await new JobExecutionCoordinator(recoveredPolicy, logger).RecoverInterruptedJobsAsync(config, pending,
                CancellationToken.None, Path.Combine(root, "no-update.json"));
            Check(recoveredPolicy.Store.Load(interrupted.Id)!.IsFinal && pending.LoadAll().Any(x => x.JobId == interrupted.Id),
                "Interrupted action was not recovered into durable results");
            Check(!recoveredPolicy.Prepare(config, interrupted).ShouldExecute && native.Flushes == 1,
                "Interrupted mutation could be automatically repeated");
            // Existing jobs retain their policy and execution contract.
            Check(policy.Prepare(config, new() { Id = Guid.NewGuid().ToString(), Type = "ping", Payload = new() { ["target"] = "127.0.0.1" } }).ShouldExecute, "Legacy ping rejected");
            Console.WriteLine("PASS operational actions: catalog/version/schema/authorization/admin, fixed output, duplicate persistence, native error redaction, 15s cap, timeout ownership, restart recovery, critical results, legacy policy.");
        }
        finally { Directory.Delete(root, true); }
    }

    private sealed class Native : IOperationalActionNative
    {
        public bool IsAdministrator { get; set; }
        public int Queries, Flushes;
        public bool Throw;
        public ManualResetEventSlim? Blocked;
        public void FlushDns() { Flushes++; }
        public string QueryService(string name)
        {
            Queries++; Blocked?.Wait();
            if (Throw) throw new Exception("secret native path and account");
            return "Running";
        }
    }
}
