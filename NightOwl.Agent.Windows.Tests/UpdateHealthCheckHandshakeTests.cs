using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;
using System.Net;
using System.Reflection;
using System.Text.Json;

internal static class UpdateHealthCheckHandshakeTests
{
    internal static async Task RunAsync()
    {
        await LegacyUpdaterWaitsForHandshakeAsync();
        await LegacyUpdaterDelayOver110SecondsAsync();
        await ModernUpdaterConfirmsImmediatelyAsync();
        await WaitingStateCanChangeBeforeConfirmationAsync();
        foreach (string stage in new[] { UpdateStages.RollbackRequired, UpdateStages.RollbackStarting })
            await ChangedStateIsNotConfirmedAsync(stage);
        await ChangedStateIsNotConfirmedAsync(UpdateStages.Failed);
        await ChangedStateIsNotConfirmedAsync(UpdateStages.RolledBack);
        await ChangedStateIsNotConfirmedAsync(UpdateStages.RollbackFailed);
        await ChangedIdentityIsNotConfirmedAsync("update_id");
        await ChangedIdentityIsNotConfirmedAsync("job_id");
        await ChangedIdentityIsNotConfirmedAsync("target_version");
        await ChangedIdentityIsNotConfirmedAsync("attempt");
        await ChangedIdentityIsNotConfirmedAsync("channel");
        await ChangedIdentityIsNotConfirmedAsync("release_id");
        await ChangedIdentityIsNotConfirmedAsync("package_sha256");
        await ChangedIdentityIsNotConfirmedAsync("package_url");
        await ChangedIdentityIsNotConfirmedAsync("source");
        await DisappearedStateIsNotRecreatedAsync();
        await StaleStateWaitsUntilCancellationAsync();
        await TransientReadFailureRecoversAsync();
        await PersistentReadFailureStopsAsync();
        await InitialReadRetriesAreBoundedAsync();
        await AlreadyCompletedIsIdempotentAsync();
        await RollbackHealthCheckStillWorksAsync();
        await CancellationPropagatesAsync();
        await WorkerRunsWhileLegacyHandshakeWaitsAsync();
        await WorkerRunsWhileLegacyHandshakeWaitsAsync("stale");
        await WorkerRunsWhileLegacyHandshakeWaitsAsync("missing");
        await TargetedFinalizationRequiresMatchingUpdateIdAsync();
    }

    private static async Task LegacyUpdaterWaitsForHandshakeAsync()
    {
        // RC41: StartService -> ServiceStarted -> Tray/validation -> WaitingHealthCheck.
        using Fixture fixture = new(UpdateStages.ServiceStarted, legacy: true);
        (Task<HandshakeResult> wait, TaskCompletionSource<bool> resume) = fixture.StartParkedWait();
        Require(fixture.Store.Load()!.CurrentStage == UpdateStages.ServiceStarted,
            "Legacy updater state changed before the healthcheck handshake.");
        Require(!wait.IsCompleted, "Handshake blocked unrelated agent startup work.");
        fixture.ChangeStage(UpdateStages.WaitingHealthCheck);
        resume.SetResult(true);
        HandshakeResult result = await wait;
        Require(result.Status == HandshakeStatus.Ready && result.Waited,
            "Legacy updater's delayed WaitingHealthCheck was not observed.");
        Require(UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out UpdateState? confirmed),
            "Target agent did not confirm legacy updater state.");
        Require(confirmed!.CurrentStage == UpdateStages.Completed && confirmed.HealthCheckConfirmed,
            "Confirmed state is not completed.");
        Require(fixture.Store.Load()!.CurrentStage == UpdateStages.Completed,
            "Legacy updater waiter did not observe persisted Completed.");
        Require(!fixture.Store.Save(fixture.Initial), "Legacy updater overwrote terminal state.");
    }

    private static async Task LegacyUpdaterDelayOver110SecondsAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted, legacy: true);
        int virtualPolls = 0;
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial, fixture.Read,
            (_, _) =>
            {
                virtualPolls++;
                Require(fixture.Store.Load()!.CurrentStage != UpdateStages.Completed,
                    "Agent completed before RC41 persisted WaitingHealthCheck.");
                if (virtualPolls == 221) fixture.ChangeStage(UpdateStages.WaitingHealthCheck);
                return Task.CompletedTask;
            }, CancellationToken.None);
        Require(virtualPolls * UpdateHealthCheckHandshake.PollInterval.TotalSeconds > 110,
            "Test did not cross the RC41 post-start delay envelope.");
        Require(result.Status == HandshakeStatus.Ready && result.Waited,
            "Watcher did not survive the RC41 post-start delay.");
        Require(UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out _)
            && fixture.Store.Load()!.CurrentStage == UpdateStages.Completed,
            "RC41 waiter did not observe Completed after the delayed handshake.");
    }

    private static async Task ModernUpdaterConfirmsImmediatelyAsync()
    {
        using Fixture fixture = new(UpdateStages.WaitingHealthCheck);
        int delays = 0;
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial, fixture.Read,
            (_, _) => { delays++; return Task.CompletedTask; }, CancellationToken.None);
        Require(result.Status == HandshakeStatus.Ready && !result.Waited && delays == 0,
            "Modern updater did not take the immediate path.");
        Require(UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out UpdateState? confirmed)
            && confirmed!.CurrentStage == UpdateStages.Completed, "Modern updater was not confirmed.");
    }

    private static async Task WaitingStateCanChangeBeforeConfirmationAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        fixture.ChangeStage(UpdateStages.WaitingHealthCheck);
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial, fixture.Read,
            (_, _) => Task.CompletedTask, CancellationToken.None);
        Require(result.Status == HandshakeStatus.Ready, "Waiting stage was not observed.");
        fixture.ChangeStage(UpdateStages.RollbackRequired);
        Require(!UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out _),
            "Rollback won but the agent confirmed Completed.");
        Require(fixture.Store.Load()!.CurrentStage == UpdateStages.RollbackRequired,
            "Rollback state was overwritten.");
    }

    private static async Task ChangedStateIsNotConfirmedAsync(string stage)
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        (Task<HandshakeResult> wait, TaskCompletionSource<bool> resume) = fixture.StartParkedWait();
        fixture.ChangeStage(stage);
        resume.SetResult(true);
        HandshakeResult result = await wait;
        Require(result.Status == HandshakeStatus.Skipped, $"Stage {stage} was not rejected.");
        Require(!UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out _),
            $"Stage {stage} was confirmed as Completed.");
        Require(fixture.Store.Load()!.CurrentStage == stage, $"Stage {stage} was overwritten.");
    }

    private static async Task ChangedIdentityIsNotConfirmedAsync(string field)
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        (Task<HandshakeResult> wait, TaskCompletionSource<bool> resume) = fixture.StartParkedWait();
        UpdateState replacement = fixture.Store.Load()!;
        if (field == "update_id") replacement.UpdateId = Guid.NewGuid().ToString();
        if (field == "job_id") replacement.JobId = Guid.NewGuid().ToString();
        if (field == "target_version") replacement.TargetVersion = "different-target";
        if (field == "attempt") replacement.Attempt++;
        if (field == "channel") replacement.Channel = "stable";
        if (field == "release_id") replacement.ReleaseId = "different-release";
        if (field == "package_sha256") replacement.ExpectedSha256 = new string('b', 64);
        if (field == "package_url") replacement.PackageUrl = "https://example.invalid/other.zip";
        if (field == "source") replacement.Source = "different-source";
        replacement.MarkStage(UpdateStages.WaitingHealthCheck);
        Require(fixture.Store.Save(replacement), "Replacement state was not persisted.");
        resume.SetResult(true);
        HandshakeResult result = await wait;
        Require(result.Status == HandshakeStatus.Skipped && result.Reason == "update_identity_changed",
            $"Changed {field} was not detected.");
        Require(!UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out _),
            $"Changed {field} was confirmed.");
        Require(fixture.Store.Load()!.CurrentStage == UpdateStages.WaitingHealthCheck,
            "Replacement state was modified.");
    }

    private static async Task DisappearedStateIsNotRecreatedAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        (Task<HandshakeResult> wait, TaskCompletionSource<bool> resume) = fixture.StartParkedWait();
        File.Delete(fixture.Path);
        resume.SetResult(true);
        HandshakeResult result = await wait;
        Require(result.Status == HandshakeStatus.Skipped && result.Reason == "state_disappeared",
            "Missing state was not detected.");
        Require(!UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out _),
            "Missing state was confirmed.");
        Require(!File.Exists(fixture.Path), "Missing state was recreated.");
    }

    private static async Task StaleStateWaitsUntilCancellationAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        using CancellationTokenSource cts = new();
        int delays = 0;
        try
        {
            await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial, fixture.Read,
                (_, ct) => { if (++delays == 3) cts.Cancel(); ct.ThrowIfCancellationRequested(); return Task.CompletedTask; },
                cts.Token);
            throw new InvalidOperationException("Stale watcher ended without cancellation.");
        }
        catch (OperationCanceledException) when (cts.IsCancellationRequested) { }
        Require(delays == 3, "Watcher did not remain active until cancellation.");
        Require(fixture.Store.Load()!.CurrentStage == UpdateStages.ServiceStarted,
            "Stale watcher changed state.");
    }

    private static async Task TransientReadFailureRecoversAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        int reads = 0;
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial,
            () => ++reads <= 2 ? (false, null) : fixture.Read(),
            (_, _) => { if (reads == 2) fixture.ChangeStage(UpdateStages.WaitingHealthCheck); return Task.CompletedTask; },
            CancellationToken.None);
        Require(result.Status == HandshakeStatus.Ready && result.ReadRetries == 2,
            "Transient read errors did not recover.");
        Require(UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out _),
            "Recovered state was not confirmed.");
    }

    private static async Task PersistentReadFailureStopsAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial,
            () => (false, null), (_, _) => Task.CompletedTask, CancellationToken.None);
        Require(result.Status == HandshakeStatus.ReadFailed && result.ReadRetries == 3,
            "Persistent read errors were not bounded.");
        Require(fixture.Store.Load()!.CurrentStage == UpdateStages.ServiceStarted,
            "Read failure changed state.");
    }

    private static async Task InitialReadRetriesAreBoundedAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        int reads = 0;
        (bool readable, UpdateState? state) = await UpdateHealthCheckHandshake.ReadInitialAsync(
            () => ++reads < 3 ? (false, null) : fixture.Read(),
            (_, _) => Task.CompletedTask, CancellationToken.None);
        Require(readable && state is not null && reads == 3, "Initial read did not recover.");
        reads = 0;
        (readable, state) = await UpdateHealthCheckHandshake.ReadInitialAsync(
            () => { reads++; return (false, null); }, (_, _) => Task.CompletedTask, CancellationToken.None);
        Require(!readable && state is null && reads == 3, "Initial read failure was not bounded.");
    }

    private static async Task AlreadyCompletedIsIdempotentAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        (Task<HandshakeResult> wait, TaskCompletionSource<bool> resume) = fixture.StartParkedWait();
        fixture.ChangeStage(UpdateStages.Completed);
        resume.SetResult(true);
        HandshakeResult result = await wait;
        Require(result.Status == HandshakeStatus.AlreadyCompleted, "Completed state was not idempotent.");
        Require(!UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out _),
            "Completed state was rewritten.");
    }

    private static async Task RollbackHealthCheckStillWorksAsync()
    {
        using Fixture fixture = new(UpdateStages.RollbackWaitingHealthCheck);
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial, fixture.Read,
            (_, _) => throw new InvalidOperationException("Rollback fast path unexpectedly waited."), CancellationToken.None);
        Require(result.Status == HandshakeStatus.Ready && !result.Waited, "Rollback healthcheck did not proceed.");
        Require(UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, true, out UpdateState? confirmed)
            && confirmed!.CurrentStage == UpdateStages.RolledBack, "Rollback healthcheck was not confirmed.");
    }

    private static async Task CancellationPropagatesAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        using CancellationTokenSource cts = new();
        cts.Cancel();
        try
        {
            await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial, fixture.Read, Task.Delay, cts.Token);
            throw new InvalidOperationException("Canceled handshake continued.");
        }
        catch (OperationCanceledException) { }
    }

    private static async Task WorkerRunsWhileLegacyHandshakeWaitsAsync(string? oldResult = null)
    {
        string directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(),
            "nightowl-worker-handshake-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        Worker? worker = null;
        try
        {
            string version = (typeof(Worker).Assembly
                .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
                ?? typeof(Worker).Assembly.GetName().Version?.ToString() ?? "").Split('+')[0];
            Require(!string.IsNullOrWhiteSpace(version), "Running test agent version is missing.");
            string updatePath = System.IO.Path.Combine(directory, "update-state.json");
            string statePath = System.IO.Path.Combine(directory, "agent.state.json");
            string jobsPath = System.IO.Path.Combine(directory, "jobs");
            string pendingPath = System.IO.Path.Combine(directory, "pending-results");
            string jobA = Guid.NewGuid().ToString();
            string jobB = Guid.NewGuid().ToString();
            UpdateStateStore updateStore = new(updatePath);
            UpdateState update = UpdateState.Create(Guid.NewGuid().ToString(), jobA, "rc41", version);
            update.MarkStage(UpdateStages.ServiceStarted);
            Require(updateStore.Save(update), "Could not persist RC41 ServiceStarted state.");

            JobStore jobs = new(jobsPath);
            jobs.Mark(jobA, "update_agent", "running", 1, "test-update");
            AgentConfig config = new()
            {
                AgentToken = "synthetic-test-token",
                MachineId = "synthetic-test-machine",
                AgentVersion = version,
                ServerBaseUrl = "https://nightowl.test.invalid",
                HeartbeatUrl = "https://nightowl.test.invalid/heartbeat",
                CollectUrl = "https://nightowl.test.invalid/collect",
                JobsPullUrl = "https://nightowl.test.invalid/jobs/pull",
                JobsResultUrl = "https://nightowl.test.invalid/jobs/result",
                StatePath = statePath,
                LogPath = System.IO.Path.Combine(directory, "agent.log"),
                JobsPath = jobsPath,
                PendingResultsPath = pendingPath,
                TelemetryEnabled = false
            };
            File.WriteAllText(statePath, JsonSerializer.Serialize(new AgentState
            {
                LastCollectionAt = DateTimeOffset.UtcNow
            }, new JsonSerializerOptions(JsonSerializerDefaults.Web)));

            JsonlLogger logger = new(config.LogPath);
            TestHttpHandler handler = new();
            AgentApiClient api = new(new TestHttpClientFactory(handler));
            WindowsInventoryCollector collector = new(logger);
            JobExecutionPolicy policy = new(jobs);
            JobExecutionCoordinator coordinator = new(policy, logger);
            PendingResultQueue queue = new(pendingPath);
            worker = new Worker(new ConfigService(), new StateService(), logger, api, collector,
                new JobExecutor(collector, logger, policy), coordinator, queue,
                new TelemetryPipeline(new TelemetryCollector(), api, logger), () => config, updatePath);

            await worker.StartAsync(CancellationToken.None);
            await Task.WhenAll(handler.Heartbeat.Task, handler.JobPull.Task).WaitAsync(TimeSpan.FromSeconds(20));
            Require(updateStore.Load()!.CurrentStage == UpdateStages.ServiceStarted,
                "Worker completed the update before RC41 wrote WaitingHealthCheck.");
            Require(jobs.Load(jobA)!.Status == "running", "Startup recovery interrupted update A.");
            jobs.Mark(jobB, "ping", "running", 1, "test-concurrent-job");

            if (oldResult is not null)
            {
                Directory.CreateDirectory(pendingPath);
                Dictionary<string, string> details = new() { ["type"] = "update_agent" };
                if (oldResult == "stale") details["update_id"] = Guid.NewGuid().ToString();
                File.WriteAllText(System.IO.Path.Combine(pendingPath, "aaa-old-result.json"),
                    JsonSerializer.Serialize(new JobExecutionResult
                    {
                        JobId = jobA,
                        Status = JobFinalStatuses.Failed,
                        Result = details
                    }));
            }

            update.MarkStage(UpdateStages.WaitingHealthCheck);
            Require(updateStore.Save(update), "Could not persist RC41 WaitingHealthCheck.");
            await handler.Result.Task.WaitAsync(TimeSpan.FromSeconds(20));
            await WaitForAsync(() => jobs.Load(jobA)?.Status == JobFinalStatuses.Completed,
                TimeSpan.FromSeconds(10));
            Require(jobs.Load(jobB)!.Status == "running",
                "Concurrent job B was incorrectly recovered as JOB_INTERRUPTED.");
            if (oldResult is not null)
            {
                Require(Directory.GetFiles(System.IO.Path.Combine(pendingPath, "migrated"), "aaa-old-result-*.json").Length == 1,
                    "Incompatible legacy result was not removed from the active pending directory.");
                Require(!queue.LoadAll().Any(record => record.JobId == jobA && record.Status == JobFinalStatuses.Failed),
                    "Incompatible legacy result was reused in the pending queue.");
            }
            Require(updateStore.Load()!.CurrentStage == UpdateStages.Completed,
                "RC41 updater could not observe persisted Completed.");
            using JsonDocument result = JsonDocument.Parse(handler.Result.Task.Result);
            JsonElement payload = result.RootElement.GetProperty("result");
            Require(result.RootElement.GetProperty("job_id").GetString() == jobA
                && result.RootElement.GetProperty("status").GetString() == "completed"
                && payload.GetProperty("update_id").GetString() == update.UpdateId
                && payload.GetProperty("installed_version").GetString() == version
                && payload.GetProperty("active_version").GetString() == version
                && payload.GetProperty("health_check").GetProperty("confirmed").GetBoolean()
                && !payload.GetProperty("rollback_performed").GetBoolean(),
                "Update result protocol changed during legacy handshake.");
        }
        finally
        {
            if (worker is not null)
            {
                await worker.StopAsync(CancellationToken.None);
                worker.Dispose();
            }
            Directory.Delete(directory, recursive: true);
        }
    }

    private static async Task TargetedFinalizationRequiresMatchingUpdateIdAsync()
    {
        foreach (string candidate in new[] { "stale", "missing", "matching" })
        {
            string directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(),
                "nightowl-targeted-recovery-tests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                string jobId = Guid.NewGuid().ToString();
                string expectedUpdateId = Guid.NewGuid().ToString();
                Dictionary<string, string> details = new() { ["type"] = "update_agent" };
                if (candidate != "missing")
                    details["update_id"] = candidate == "matching" ? expectedUpdateId : Guid.NewGuid().ToString();

                JobStore jobs = new(System.IO.Path.Combine(directory, "jobs"));
                jobs.Mark(jobId, "update_agent", "running", 1, "test-targeted-recovery");
                PendingResultQueue queue = new(System.IO.Path.Combine(directory, "pending"));
                PendingResultRecord pending = queue.Enqueue("update_agent", new JobExecutionResult
                {
                    JobId = jobId,
                    Status = JobFinalStatuses.Completed,
                    Result = details
                });
                JobExecutionCoordinator coordinator = new(new JobExecutionPolicy(jobs),
                    new JsonlLogger(System.IO.Path.Combine(directory, "agent.log")));
                bool finalized = await coordinator.FinalizePendingUpdateJobAsync(new AgentConfig(), pending,
                    jobId, expectedUpdateId, CancellationToken.None);
                bool shouldFinalize = candidate == "matching";
                Require(finalized == shouldFinalize
                    && jobs.Load(jobId)!.Status == (shouldFinalize ? JobFinalStatuses.Completed : "running"),
                    $"Targeted finalization accepted an incompatible {candidate} update result.");
            }
            finally
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    private static async Task WaitForAsync(Func<bool> condition, TimeSpan timeout)
    {
        DateTimeOffset deadline = DateTimeOffset.UtcNow.Add(timeout);
        while (!condition())
        {
            Require(DateTimeOffset.UtcNow < deadline, "Timed out waiting for local update finalization.");
            await Task.Delay(25);
        }
    }

    private sealed class TestHttpClientFactory(HttpMessageHandler handler) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => new(handler, disposeHandler: false);
    }

    private sealed class TestHttpHandler : HttpMessageHandler
    {
        internal TaskCompletionSource<bool> Heartbeat { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal TaskCompletionSource<bool> JobPull { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal TaskCompletionSource<string> Result { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            string path = request.RequestUri?.AbsolutePath ?? "";
            if (path == "/heartbeat") Heartbeat.TrySetResult(true);
            if (path == "/jobs/pull") JobPull.TrySetResult(true);
            if (path == "/jobs/result")
                Result.TrySetResult(await request.Content!.ReadAsStringAsync(ct));
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(path == "/jobs/pull" ? "{\"jobs\":[]}" : "{}")
            };
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private sealed class Fixture : IDisposable
    {
        private readonly string _directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(),
            "nightowl-handshake-tests", Guid.NewGuid().ToString("N"));

        internal string Path => System.IO.Path.Combine(_directory, "update-state.json");
        internal UpdateStateStore Store { get; }
        internal UpdateState Initial { get; }

        internal Fixture(string stage, bool legacy = false)
        {
            Directory.CreateDirectory(_directory);
            Store = new UpdateStateStore(Path);
            Initial = UpdateState.Create(Guid.NewGuid().ToString(), Guid.NewGuid().ToString(), "rc41", "rc43");
            if (!legacy)
            {
                Initial.ReleaseId = "test-release";
                Initial.Channel = "development";
                Initial.ExpectedSha256 = new string('a', 64);
                Initial.PackageUrl = "https://example.invalid/package.zip";
                Initial.Source = "panel";
            }
            Initial.MarkStage(stage);
            Require(Store.Save(Initial), "Initial update state could not be persisted.");
        }

        internal (bool Readable, UpdateState? State) Read()
            => (Store.TryLoad(out UpdateState? state, out _), state);

        internal void ChangeStage(string stage)
        {
            UpdateState state = Store.Load()!;
            state.MarkStage(stage);
            Require(Store.Save(state), $"Could not persist stage {stage}.");
        }

        internal (Task<HandshakeResult> Wait, TaskCompletionSource<bool> Resume) StartParkedWait()
        {
            TaskCompletionSource<bool> entered = new(TaskCreationOptions.RunContinuationsAsynchronously);
            TaskCompletionSource<bool> resume = new(TaskCreationOptions.RunContinuationsAsynchronously);
            Task<HandshakeResult> wait = UpdateHealthCheckHandshake.WaitAsync(Initial, Read,
                async (_, ct) => { entered.TrySetResult(true); await resume.Task.WaitAsync(ct); },
                CancellationToken.None);
            Require(entered.Task.IsCompletedSuccessfully, "Handshake did not reach its first wait.");
            return (wait, resume);
        }

        public void Dispose() => Directory.Delete(_directory, recursive: true);
    }
}
