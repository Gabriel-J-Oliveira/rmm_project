using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class TelemetryConfigurationService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly TelemetryRuntime _runtime;
    private readonly PendingResultQueue _queue;
    private readonly string _configPath;
    private readonly string _journalPath;
    private readonly SemaphoreSlim _gate = new(1, 1);
    public bool RecoveryPending => File.Exists(_journalPath);

    public TelemetryConfigurationService(TelemetryRuntime runtime, PendingResultQueue queue)
        : this(runtime, queue, NightOwlPaths.Current.ResolveConfigPath(),
            Path.Combine(NightOwlPaths.Current.StateDir, "telemetry-configuration.json")) { }

    internal TelemetryConfigurationService(TelemetryRuntime runtime, PendingResultQueue queue,
        string configPath, string journalPath)
    {
        _runtime = runtime;
        _queue = queue;
        _configPath = configPath;
        _journalPath = journalPath;
    }

    public async Task RecoverAsync(AgentConfig config, CancellationToken ct)
    {
        if (!File.Exists(_journalPath)) return;
        TelemetryConfigurationJournal journal = JsonSerializer.Deserialize<TelemetryConfigurationJournal>(
            File.ReadAllText(_journalPath), JsonOptions) ?? throw new InvalidOperationException("Invalid telemetry journal.");
        if (!Guid.TryParse(journal.JobId, out _) || journal.MachineId != config.MachineId)
            throw new InvalidOperationException("Telemetry journal identity mismatch.");
        await _gate.WaitAsync(ct);
        try
        {
            // A durable result is retransmitted, never re-applied.
            if (journal.Result is not null) PersistResult(journal);
            else await ApplyJournalAsync(config, journal, ct);
        }
        finally { _gate.Release(); }
    }

    public async Task<JobExecutionResult> ConfigureAsync(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        TelemetrySettings requested = TelemetrySettings.Parse(job.Payload);
        await _gate.WaitAsync(ct);
        try
        {
            if (File.Exists(_journalPath)) throw new InvalidOperationException("Telemetry recovery pending.");
            var journal = new TelemetryConfigurationJournal(job.Id, config.MachineId,
                DateTimeOffset.UtcNow, TelemetrySettings.FromConfig(config), requested, null);
            Save(journal);
            return await ApplyJournalAsync(config, journal, ct);
        }
        finally { _gate.Release(); }
    }

    private async Task<JobExecutionResult> ApplyJournalAsync(AgentConfig config, TelemetryConfigurationJournal journal, CancellationToken ct)
    {
        bool applied = false;
        bool rollback = false;
        string error = "";
        try
        {
            if (journal.RollbackRequired) throw new InvalidOperationException("Telemetry rollback recovery.");
            ConfigService.PersistTelemetry(_configPath, journal.Requested);
            journal.Requested.ApplyTo(config);
            await _runtime.ReloadAsync(config, ct);
            applied = _runtime.Effective == journal.Requested;
            if (!applied) throw new InvalidOperationException("Telemetry readiness mismatch.");
        }
        catch
        {
            // Rollback has its own bounded readiness check, independent of the job timeout.
            error = "TELEMETRY_APPLY_FAILED";
            journal = journal with { RollbackRequired = true };
            Save(journal);
            ConfigService.PersistTelemetry(_configPath, journal.Previous);
            journal.Previous.ApplyTo(config);
            await _runtime.ReloadAsync(config, CancellationToken.None);
            rollback = _runtime.Effective == journal.Previous;
            if (!rollback) throw new InvalidOperationException("Telemetry rollback pending recovery.");
        }
        DateTimeOffset now = DateTimeOffset.UtcNow;
        var result = new JobExecutionResult
        {
            JobId = journal.JobId, Status = applied ? "completed" : "failed",
            StartedAt = journal.StartedAt, FinishedAt = now,
            DurationSeconds = Math.Max(0, (now - journal.StartedAt).TotalSeconds),
            ExitCode = applied ? 0 : 1, ErrorMessage = error,
            Result = new { type = "configure_telemetry", configuration_id = journal.JobId,
                machine_id = config.MachineId, effective = _runtime.Effective?.Report(),
                applied_at = applied ? (DateTimeOffset?)now : null, confirmed = applied,
                rollback_confirmed = rollback, error_code = error }
        };
        journal = journal with { Result = result };
        Save(journal);
        PersistResult(journal);
        return result;
    }

    private void PersistResult(TelemetryConfigurationJournal journal)
    {
        _queue.Enqueue("configure_telemetry", journal.Result!, true, journal.JobId);
        File.Delete(_journalPath);
    }

    private void Save(TelemetryConfigurationJournal journal) =>
        NightOwlFileStore.WriteAllText(_journalPath, JsonSerializer.Serialize(journal, JsonOptions));
}

internal sealed record TelemetryConfigurationJournal(string JobId, string MachineId, DateTimeOffset StartedAt,
    TelemetrySettings Previous, TelemetrySettings Requested, JobExecutionResult? Result, bool RollbackRequired = false);
