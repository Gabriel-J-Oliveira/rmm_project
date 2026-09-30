using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Services;

internal static class UpdateHealthCheckHandshakeTests
{
    internal static async Task RunAsync()
    {
        await LegacyUpdaterWaitsForHandshakeAsync();
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
        await DisappearedStateIsNotRecreatedAsync();
        await TimeoutDoesNotConfirmAsync();
        await TransientReadFailureRecoversAsync();
        await PersistentReadFailureStopsAsync();
        await InitialReadRetriesAreBoundedAsync();
        await AlreadyCompletedIsIdempotentAsync();
        await RollbackHealthCheckStillWorksAsync();
        await CancellationPropagatesAsync();
    }

    private static async Task LegacyUpdaterWaitsForHandshakeAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
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

    private static async Task TimeoutDoesNotConfirmAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        int delays = 0;
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial, fixture.Read,
            (_, _) => { delays++; return Task.CompletedTask; }, CancellationToken.None, maxPolls: 3);
        Require(result.Status == HandshakeStatus.TimedOut && delays == 3,
            "Handshake timeout was not bounded.");
        Require(fixture.Store.Load()!.CurrentStage == UpdateStages.ServiceStarted,
            "Timed out handshake changed state.");
    }

    private static async Task TransientReadFailureRecoversAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        int reads = 0;
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial,
            () => ++reads <= 2 ? (false, null) : fixture.Read(),
            (_, _) => { if (reads == 2) fixture.ChangeStage(UpdateStages.WaitingHealthCheck); return Task.CompletedTask; },
            CancellationToken.None, maxPolls: 4);
        Require(result.Status == HandshakeStatus.Ready && result.ReadRetries == 2,
            "Transient read errors did not recover.");
        Require(UpdateHealthCheckHandshake.TryConfirm(fixture.Store, fixture.Initial, false, out _),
            "Recovered state was not confirmed.");
    }

    private static async Task PersistentReadFailureStopsAsync()
    {
        using Fixture fixture = new(UpdateStages.ServiceStarted);
        HandshakeResult result = await UpdateHealthCheckHandshake.WaitAsync(fixture.Initial,
            () => (false, null), (_, _) => Task.CompletedTask, CancellationToken.None, maxPolls: 4);
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

        internal Fixture(string stage)
        {
            Directory.CreateDirectory(_directory);
            Store = new UpdateStateStore(Path);
            Initial = UpdateState.Create(Guid.NewGuid().ToString(), Guid.NewGuid().ToString(), "rc41", "rc43");
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
