using System.Collections.Concurrent;
using System.Net;
using System.Text;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class LightweightHeartbeatTests
{
    internal static async Task RunAsync()
    {
        PayloadIsLightweight();
        await CollectionOwnershipSurvivesLogicalTimeoutAsync();
        await HeartbeatsContinueDuringBlockedWorkAsync(job: false);
        await HeartbeatsContinueDuringBlockedWorkAsync(job: true);
        Console.WriteLine("Lightweight heartbeat tests passed (payload, gate, inventory isolation, job isolation).");
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private static void PayloadIsLightweight()
    {
        string source = File.ReadAllText(Path.Combine(RepoRoot(), "NightOwl.Agent.Windows", "Collectors", "WindowsInventoryCollector.cs"));
        string body = source[source.IndexOf("public AgentHeartbeatPayload BuildHeartbeat", StringComparison.Ordinal)..
            source.IndexOf("public AgentCollectPayload BuildCollectPayload", StringComparison.Ordinal)];
        foreach (string forbidden in new[] { "GetSystem(", "GetHardware(", "GetNetwork(", "RunPowerShell", "GetHostEntry", "GetHostAddresses", "Environment.UserName", "WindowsIdentity.GetCurrent" })
            Require(!body.Contains(forbidden, StringComparison.Ordinal), "Heartbeat contains forbidden collection: " + forbidden);
        AgentConfig config = new() { MachineId = "synthetic-machine", AgentVersion = "synthetic-version" };
        WindowsInventoryCollector collector = new(new JsonlLogger(Path.Combine(Path.GetTempPath(), "unused-heartbeat-test.log")),
            _ => throw new InvalidOperationException("Inventory must never be called by heartbeat."));
        AgentHeartbeatPayload payload = collector.BuildHeartbeat(config);
        using JsonDocument json = JsonDocument.Parse(JsonSerializer.Serialize(payload));
        foreach (string absent in new[] { "hardware", "os", "os_name", "os_version", "windows_build", "logged_user", "username" })
            Require(!json.RootElement.TryGetProperty(absent, out _), "Unknown inventory field must be omitted: " + absent);
        Require(payload.MachineId == config.MachineId && payload.AgentVersion == config.AgentVersion,
            "Heartbeat lost identity/version.");
        Require(payload.Hostname == Environment.MachineName && payload.UptimeSeconds >= 0,
            "Heartbeat lost native identity/uptime.");
        Require(payload.AgentMode == "dotnet-service" && payload.Agent.ContainsKey("service_name"),
            "Heartbeat lost operational metadata.");
    }

    private static async Task CollectionOwnershipSurvivesLogicalTimeoutAsync()
    {
        using ManualResetEventSlim release = new();
        using ManualResetEventSlim entered = new();
        int active = 0, peak = 0, calls = 0;
        WindowsInventoryCollector collector = new(new JsonlLogger(Path.Combine(Path.GetTempPath(), "unused-heartbeat-test.log")), _ =>
        {
            Interlocked.Increment(ref calls);
            int count = Interlocked.Increment(ref active);
            peak = Math.Max(peak, count);
            entered.Set();
            release.Wait();
            Interlocked.Decrement(ref active);
            return new AgentCollectPayload();
        });
        Task first = Task.Run(() => collector.BuildCollectPayload(new()));
        try
        {
            Require(entered.Wait(TimeSpan.FromSeconds(5)), "Collection did not start.");
            try { await first.WaitAsync(TimeSpan.FromMilliseconds(100)); throw new Exception("Expected logical timeout."); }
            catch (TimeoutException) { }
            Task second = Task.Run(() => collector.BuildCollectPayload(new()));
            try { await second.WaitAsync(TimeSpan.FromSeconds(2)); throw new Exception("Busy collection must be rejected."); }
            catch (InvalidOperationException error) when (error.Message == JobErrorCodes.JobConcurrencyLimit) { }
            Require(calls == 1 && active == 1, "A logical timeout released a running collection's gate.");
        }
        finally
        {
            release.Set();
            await first.WaitAsync(TimeSpan.FromSeconds(5));
        }
        collector.BuildCollectPayload(new());
        Require(peak == 1 && calls == 2, "Deep collections overlapped or never recovered.");
    }

    private static async Task HeartbeatsContinueDuringBlockedWorkAsync(bool job)
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-light-heartbeat-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        using ManualResetEventSlim release = new();
        using ManualResetEventSlim entered = new();
        Worker? worker = null;
        try
        {
            AgentConfig config = new()
            {
                MachineId = "synthetic-machine", AgentToken = "synthetic-test-token",
                ServerBaseUrl = "https://isolated.invalid",
                StatePath = Path.Combine(root, "agent.state.json"), LogPath = Path.Combine(root, "agent.log"),
                JobsPath = Path.Combine(root, "jobs"), PendingResultsPath = Path.Combine(root, "pending"),
                HeartbeatUrl = "https://isolated.invalid/heartbeat", CollectUrl = "https://isolated.invalid/collect",
                JobsPullUrl = "https://isolated.invalid/jobs", JobsResultUrl = "https://isolated.invalid/result",
                TelemetryEnabled = false,
                Intervals = new() { HeartbeatSeconds = 5, CollectSeconds = 3600, JobsSeconds = 5 }
            };
            File.WriteAllText(config.StatePath, JsonSerializer.Serialize(new AgentState
            {
                LastCollectionAt = job ? DateTimeOffset.UtcNow : null,
                InstallStatus = "installed", ExtensionData = new() { ["synthetic_marker"] = JsonSerializer.SerializeToElement("preserve") }
            }));
            FakeHandler handler = new(job);
            AgentApiClient api = new(new Factory(handler));
            JsonlLogger logger = new(config.LogPath);
            WindowsInventoryCollector collector = new(logger, _ =>
            {
                entered.Set();
                release.Wait();
                if (!job) throw new InvalidOperationException("Synthetic inventory failure.");
                return new AgentCollectPayload { MachineId = config.MachineId };
            });
            JobExecutionPolicy policy = new(new JobStore(config.JobsPath));
            JobExecutionCoordinator coordinator = new(policy, logger);
            PendingResultQueue queue = new(config.PendingResultsPath);
            worker = new Worker(new ConfigService(), new StateService(), logger, api, collector,
                new JobExecutor(collector, logger, policy), coordinator, queue,
                new TelemetryPipeline(new TelemetryCollector(), api, logger), () => config, Path.Combine(root, "update.json"));
            await worker.StartAsync(CancellationToken.None);
            Require(entered.Wait(TimeSpan.FromSeconds(5)), "Synthetic blocking work did not start.");
            await handler.TwoHeartbeats.Task.WaitAsync(TimeSpan.FromSeconds(15));
            Require(handler.Capabilities.Contains("jobs_protocol_v2") && handler.Capabilities.Contains("job_protocol_v2"),
                "Real heartbeat loop must emit canonical protocol capability and its legacy alias.");
            var times = handler.Times.ToArray();
            Require(times.Length >= 2 && times[1] - times[0] >= TimeSpan.FromSeconds(4.5),
                "Heartbeat interval was ignored or heartbeats stopped behind blocking work.");
            Require(!release.IsSet, "The test released inventory before proving heartbeat isolation.");
            release.Set();
            await Task.Delay(1000);
            using JsonDocument state = JsonDocument.Parse(File.ReadAllText(config.StatePath));
            Require(state.RootElement.GetProperty("install_status").GetString() == "installed"
                && state.RootElement.GetProperty("synthetic_marker").GetString() == "preserve",
                "Concurrent heartbeat corrupted persistent lifecycle/extension state.");
        }
        finally
        {
            release.Set();
            if (worker is not null)
            {
                using CancellationTokenSource stop = new(TimeSpan.FromSeconds(10));
                await worker.StopAsync(stop.Token);
                worker.Dispose();
            }
            Directory.Delete(root, recursive: true);
        }
    }

    private static string RepoRoot()
    {
        for (DirectoryInfo? dir = new(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
            if (Directory.Exists(Path.Combine(dir.FullName, "NightOwl.Agent.Windows"))) return dir.FullName;
        throw new InvalidOperationException("Source root unavailable for static heartbeat guard.");
    }

    internal static async Task EmitHeartbeatContractAsync(bool observeFootprint = false)
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-heartbeat-contract-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        string? previousHome = Environment.GetEnvironmentVariable("NIGHTOWL_HOME");
        Environment.SetEnvironmentVariable("NIGHTOWL_HOME", root);
        Worker? worker = null;
        try
        {
            AgentConfig config = new()
            {
                MachineId = Environment.GetEnvironmentVariable("NIGHTOWL_TEST_MACHINE") ?? Guid.NewGuid().ToString("D"),
                AgentVersion = "synthetic-v2", AgentToken = "synthetic-heartbeat-contract-token",
                ServerBaseUrl = "https://isolated.invalid", HeartbeatUrl = "https://isolated.invalid/heartbeat",
                JobsPullUrl = "https://isolated.invalid/jobs", JobsResultUrl = "https://isolated.invalid/result",
                StatePath = Path.Combine(root,"state.json"), LogPath = Path.Combine(root,"log.jsonl"),
                JobsPath = Path.Combine(root,"jobs"), PendingResultsPath = Path.Combine(root,"pending"),
                InstallPath = Path.Combine(root,"install"), TelemetryEnabled = observeFootprint
            };
            config.AllowedJobTypes.Add("unknown_privileged_operation");
            config.AllowedJobTypes.Add("install_software");
            DateTimeOffset now = DateTimeOffset.UtcNow;
            File.WriteAllText(config.StatePath, JsonSerializer.Serialize(new AgentState
            {
                LastCollectionAt=now, LastBasicInventoryAt=now, LastDetailedHardwareAt=now,
                LastVolumesAt=now, LastNetworkInventoryAt=now, LastAncillaryInventoryAt=now
            }));
            ContractHandler handler = new();
            AgentApiClient api = new(new ContractFactory(handler));
            JsonlLogger logger = new(config.LogPath);
            WindowsInventoryCollector collector = new(logger, _ => throw new Exception("No inventory in heartbeat contract test."));
            JobExecutionPolicy policy = new(new JobStore(config.JobsPath));
            worker = new Worker(new ConfigService(),new StateService(),logger,api,collector,
                new JobExecutor(collector,logger,policy),new JobExecutionCoordinator(policy,logger),
                new PendingResultQueue(config.PendingResultsPath),new TelemetryPipeline(new TelemetryCollector(),api,logger),
                () => config,Path.Combine(root,"update.json"));
            System.Diagnostics.Stopwatch startup = System.Diagnostics.Stopwatch.StartNew();
            await worker.StartAsync(CancellationToken.None);
            string body = await handler.Heartbeat.Task.WaitAsync(TimeSpan.FromSeconds(15));
            using JsonDocument document = JsonDocument.Parse(body);
            string[] capabilities = document.RootElement.GetProperty("agent").GetProperty("capabilities").EnumerateArray().Select(x=>x.GetString()!).ToArray();
            Require(capabilities.Count(x=>x=="jobs_protocol_v2")==1 && capabilities.Count(x=>x=="job_protocol_v2")==1,
                "Actual Worker must announce canonical jobs v2 and preserve its legacy alias.");
            Require(!capabilities.Contains("unknown_privileged_operation") && !capabilities.Contains("install_software"),
                "Capability correction must not advertise unknown or disabled privileged operations.");
            Require(!body.Contains(config.AgentToken,StringComparison.Ordinal),"Heartbeat body exposed a credential.");
            // Captured by the isolated Django test process; no credential/header is exported.
            if (!observeFootprint) Console.WriteLine("WORKER_HEARTBEAT_CONTRACT="+body);
            else
            {
                double firstHeartbeatMs = startup.Elapsed.TotalMilliseconds;
                using System.Diagnostics.Process process = System.Diagnostics.Process.GetCurrentProcess();
                TimeSpan cpuBefore = process.TotalProcessorTime;
                long workingSet = 0; int threads = 0, handles = 0;
                bool ioAvailable = IoCounters(process.Handle, out ProcessIo ioBefore);
                System.Diagnostics.Stopwatch observation = System.Diagnostics.Stopwatch.StartNew();
                for (int i = 0; i < 30; i++)
                {
                    await Task.Delay(1000); process.Refresh();
                    workingSet = Math.Max(workingSet, process.WorkingSet64);
                    threads = Math.Max(threads, process.Threads.Count); handles = Math.Max(handles, process.HandleCount);
                }
                process.Refresh(); ioAvailable &= IoCounters(process.Handle, out ProcessIo ioAfter);
                Console.WriteLine("WORKER_LOCAL_FOOTPRINT=" + JsonSerializer.Serialize(new {
                    kind="actual_worker_native_telemetry_synthetic_inventory_and_inprocess_http_not_installed_service",
                    observation_seconds=observation.Elapsed.TotalSeconds, first_heartbeat_ms=firstHeartbeatMs,
                    normalized_cpu_percent=(process.TotalProcessorTime-cpuBefore).TotalSeconds/observation.Elapsed.TotalSeconds/Environment.ProcessorCount*100,
                    peak_working_set_bytes=workingSet, peak_threads=threads, peak_handles=handles,
                    process_read_transfer_bytes=ioAvailable ? (ulong?)(ioAfter.ReadBytes-ioBefore.ReadBytes) : null,
                    process_write_transfer_bytes=ioAvailable ? (ulong?)(ioAfter.WriteBytes-ioBefore.WriteBytes) : null,
                    http_handler_requests=handler.Requests, serialized_outbound_payload_bytes=handler.PayloadBytes,
                    fixture_files=Directory.GetFiles(root,"*",SearchOption.AllDirectories).Length,
                    fixture_bytes=Directory.GetFiles(root,"*",SearchOption.AllDirectories).Sum(p=>new FileInfo(p).Length)
                }));
            }
        }
        finally
        {
            if(worker is not null)
            {
                using CancellationTokenSource stop = new(TimeSpan.FromSeconds(10));
                await worker.StopAsync(stop.Token);
                worker.Dispose();
            }
            Environment.SetEnvironmentVariable("NIGHTOWL_HOME", previousHome);
            Directory.Delete(root,true);
        }
    }

    private sealed class ContractFactory(ContractHandler handler) : IHttpClientFactory
    { public HttpClient CreateClient(string name) => new(handler,disposeHandler:false); }
    private sealed class ContractHandler : HttpMessageHandler
    {
        internal int Requests;
        internal long PayloadBytes;
        internal TaskCompletionSource<string> Heartbeat { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct)
        {
            Interlocked.Increment(ref Requests);
            if (request.Content is not null) Interlocked.Add(ref PayloadBytes, (await request.Content.ReadAsByteArrayAsync(ct)).LongLength);
            if(request.RequestUri!.AbsolutePath=="/heartbeat")
                Heartbeat.TrySetResult(await request.Content!.ReadAsStringAsync(ct));
            string body=request.RequestUri.AbsolutePath.EndsWith("/signal/") ? "{\"protocol\":2,\"available\":false}" : "{}";
            return new(HttpStatusCode.OK) {Content=new StringContent(body,Encoding.UTF8,"application/json")};
        }
    }

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct ProcessIo { public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes; }
    [System.Runtime.InteropServices.DllImport("kernel32.dll", EntryPoint="GetProcessIoCounters")]
    private static extern bool IoCounters(nint process, out ProcessIo counters);

    private sealed class Factory(FakeHandler handler) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => new(handler, disposeHandler: false);
    }

    private sealed class FakeHandler(bool job) : HttpMessageHandler
    {
        private int _jobSent;
        private readonly string _jobId = Guid.NewGuid().ToString("D");
        internal ConcurrentQueue<DateTimeOffset> Times { get; } = new();
        internal TaskCompletionSource TwoHeartbeats { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal string[] Capabilities { get; private set; } = Array.Empty<string>();
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            string body = "{}";
            if (request.RequestUri!.AbsolutePath == "/heartbeat")
            {
                using JsonDocument document=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct));
                Capabilities=document.RootElement.GetProperty("agent").GetProperty("capabilities").EnumerateArray().Select(item=>item.GetString()!).ToArray();
                Times.Enqueue(DateTimeOffset.UtcNow);
                if (Times.Count >= 2) TwoHeartbeats.TrySetResult();
            }
            if (request.RequestUri.AbsolutePath == "/jobs")
                body = job && Interlocked.Exchange(ref _jobSent, 1) == 0
                    ? JsonSerializer.Serialize(new { jobs = new[] { new { id = _jobId, type = "force_inventory", payload = new { } } } })
                    : "{\"jobs\":[]}";
            if (request.RequestUri.AbsolutePath.EndsWith("/signal/")) body = "{\"protocol\":2,\"available\":true}";
            if (request.RequestUri.AbsolutePath.EndsWith("/started/")) body = JsonSerializer.Serialize(new {job_id=_jobId,state="running"});
            if (request.RequestUri.AbsolutePath == "/result") body = JsonSerializer.Serialize(new {job_id=_jobId,result_id=request.Headers.GetValues("Idempotency-Key").First()});
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, "application/json") };
        }
    }
}
