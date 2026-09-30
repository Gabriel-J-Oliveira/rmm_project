using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Windows.Services;

internal enum HandshakeStatus { Ready, AlreadyCompleted, Skipped, TimedOut, ReadFailed }

internal sealed record HandshakeResult(HandshakeStatus Status, UpdateState? State, string Reason, bool Waited, int ReadRetries);

internal static class UpdateHealthCheckHandshake
{
    internal const int MaxPolls = 120;
    internal const int MaxReadFailures = 3;
    internal static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(500);

    internal static async Task<(bool Readable, UpdateState? State)> ReadInitialAsync(
        Func<(bool Readable, UpdateState? State)> read,
        Func<TimeSpan, CancellationToken, Task> delay,
        CancellationToken ct)
    {
        for (int attempt = 1; attempt <= MaxReadFailures; attempt++)
        {
            ct.ThrowIfCancellationRequested();
            (bool readable, UpdateState? state) = read();
            if (readable) return (true, state);
            if (attempt < MaxReadFailures) await delay(PollInterval, ct);
        }
        return (false, null);
    }

    internal static async Task<HandshakeResult> WaitAsync(
        UpdateState initial,
        Func<(bool Readable, UpdateState? State)> read,
        Func<TimeSpan, CancellationToken, Task> delay,
        CancellationToken ct,
        int maxPolls = MaxPolls)
    {
        bool rollback = initial.CurrentStage.Equals(UpdateStages.RollbackWaitingHealthCheck, StringComparison.OrdinalIgnoreCase);
        if (CanConfirm(initial, initial, rollback))
            return new(HandshakeStatus.Ready, initial, "waiting_health_check", false, 0);
        if (!IsPreHandshake(initial.CurrentStage) || rollback)
            return new(HandshakeStatus.Skipped, initial, "stage_not_waitable", false, 0);

        int readFailures = 0;
        int readRetries = 0;
        for (int poll = 0; poll < maxPolls; poll++)
        {
            await delay(PollInterval, ct);
            ct.ThrowIfCancellationRequested();
            (bool readable, UpdateState? state) = read();
            if (!readable)
            {
                readRetries++;
                if (++readFailures >= MaxReadFailures)
                    return new(HandshakeStatus.ReadFailed, null, "state_read_failed", true, readRetries);
                continue;
            }
            readFailures = 0;
            if (state is null)
                return new(HandshakeStatus.Skipped, null, "state_disappeared", true, readRetries);
            if (!SameIdentity(initial, state))
                return new(HandshakeStatus.Skipped, state, "update_identity_changed", true, readRetries);
            if (state.CurrentStage.Equals(UpdateStages.Completed, StringComparison.OrdinalIgnoreCase))
                return new(HandshakeStatus.AlreadyCompleted, state, "already_completed", true, readRetries);
            if (CanConfirm(state, initial, rollback: false))
                return new(HandshakeStatus.Ready, state, "waiting_health_check", true, readRetries);
            if (!state.IsActive || state.RollbackRequired || !IsPreHandshake(state.CurrentStage))
                return new(HandshakeStatus.Skipped, state, "stage_changed", true, readRetries);
        }
        return new(HandshakeStatus.TimedOut, null, "handshake_timeout", true, readRetries);
    }

    internal static bool CanConfirm(UpdateState current, UpdateState expected, bool rollback)
        => SameIdentity(expected, current)
            && current.IsActive
            && (rollback || !current.RollbackRequired)
            && current.CurrentStage.Equals(rollback ? UpdateStages.RollbackWaitingHealthCheck : UpdateStages.WaitingHealthCheck,
                StringComparison.OrdinalIgnoreCase);

    internal static bool TryConfirm(UpdateStateStore store, UpdateState expected, bool rollback, out UpdateState? confirmed)
        => store.TryTransitionNonTerminal(expected.UpdateId, current =>
        {
            if (!CanConfirm(current, expected, rollback)) return false;
            current.ServiceStarted = true;
            if (rollback)
            {
                current.PreviousVersionConfirmed = true;
                current.HealthCheckConfirmed = false;
                current.MarkStage(UpdateStages.RolledBack, UpdateStatuses.Failed);
            }
            else
            {
                current.HealthCheckConfirmed = true;
                current.MarkStage(UpdateStages.Completed, UpdateStatuses.Completed);
            }
            return true;
        }, out confirmed);

    private static bool SameIdentity(UpdateState expected, UpdateState current)
        => current.UpdateId.Equals(expected.UpdateId, StringComparison.OrdinalIgnoreCase)
            && string.Equals(current.JobId, expected.JobId, StringComparison.OrdinalIgnoreCase)
            && string.Equals(current.TargetVersion, expected.TargetVersion, StringComparison.OrdinalIgnoreCase);

    private static bool IsPreHandshake(string stage)
        => stage.Equals(UpdateStages.StartingService, StringComparison.OrdinalIgnoreCase)
            || stage.Equals(UpdateStages.ServiceStarted, StringComparison.OrdinalIgnoreCase);
}
