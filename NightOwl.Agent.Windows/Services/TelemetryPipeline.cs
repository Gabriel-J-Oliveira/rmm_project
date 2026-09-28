using System.Diagnostics;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class TelemetryPipeline
{
    private static readonly TimeSpan CollectionBudget = TimeSpan.FromSeconds(10);
    private readonly TelemetryCollector _collector;
    private readonly AgentApiClient _api;
    private readonly JsonlLogger _logger;
    private Task<TelemetrySample>? _collectionTask;

    public TelemetryPipeline(TelemetryCollector collector, AgentApiClient api, JsonlLogger logger)
    {
        _collector = collector;
        _api = api;
        _logger = logger;
    }

    public async Task RunAsync(AgentConfig config, CancellationToken ct)
    {
        if (!config.TelemetryEnabled) return;
        TelemetryBuffer buffer;
        try
        {
            buffer = new TelemetryBuffer(
                Path.Combine(NightOwlPaths.Current.StateDir, "telemetry-buffer.json"),
                config.TelemetryBufferMaxSamples,
                TimeSpan.FromHours(config.TelemetryBufferMaxAgeHours));
        }
        catch (Exception)
        {
            try
            {
                await _logger.LogAsync("telemetry.buffer.failed", "Telemetry buffer could not be loaded.", new { error_code = "TELEMETRY_BUFFER_LOAD_FAILED" }, ct, "error");
            }
            catch (Exception) { /* Telemetry must not stop the agent when its logger is unavailable. */ }
            return;
        }

        DateTimeOffset nextSample = DateTimeOffset.UtcNow;
        DateTimeOffset lastFlush = DateTimeOffset.UtcNow;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                DateTimeOffset now = DateTimeOffset.UtcNow;
                if (now >= nextSample)
                {
                    nextSample = now.AddSeconds(config.TelemetrySampleSeconds);
                    await CollectOnceAsync(config, buffer, ct);
                }
                if (buffer.Count > 0 &&
                    (now - lastFlush >= TimeSpan.FromSeconds(config.TelemetryFlushSeconds)
                     || (buffer.Count >= 6 && now - lastFlush >= TimeSpan.FromMinutes(1))))
                {
                    lastFlush = now;
                    await FlushOnceAsync(config, buffer, ct);
                }
                await Task.Delay(TimeSpan.FromSeconds(5), ct);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested) { return; }
            catch (Exception)
            {
                try
                {
                    await _logger.LogAsync("telemetry.sample.skipped", "Telemetry loop deferred after local failure.",
                        new { reason = "local_io_failure" }, ct, "warning");
                }
                catch (Exception) { /* Logging must not bring down the agent worker. */ }
                try { await Task.Delay(TimeSpan.FromSeconds(30), ct); }
                catch (OperationCanceledException) when (ct.IsCancellationRequested) { return; }
            }
        }
    }

    internal async Task CollectOnceAsync(AgentConfig config, TelemetryBuffer buffer, CancellationToken ct)
    {
        if (_collectionTask is { IsCompleted: false })
        {
            await _logger.LogAsync("telemetry.sample.skipped", "Previous telemetry collection remains active.", new { reason = "previous_collection_active" }, ct, "warning");
            return;
        }
        if (_collectionTask is { IsFaulted: true }) _ = _collectionTask.Exception;
        await _logger.LogAsync("telemetry.sample.started", "Telemetry collection started.", null, ct);
        using CancellationTokenSource budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
        budget.CancelAfter(CollectionBudget);
        _collectionTask = Task.Run(() => _collector.Collect(config.MachineId, budget.Token), budget.Token);
        try
        {
            TelemetrySample sample = await _collectionTask.WaitAsync(CollectionBudget, ct);
            int discarded = buffer.Add(sample);
            if (discarded > 0)
                await _logger.LogAsync("telemetry.buffer.overflow", "Old telemetry samples discarded.", new { discarded }, ct, "warning");
            await _logger.LogAsync("telemetry.buffered", "Telemetry sample persisted.", new { count = buffer.Count }, ct);
            await _logger.LogAsync(sample.TelemetryErrorsCount > 0 ? "telemetry.sample.partial" : "telemetry.sample.completed",
                "Telemetry sample finished.", new { sample.CollectionDurationMs, sample.AgentWorkingSetBytes, sample.TelemetryErrorsCount }, ct,
                sample.TelemetryErrorsCount > 0 ? "warning" : "info");
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { }
        catch (Exception ex)
        {
            budget.Cancel();
            await _logger.LogAsync("telemetry.sample.skipped", "Telemetry sample did not complete.",
                new { reason = ex is TimeoutException or OperationCanceledException ? "timeout" : "collection_failed", error_type = ex.GetType().Name }, ct, "warning");
        }
    }

    internal async Task FlushOnceAsync(AgentConfig config, TelemetryBuffer buffer, CancellationToken ct)
    {
        IReadOnlyList<TelemetrySample> pending = buffer.Snapshot(24);
        if (pending.Count == 0) return;
        TelemetryBatch batch = new() { MachineId = config.MachineId, Samples = pending.ToList() };
        int bytes = JsonSerializer.SerializeToUtf8Bytes(batch, new JsonSerializerOptions(JsonSerializerDefaults.Web)).Length;
        await _logger.LogAsync("telemetry.flush.started", "Telemetry batch sending.", new { sample_count = pending.Count, serialized_payload_size_bytes = bytes }, ct);
        try
        {
            await _api.PostTelemetryAsync(config, batch, ct);
            buffer.Acknowledge(pending.Select(item => item.SampleId));
            await _logger.LogAsync("telemetry.flush.completed", "Telemetry batch accepted.", new { sample_count = pending.Count, serialized_payload_size_bytes = bytes }, ct);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { }
        catch (Exception ex)
        {
            await _logger.LogAsync("telemetry.flush.failed", "Telemetry batch retained for retry.",
                new { sample_count = pending.Count, error_type = ex.GetType().Name }, ct, "warning");
        }
    }
}
