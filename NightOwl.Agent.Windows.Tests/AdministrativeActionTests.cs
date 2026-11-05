using System.Net;
using System.ServiceProcess;
using System.Text;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class AdministrativeActionTests
{
    private static void Check(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static AgentJobRequest Job(string action = "start_service", object? parameters = null) => new()
    {
        Id = Guid.NewGuid().ToString(), Type = "operational_action", TimeoutSeconds = 5,
        Payload = new() { ["action_id"] = action, ["action_version"] = 1,
            ["parameters"] = parameters ?? (AdministrativeActions.IsService(action) ? new { service_name = "AcmePilotService" } : new { }) }
    };
    private static AgentConfig Config(string root)
    {
        AgentConfig config = new() { AgentToken = "synthetic-only", MachineId = Guid.NewGuid().ToString(),
            ServerBaseUrl = "https://isolated.invalid", JobsPullUrl = "https://isolated.invalid/pull/", JobsResultUrl = "https://isolated.invalid/result/",
            JobsPath = Path.Combine(root, "jobs"), PendingResultsPath = Path.Combine(root, "results"),
            StatePath = Path.Combine(root, "state.json"), LogPath = Path.Combine(root, "log.jsonl"), OperationalActionsEnabled = true };
        config.AllowedJobTypes.Add("operational_action");
        config.AllowedOperationalActions.AddRange(new[] { "start_service", "stop_service", "restart_service", "gpupdate",
            "create_local_user", "set_local_user_enabled", "reset_local_password" });
        return config;
    }

    internal static async Task RunAsync()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-admin-actions-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            AgentConfig config = Config(root);
            JobExecutionPolicy policy = new(new JobStore(config.JobsPath));
            Check(!policy.Prepare(config, Job()).ShouldExecute && !OperationalActions.Capabilities(config).Any(), "Administrative default is enabled");
            config.AdministrativeActionsEnabled = true;
            Check(!policy.Prepare(config, Job()).ShouldExecute && !policy.Prepare(config, Job("gpupdate")).ShouldExecute, "Resource allowlist bypass");
            config.AllowedServiceOperations["AcmePilotService"] = new() { "start_service", "stop_service", "restart_service" };
            config.AllowComputerPolicyRefresh = true;
            Check(OperationalActions.Capabilities(config).Count() == 4, "Blocked accounts advertised");
            foreach (string name in new[] { "WinDefend", "EventLog", "RpcSs", "WinRM", "SamSs", "gpsvc", "NightOwlAgentDotNet", "svc;whoami", "../svc", "*" })
            {
                config.AllowedServiceOperations[name] = new() { "stop_service" };
                Check(!policy.Prepare(config, Job("stop_service", new { service_name = name })).ShouldExecute, "Protected service allowed");
            }
            Check(!policy.Prepare(config, Job("gpupdate", new { command = "whoami" })).ShouldExecute, "Command accepted");
            string sid = "S-1-5-21-1-2-3-1001", secret = Guid.NewGuid().ToString();
            foreach (var account in new[] { Job("create_local_user", new { user_name = "nw_help", secret_reference = secret }),
                Job("set_local_user_enabled", new { user_name = "nw_help", account_sid = sid, enabled = true }),
                Job("reset_local_password", new { user_name = "nw_help", account_sid = sid, secret_reference = secret }),
                Job("create_local_user", new { user_name = "nw_help", password = "synthetic-private-password" }) })
                Check(!policy.Prepare(config, account).ShouldExecute, "Blocked account reached execution");
            foreach (string name in new[] { "Administrator", "Administrador", "Guest", "CONTROL\\someone", "someone@control.local", "krbtgt", "system" })
                Check(!policy.Prepare(config, Job("create_local_user", new { user_name = name, secret_reference = secret })).ShouldExecute, "Protected/domain account allowed");
            foreach (string protectedSid in new[] { "S-1-5-18", "S-1-5-32-544", "S-1-5-21-1-2-3-500", "S-1-5-21-1-2-3-501" })
                Check(!policy.Prepare(config, Job("set_local_user_enabled", new { user_name = "nw_help", account_sid = protectedSid, enabled = false })).ShouldExecute, "Protected SID allowed");
            ResourceGuards(root);
            Native native = new();
            var actions = new AdministrativeActions(native);
            JsonlLogger logger = new(config.LogPath);
            JobExecutor executor = new(new WindowsInventoryCollector(logger, _ => throw new Exception("No inventory")), logger, policy,
                operationalActions: new OperationalActions(administrative: actions));
            foreach (string action in new[] { "start_service", "stop_service", "restart_service", "gpupdate" })
            {
                var job = Job(action);
                var result = await executor.ExecuteAsync(config, job, CancellationToken.None);
                Check(result.Status == "completed" && result.ExitCode == 0, "Mock administrative execution failed");
                if (action == "gpupdate") Check(JsonSerializer.Serialize(result.Result).Contains("\"processing_confirmed\":false"), "Request reported as applied");
                int calls = native.Calls;
                Check((await executor.ExecuteAsync(config, job, CancellationToken.None)).Status == "duplicate" && native.Calls == calls, "Administrative replay executed");
            }
            native.IsAdministrator = false;
            Check((await executor.ExecuteAsync(config, Job(), CancellationToken.None)).Stderr.Contains("action_admin_required"), "Privilege check missing");
            native.IsAdministrator = true; native.Throw = true;
            var failure = await executor.ExecuteAsync(config, Job(), CancellationToken.None);
            Check(failure.Status == "failed" && !JsonSerializer.Serialize(failure).Contains("synthetic-private"), "Native details leaked");
            native.Throw = false;
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            int before = native.Calls;
            try { actions.Execute(config, Job(), cancellation.Token); throw new Exception("Cancellation ignored"); }
            catch (OperationCanceledException) { }
            Check(native.Calls == before, "Cancelled action reached native layer");
            using (var during = new CancellationTokenSource())
            {
                native.ServiceBlock = true;
                Task pending = Task.Run(() => actions.Execute(config, Job(), during.Token));
                await Task.Delay(100); during.Cancel();
                try { await pending; throw new Exception("Active cancellation ignored"); }
                catch (OperationCanceledException) { }
                native.ServiceBlock = false;
            }
            using var release = new ManualResetEventSlim();
            native.Block = release;
            Task<JobExecutionResult> timedOut = executor.ExecuteAsync(config, Job("gpupdate"), CancellationToken.None);
            try { await Task.Delay(5300); Check(!timedOut.IsCompleted, "Native ownership released prematurely"); }
            finally { release.Set(); }
            Check((await timedOut).Status == "timed_out", "Timeout fabricated success");
            native.Block = null;
            var interrupted = Job("restart_service");
            policy.Prepare(config, interrupted); policy.MarkRunning(interrupted);
            await new JobExecutionCoordinator(policy, logger).RecoverInterruptedJobsAsync(config, new(config.PendingResultsPath), CancellationToken.None,
                Path.Combine(root, "no-update.json"));
            Check(!policy.Prepare(config, interrupted).ShouldExecute, "Unknown mutation repeated automatically");
            Check(JobExecutionCoordinator.GetCategory("operational_action") == JobCategories.Exclusive &&
                JobExecutionCoordinator.IsCritical("operational_action"), "Ownership/critical retention weakened");
            await RejectBeforeJournal(Path.Combine(root, "admission"));
            Console.WriteLine("PASS administrative actions: mocked service controls/policy request, protected targets, account hard blocks, privilege, timeout ownership, cancellation, idempotency/recovery, pre-journal credential rejection. NO real mutations.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static void ResourceGuards(string root)
    {
        string exe = Path.Combine(root, "synthetic.exe");
        File.WriteAllText(exe, "synthetic-not-executed");
        WindowsAdministrativeActionNative.ValidateResource(ServiceType.Win32OwnProcess, 0, '"' + exe + '"');
        foreach (var invalid in new[] { (ServiceType.KernelDriver, 0u, exe), (ServiceType.Win32ShareProcess, 0u, exe),
            (ServiceType.Win32OwnProcess, 1u, exe), (ServiceType.Win32OwnProcess, 0u, exe + " --anything"),
            (ServiceType.Win32OwnProcess, 0u, Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "not-used.exe")) })
        {
            try { WindowsAdministrativeActionNative.ValidateResource(invalid.Item1, invalid.Item2, invalid.Item3); throw new Exception("Unsafe service metadata accepted"); }
            catch (InvalidOperationException) { }
        }
    }

    private static async Task RejectBeforeJournal(string root)
    {
        Directory.CreateDirectory(root);
        AgentConfig config = Config(root);
        config.AdministrativeActionsEnabled = true;
        AgentJobRequest job = Job("reset_local_password", new { user_name = "nw_help", password = "synthetic-admission-private-secret" });
        bool delivered = false, ack = false;
        using var stop = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        var api = new AgentApiClient(new Factory(async request =>
        {
            if (request.RequestUri!.AbsolutePath.EndsWith("signal/")) return Json(new { protocol = 2, available = !delivered, poll_seconds = 2 });
            if (request.RequestUri.AbsolutePath.EndsWith("pull/")) { delivered = true; return Json(new { jobs = new[] { job } }); }
            string body = await request.Content!.ReadAsStringAsync();
            Check(!body.Contains("synthetic-admission-private-secret"), "Credential in result");
            return Json(new { status = "ok", job_id = job.Id, result_id = request.Headers.GetValues("Idempotency-Key").Single() });
        }));
        var logger = new JsonlLogger(config.LogPath);
        using var heavy = new SemaphoreSlim(1, 1);
        var runtime = new JobRuntime(api, new(new(new JobStore(config.JobsPath)), logger),
            (_, _, _) => throw new Exception("Rejected action executed"), new(config.PendingResultsPath), logger, heavy);
        runtime.Observe = (stage, _, _) => { if (stage == "result_ack") ack = true; };
        Task running = runtime.RunAsync(config, stop.Token);
        try { while (!ack) await Task.Delay(50, stop.Token); }
        finally { stop.Cancel(); try { await running; } catch (OperationCanceledException) { } }
        foreach (string file in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
            Check(!File.ReadAllText(file).Contains("synthetic-admission-private-secret"), "Credential persisted before rejection");
    }

    private sealed class Native : IAdministrativeActionNative
    {
        public bool IsAdministrator { get; set; } = true;
        public int Calls;
        public bool Throw;
        public bool ServiceBlock;
        public ManualResetEventSlim? Block;
        public ServiceControlOutcome ControlService(string name, string action, CancellationToken ct)
        { Calls++; if (Throw) throw new Exception("synthetic-private-native-error"); if (ServiceBlock) { ct.WaitHandle.WaitOne(); ct.ThrowIfCancellationRequested(); } return new(action == "stop_service" ? "Stopped" : "Running", true); }
        public bool RefreshComputerPolicy() { Calls++; Block?.Wait(); return true; }
    }
    private static HttpResponseMessage Json(object value) => new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(value, new JsonSerializerOptions(JsonSerializerDefaults.Web)), Encoding.UTF8, "application/json") };
    private sealed class Factory(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler) : IHttpClientFactory
    { public HttpClient CreateClient(string name) => new(new Handler(handler)); }
    private sealed class Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    { protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) => handler(request); }
}
