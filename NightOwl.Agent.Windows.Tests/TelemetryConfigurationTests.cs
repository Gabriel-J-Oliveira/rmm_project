using System.Text.Json;
using System.Security.AccessControl;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class TelemetryConfigurationTests
{
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web);
    private static void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
    internal static async Task RunAsync()
    {
        var requested = new TelemetrySettings(true, 300, 900);
        Check(TelemetrySettings.Parse(Payload(requested)) == requested, "Strict payload round trip.");
        foreach (var invalid in new[] {
            new Dictionary<string, object?> { ["telemetryEnabled"] = "true", ["telemetrySampleSeconds"] = 300, ["telemetryFlushSeconds"] = 900 },
            new Dictionary<string, object?> { ["telemetryEnabled"] = true, ["telemetrySampleSeconds"] = 59, ["telemetryFlushSeconds"] = 900 },
            new Dictionary<string, object?> { ["telemetryEnabled"] = true, ["telemetrySampleSeconds"] = 300, ["telemetryFlushSeconds"] = 86401 },
            new Dictionary<string, object?> { ["telemetryEnabled"] = true, ["agentToken"] = "sentinel" } })
        {
            bool rejected = false;
            try { TelemetrySettings.Parse(invalid); } catch (InvalidOperationException) { rejected = true; }
            Check(rejected, "Invalid payload accepted.");
        }
        string root = Path.Combine(Path.GetTempPath(), "nightowl-telemetry-config-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string path = Path.Combine(root, "config.json");
            string journalPath = Path.Combine(root, "journal.json");
            var config = new AgentConfig { AgentToken = "identity-secret-sentinel", MachineId = Guid.NewGuid().ToString(),
                TelemetryEnabled = false, TelemetrySampleSeconds = 600, TelemetryFlushSeconds = 3600 };
            File.WriteAllText(path, JsonSerializer.Serialize(config, Options).TrimEnd('}') + ",\"futureProperty\":42}");
            var acl = new FileInfo(path).GetAccessControl();
            acl.SetAccessRuleProtection(true, true);
            new FileInfo(path).SetAccessControl(acl);
            string originalAcl = new FileInfo(path).GetAccessControl().GetSecurityDescriptorSddlForm(AccessControlSections.Access);
            string bufferPath = Path.Combine(root, "telemetry-buffer.json");
            File.WriteAllText(bufferPath, "buffer-sentinel");
            string? appliedConfig = null;
            int active = 0, peak = 0, reloads = 0;
            bool failNext = false;
            var runtime = new TelemetryRuntime(async (current, ct, ready) =>
            {
                reloads++;
                if (failNext) { failNext = false; throw new IOException("synthetic failure"); }
                active++;
                peak = Math.Max(peak, active);
                appliedConfig = JsonSerializer.Serialize(TelemetrySettings.FromConfig(current));
                ready();
                try { await Task.Delay(Timeout.Infinite, ct); }
                catch (OperationCanceledException) { }
                finally { active--; }
            });
            using var lifetime = new CancellationTokenSource();
            await runtime.StartAsync(config, lifetime.Token);
            var queue = new PendingResultQueue(Path.Combine(root, "results"));
            var service = new TelemetryConfigurationService(runtime, queue, path, journalPath);
            var job = new AgentJobRequest { Id = Guid.NewGuid().ToString(), Type = "configure_telemetry", Payload = Payload(requested) };
            var policy = new JobExecutionPolicy(new JobStore(Path.Combine(root, "jobs")));
            Check(policy.Prepare(config, job).ShouldExecute, "Configuration job must be allowlisted.");
            var result = await service.ConfigureAsync(config, job, CancellationToken.None);
            Check(result.Status == "completed" && runtime.Effective == requested, "Completion requires effective reload.");
            Check(appliedConfig == JsonSerializer.Serialize(requested), "Runtime did not observe new settings.");
            Check(peak == 1 && active == 1, "Pipelines overlapped.");
            var persisted = JsonDocument.Parse(File.ReadAllText(path)).RootElement;
            Check(persisted.GetProperty("agentToken").GetString() == config.AgentToken, "Token changed.");
            Check(persisted.GetProperty("machineId").GetString() == config.MachineId, "Identity changed.");
            Check(persisted.GetProperty("futureProperty").GetInt32() == 42, "Unknown config property lost.");
            Check(new FileInfo(path).GetAccessControl().GetSecurityDescriptorSddlForm(AccessControlSections.Access) == originalAcl, "Config ACL changed.");
            Check(File.ReadAllText(bufferPath) == "buffer-sentinel", "Buffer changed.");
            Check(queue.LoadAll().Count == 1 && queue.LoadAll()[0].Critical, "Result must be durable and critical.");
            Check(!File.ReadAllText(Directory.GetFiles(queue.DirectoryPath, "*.json")[0]).Contains(config.AgentToken), "Secret in result.");
            policy.MarkFinal(config, job, result);
            Check(!policy.Prepare(config, job).ShouldExecute, "Duplicate job re-executed.");

            // Crash after config persistence but before readiness/result acknowledgement.
            var recoveredJob = new AgentJobRequest { Id = Guid.NewGuid().ToString(), Type = "configure_telemetry", Payload = Payload(new(true, 120, 600)) };
            var journal = new TelemetryConfigurationJournal(recoveredJob.Id, config.MachineId, DateTimeOffset.UtcNow,
                requested, new(true, 120, 600), null);
            File.WriteAllText(journalPath, JsonSerializer.Serialize(journal, Options));
            await service.RecoverAsync(config, CancellationToken.None);
            Check(runtime.Effective == journal.Requested && !File.Exists(journalPath), "Restart recovery failed.");
            int before = reloads;
            await service.RecoverAsync(config, CancellationToken.None);
            Check(reloads == before, "Recovery is not idempotent.");

            // Readiness failure restores both persisted and effective parameters.
            var previous = TelemetrySettings.FromConfig(config);
            failNext = true;
            var failedJob = new AgentJobRequest { Id = Guid.NewGuid().ToString(), Type = "configure_telemetry", Payload = Payload(requested) };
            var failed = await service.ConfigureAsync(config, failedJob, CancellationToken.None);
            Check(failed.Status == "failed" && runtime.Effective == previous, "Rollback did not restore runtime.");
            persisted = JsonDocument.Parse(File.ReadAllText(path)).RootElement;
            Check(persisted.GetProperty("telemetrySampleSeconds").GetInt32() == previous.SampleSeconds, "Rollback did not restore file.");
            Check(File.ReadAllText(bufferPath) == "buffer-sentinel", "Rollback lost buffer.");
            var off = new AgentJobRequest { Id = Guid.NewGuid().ToString(), Type = "configure_telemetry", Payload = Payload(new(false, 300, 900)) };
            Check((await service.ConfigureAsync(config, off, CancellationToken.None)).Status == "completed", "Disable failed.");
            var on = new AgentJobRequest { Id = Guid.NewGuid().ToString(), Type = "configure_telemetry", Payload = Payload(requested) };
            Check((await service.ConfigureAsync(config, on, CancellationToken.None)).Status == "completed" && active == 1, "Re-enable failed.");
            File.SetAttributes(path, FileAttributes.ReadOnly);
            bool persistenceFailed = false;
            try { ConfigService.PersistTelemetry(path, new(false, 600, 3600)); }
            catch (UnauthorizedAccessException) { persistenceFailed = true; }
            finally { File.SetAttributes(path, FileAttributes.Normal); }
            Check(persistenceFailed && JsonDocument.Parse(File.ReadAllText(path)).RootElement.GetProperty("telemetryEnabled").GetBoolean(),
                "Atomic persistence failure changed existing config.");
            var coordinator = new JobExecutionCoordinator(policy, new JsonlLogger(Path.Combine(root, "agent.jsonl")));
            Check((await coordinator.TryStartAsync(config, job, CancellationToken.None)).CanStart, "Exclusive start failed.");
            Check(!(await coordinator.TryStartAsync(config, recoveredJob, CancellationToken.None)).CanStart, "Concurrent configuration allowed.");
            coordinator.Release(job);
            var blocked = new AgentConfig();
            blocked.AllowedJobTypes.Remove("configure_telemetry");
            Check(!policy.Prepare(blocked, new AgentJobRequest { Id = Guid.NewGuid().ToString(), Type = "configure_telemetry", Payload = Payload(requested) }).ShouldExecute,
                "Local allowlist ignored.");
            await runtime.StopAsync();
            Console.WriteLine("Telemetry configuration tests passed: validation, atomic persistence, runtime reload, rollback, recovery, identity, buffer, exclusivity, idempotency.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }
    private static Dictionary<string, object?> Payload(TelemetrySettings settings) => new()
    {
        ["telemetryEnabled"] = settings.Enabled, ["telemetrySampleSeconds"] = settings.SampleSeconds,
        ["telemetryFlushSeconds"] = settings.FlushSeconds
    };
}
