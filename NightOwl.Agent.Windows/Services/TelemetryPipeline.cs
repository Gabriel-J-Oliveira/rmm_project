using System.Diagnostics;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class TelemetryPipeline
{
    private readonly TelemetryCollector _collector;
    private readonly AgentApiClient _api;
    private readonly JsonlLogger _logger;
    private readonly Func<string, int, TimeSpan, TelemetryBuffer> _bufferFactory;
    private readonly string _bufferPath;
    private readonly TimeSpan _collectionBudget;
    private readonly TimeSpan _startupRetryDelay;
    private readonly TimeSpan _pollDelay;
    private readonly TimeSpan _shutdownGrace;
    private readonly object _collectionGate = new();
    private Task<TelemetrySample>? _collectionTask;
    internal bool CollectionActive { get { lock (_collectionGate) return _collectionTask is { IsCompleted: false }; } }

    public TelemetryPipeline(TelemetryCollector collector, AgentApiClient api, JsonlLogger logger)
        : this(collector, api, logger, (path, count, age) => new TelemetryBuffer(path, count, age),
            Path.Combine(NightOwlPaths.Current.StateDir, "telemetry-buffer.json"),
            TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(2))
    {
    }

    internal TelemetryPipeline(TelemetryCollector collector, AgentApiClient api, JsonlLogger logger,
        Func<string, int, TimeSpan, TelemetryBuffer> bufferFactory, string bufferPath,
        TimeSpan collectionBudget, TimeSpan startupRetryDelay, TimeSpan pollDelay, TimeSpan shutdownGrace)
    {
        _collector = collector;
        _api = api;
        _logger = logger;
        _bufferFactory = bufferFactory;
        _bufferPath = bufferPath;
        _collectionBudget = collectionBudget;
        _startupRetryDelay = startupRetryDelay;
        _pollDelay = pollDelay;
        _shutdownGrace = shutdownGrace;
    }

    public async Task RunAsync(AgentConfig config, CancellationToken ct, Action? ready = null)
    {
        if (!config.TelemetryEnabled) return;
        try
        {
            TelemetryBuffer buffer = await LoadBufferWithRetryAsync(config, ct);
            ready?.Invoke();
            DateTimeOffset nextSample = DateTimeOffset.UtcNow;
            DateTimeOffset nextFlush = DateTimeOffset.UtcNow.Add(
                buffer.Count >= 24 ? TimeSpan.FromMinutes(1) : TimeSpan.FromSeconds(config.TelemetryFlushSeconds));
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
                    if (now >= nextFlush)
                    {
                        if (buffer.Count > 0)
                        {
                            bool fullBatch = buffer.Count >= 24;
                            bool delivered = await FlushOnceAsync(config, buffer, ct);
                            nextFlush = now.Add(NextFlushDelay(config.TelemetryFlushSeconds, delivered, fullBatch && buffer.Count > 0));
                        }
                        else
                        {
                            nextFlush = now.AddSeconds(config.TelemetryFlushSeconds);
                        }
                    }
                    await Task.Delay(_pollDelay, ct);
                }
                catch (OperationCanceledException) when (ct.IsCancellationRequested) { break; }
                catch (Exception ex) when (ex is JsonException or InvalidDataException)
                {
                    buffer = await LoadBufferWithRetryAsync(config, ct);
                }
                catch (Exception)
                {
                    await LogBestEffortAsync("telemetry.sample.skipped", "Telemetry loop deferred after local failure.",
                        new { reason = "local_io_failure" }, ct);
                    try { await Task.Delay(_startupRetryDelay, ct); }
                    catch (OperationCanceledException) when (ct.IsCancellationRequested) { break; }
                }
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { }
        finally { await AwaitOutstandingCollectionAsync(); }
    }

    internal async Task<TelemetryBuffer> LoadBufferWithRetryAsync(AgentConfig config, CancellationToken ct)
    {
        while (true)
        {
            ct.ThrowIfCancellationRequested();
            try
            {
                return _bufferFactory(_bufferPath, config.TelemetryBufferMaxSamples,
                    TimeSpan.FromHours(config.TelemetryBufferMaxAgeHours));
            }
            catch (Exception ex) when (ex is JsonException or InvalidDataException)
            {
                bool quarantined = false;
                try
                {
                    if (File.Exists(_bufferPath))
                    {
                        string directory = Path.Combine(Path.GetDirectoryName(_bufferPath)!, "quarantine");
                        Directory.CreateDirectory(directory);
                        File.Move(_bufferPath, Path.Combine(directory,
                            $"telemetry-buffer-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}-{Guid.NewGuid():N}.json"));
                        quarantined = true;
                    }
                }
                catch (Exception) { /* Keep the source untouched and retry if quarantine is unavailable. */ }
                await LogBestEffortAsync("telemetry.buffer.corrupt", "Corrupt telemetry buffer quarantined or retained for retry.",
                    new { error_code = "TELEMETRY_BUFFER_CORRUPT", quarantined }, ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                await LogBestEffortAsync("telemetry.buffer.failed", "Telemetry buffer unavailable; retry scheduled.",
                    new { error_code = "TELEMETRY_BUFFER_LOAD_FAILED", error_type = ex.GetType().Name }, ct);
            }
            await Task.Delay(_startupRetryDelay, ct);
        }
    }

    internal static TimeSpan NextFlushDelay(int flushSeconds, bool delivered, bool backlog)
        => delivered && backlog ? TimeSpan.FromMinutes(1) : TimeSpan.FromSeconds(flushSeconds);

    private async Task LogBestEffortAsync(string eventType, string message, object data, CancellationToken ct)
    {
        try { await _logger.LogAsync(eventType, message, data, ct, "warning"); }
        catch (Exception) { /* Telemetry logging must not affect the agent worker. */ }
    }

    private async Task AwaitOutstandingCollectionAsync()
    {
        Task<TelemetrySample>? task;
        lock (_collectionGate) task = _collectionTask;
        if (task is null) return;
        try { await task.WaitAsync(_shutdownGrace); }
        catch (TimeoutException)
        {
            await LogBestEffortAsync("telemetry.shutdown.deferred", "Collection did not stop within shutdown grace.",
                new { grace_ms = _shutdownGrace.TotalMilliseconds }, CancellationToken.None);
        }
        catch (Exception) { _ = task.Exception; }
    }

    internal async Task CollectOnceAsync(AgentConfig config, TelemetryBuffer buffer, CancellationToken ct)
    {
        Task<TelemetrySample> task;
        CancellationTokenSource budget;
        lock (_collectionGate)
        {
            if (_collectionTask is { IsCompleted: false })
            {
                task = _collectionTask;
                budget = null!;
            }
            else
            {
                if (_collectionTask is { IsFaulted: true }) _ = _collectionTask.Exception;
                budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                budget.CancelAfter(_collectionBudget);
                CancellationToken collectionToken = budget.Token;
                task = Task.Run(() => _collector.Collect(config.MachineId, collectionToken), collectionToken);
                _ = task.ContinueWith(static completed => _ = completed.Exception,
                    CancellationToken.None, TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously,
                    TaskScheduler.Default);
                _collectionTask = task;
            }
        }
        if (budget is null)
        {
            await _logger.LogAsync("telemetry.sample.skipped", "Previous telemetry collection remains active.", new { reason = "previous_collection_active" }, ct, "warning");
            return;
        }
        using (budget)
        try
        {
            await _logger.LogAsync("telemetry.sample.started", "Telemetry collection started.", null, ct);
            TelemetrySample sample = await task.WaitAsync(_collectionBudget, ct);
            int discarded = buffer.Add(sample);
            if (discarded > 0)
                await _logger.LogAsync("telemetry.buffer.overflow", "Old telemetry samples discarded.", new { discarded }, ct, "warning");
            await _logger.LogAsync("telemetry.buffered", "Telemetry sample persisted.", new { count = buffer.Count }, ct);
            await _logger.LogAsync(sample.TelemetryErrorsCount > 0 ? "telemetry.sample.partial" : "telemetry.sample.completed",
                "Telemetry sample finished.", new { sample.CollectionDurationMs, sample.AgentWorkingSetBytes, sample.TelemetryErrorsCount }, ct,
                sample.TelemetryErrorsCount > 0 ? "warning" : "info");
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { budget.Cancel(); }
        catch (Exception ex)
        {
            budget.Cancel();
            await _logger.LogAsync("telemetry.sample.skipped", "Telemetry sample did not complete.",
                new { reason = ex is TimeoutException or OperationCanceledException ? "timeout" : "collection_failed", error_type = ex.GetType().Name }, ct, "warning");
        }
    }

    internal async Task<bool> FlushOnceAsync(AgentConfig config, TelemetryBuffer buffer, CancellationToken ct)
    {
        IReadOnlyList<TelemetrySample> pending = buffer.Snapshot(24);
        if (pending.Count == 0) return false;
        TelemetryBatch batch = new() { MachineId = config.MachineId, Samples = pending.ToList() };
        int bytes = JsonSerializer.SerializeToUtf8Bytes(batch, new JsonSerializerOptions(JsonSerializerDefaults.Web)).Length;
        await _logger.LogAsync("telemetry.flush.started", "Telemetry batch sending.", new { sample_count = pending.Count, serialized_payload_size_bytes = bytes }, ct);
        try
        {
            await _api.PostTelemetryAsync(config, batch, ct);
            buffer.Acknowledge(pending.Select(item => item.SampleId));
            await _logger.LogAsync("telemetry.flush.completed", "Telemetry batch accepted.", new { sample_count = pending.Count, serialized_payload_size_bytes = bytes }, ct);
            return true;
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { return false; }
        catch (Exception ex)
        {
            await _logger.LogAsync("telemetry.flush.failed", "Telemetry batch retained for retry.",
                new { sample_count = pending.Count, error_type = ex.GetType().Name }, ct, "warning");
            return false;
        }
    }
}
