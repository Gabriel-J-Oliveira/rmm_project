using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using System.Threading.Channels;
using System.Security.Cryptography;
using System.Text;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

internal sealed class ContinuousTelemetryPipeline
{
    private readonly AgentApiClient _api;
    private readonly JsonlLogger _logger;
    private readonly Func<AgentConfig, CancellationToken, NativeReading> _read;
    private readonly Func<string, TelemetryWindowQueue>? _queue;
    private readonly TimeSpan _samplePeriod;
    private readonly TimeSpan _uploadPeriod;
    private readonly TimeSpan _readBudget;
    private readonly Func<DateTimeOffset> _now;
    private readonly ConcurrentQueue<NativeReading> _recent = new();
    private readonly object _runGate = new();
    private Task<NativeReading>? _activeRead;
    private bool _running;
    private Task? _writerTask;
    private string? _machine;
    internal long DroppedWindows { get; private set; }
    internal long ReadTimeouts { get; private set; }
    internal long ReadFailures { get; private set; }
    internal long TransmittedBytes { get; private set; }
    internal long UploadFailures { get; private set; }
    internal long LiveFailures { get; private set; }
    internal long ControlFailures { get; private set; }
    internal ContinuousTelemetryPipeline(AgentApiClient api, JsonlLogger logger,
        Func<AgentConfig, CancellationToken, NativeReading> read, Func<string, TelemetryWindowQueue>? queue = null,
        TimeSpan? samplePeriod = null, TimeSpan? uploadPeriod = null, TimeSpan? readBudget = null, Func<DateTimeOffset>? now = null)
    {
        _api = api; _logger = logger; _read = read;
        _queue = queue;
        _samplePeriod = samplePeriod ?? TimeSpan.FromSeconds(10);
        _uploadPeriod = uploadPeriod ?? TimeSpan.FromSeconds(60);
        _readBudget = readBudget ?? TimeSpan.FromSeconds(5);
        _now = now ?? (() => DateTimeOffset.UtcNow);
    }

    internal async Task RunAsync(AgentConfig config, CancellationToken ct, Action? ready)
    {
        lock (_runGate) { if (_running || _writerTask is { IsCompleted: false }) throw new InvalidOperationException("Telemetry already owns the sampler."); _running = true; }
        using CancellationTokenSource stop = CancellationTokenSource.CreateLinkedTokenSource(ct);
        if (_machine != config.MachineId) { while (_recent.TryDequeue(out _)) { } _machine = config.MachineId; }
        Channel<TelemetryWindow> completed = Channel.CreateBounded<TelemetryWindow>(new BoundedChannelOptions(16) { SingleReader = true, SingleWriter = true });
        try
        {
            // No durable-queue load or HTTP is allowed on the sampling path.
            Task writer = Task.Run(async () =>
            {
                string identity = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(config.MachineId)))[..24];
                string directory = Path.Combine(Path.GetDirectoryName(config.StatePath)!, "telemetry-windows-v2", identity);
                TelemetryWindowQueue queue = _queue is null
                    ? new(directory, Math.Clamp(config.TelemetryWindowMaxBytes, 1024 * 1024, 512L * 1024 * 1024),
                        TimeSpan.FromHours(Math.Clamp(config.TelemetryWindowMaxAgeHours, 1, 192))) : _queue(directory);
                ready?.Invoke();
                Task upload = UploadAsync(config, queue, stop.Token);
                try
                {
                    await foreach (TelemetryWindow window in completed.Reader.ReadAllAsync())
                    {
                        try
                        {
                            queue.Enqueue(window);
                            if (queue.LostWindows > 0 || queue.CorruptWindows > 0)
                                await Log("telemetry.coverage.loss", new { queue.LostWindows, queue.CorruptWindows });
                        }
                        catch (Exception) { DroppedWindows++; await Log("telemetry.window.persist_failed", new { DroppedWindows }); }
                    }
                }
                finally { stop.Cancel(); await upload; }
            }, CancellationToken.None);
            _writerTask = writer;
            TelemetryMinuteAggregator aggregator = new(config.MachineId);
            try
            {
                while (!ct.IsCancellationRequested && !writer.IsCompleted)
                {
                    long iteration = Stopwatch.GetTimestamp();
                    DateTimeOffset now = _now();
                    TelemetryWindow? window = aggregator.Advance(now);
                    if (window is not null && !completed.Writer.TryWrite(window))
                    {
                        DroppedWindows++; _ = Log("telemetry.window.channel_full", new { DroppedWindows });
                    }
                    if (_activeRead is null || _activeRead.IsCompleted)
                    {
                        if (_activeRead?.IsFaulted == true) _ = _activeRead.Exception;
                        using CancellationTokenSource budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                        budget.CancelAfter(_readBudget);
                        CancellationToken token = budget.Token;
                        _activeRead = Task.Run(() => _read(config, token), token);
                        try
                        {
                            NativeReading reading = await _activeRead.WaitAsync(_readBudget, ct);
                            reading.Metrics["agent.transmitted_bytes_total"] = TransmittedBytes;
                            reading.Metrics["agent.read_timeouts_total"] = ReadTimeouts;
                            reading.Metrics["agent.dropped_windows_total"] = DroppedWindows;
                            reading.Metrics["agent.read_failures_total"] = ReadFailures;
                            reading.Metrics["agent.clock_discarded_windows_total"] = aggregator.ClockDiscardedWindows;
                            reading.Metrics["agent.upload_failures_total"] = UploadFailures;
                            reading.Metrics["agent.live_failures_total"] = LiveFailures;
                            reading.Metrics["agent.control_failures_total"] = ControlFailures;
                            if (TelemetryMinuteAggregator.Minute(reading.CollectedAt) == TelemetryMinuteAggregator.Minute(now))
                            {
                                aggregator.Add(reading);
                                _recent.Enqueue(reading);
                                while (_recent.TryPeek(out NativeReading? old) && (old.CollectedAt < now.AddMinutes(-30) || _recent.Count > 180)) _recent.TryDequeue(out _);
                            }
                        }
                        catch (OperationCanceledException) when (ct.IsCancellationRequested) { break; }
                        catch (Exception error)
                        {
                            budget.Cancel();
                            if (error is TimeoutException or OperationCanceledException) ReadTimeouts++; else ReadFailures++;
                            _ = Log("telemetry.read.incomplete", new { ReadTimeouts, ReadFailures });
                        }
                        _ = _activeRead.ContinueWith(task => _ = task.Exception, CancellationToken.None, TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
                    }
                    TimeSpan delay = _samplePeriod - Stopwatch.GetElapsedTime(iteration);
                    await Task.Delay(delay > TimeSpan.Zero ? delay : TimeSpan.FromMilliseconds(1), ct);
                }
            }
            finally
            {
                completed.Writer.TryComplete();
                // Only closed minute windows are durable. The open partial minute is explicitly volatile.
                await Log("telemetry.stop.volatile_tail", new { raw_buffer_volatile = true, open_minute_may_be_lost = true });
                stop.Cancel();
                try { await writer.WaitAsync(TimeSpan.FromSeconds(5)); }
                catch (OperationCanceledException) { }
            }
        }
        finally { lock (_runGate) _running = false; }
    }

    private async Task UploadAsync(AgentConfig config, TelemetryWindowQueue queue, CancellationToken ct)
    {
        DateTimeOffset nextSummary = _now(), nextControl = _now();
        Guid? sessionId = null;
        DateTimeOffset expires = DateTimeOffset.MinValue;
        Guid? uploadedSession = null;
        HashSet<Guid> liveAcknowledged = new();
        int failures = 0;
        while (!ct.IsCancellationRequested)
        {
            DateTimeOffset now = _now();
            if (now >= expires) sessionId = null;
            if (now >= nextControl)
            {
                nextControl = now.AddSeconds(sessionId.HasValue ? 10 : 30);
                try
                {
                    using CancellationTokenSource budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    budget.CancelAfter(TimeSpan.FromSeconds(5));
                    JsonElement control = await _api.TelemetryControlAsync(config, budget.Token);
                    sessionId = control.TryGetProperty("session_id", out var id) && id.ValueKind == JsonValueKind.String && Guid.TryParse(id.GetString(), out Guid parsed) ? parsed : null;
                    if (sessionId.HasValue && control.TryGetProperty("expires_at", out var at) && DateTimeOffset.TryParse(at.GetString(), out DateTimeOffset until))
                        expires = until < now.AddMinutes(15) ? until : now.AddMinutes(15);
                    else { sessionId = null; expires = DateTimeOffset.MinValue; }
                }
                catch (Exception error)
                {
                    ControlFailures++;
                    await Log("telemetry.control.failed", new { ControlFailures, error_type = error.GetType().Name });
                }
            }
            if (sessionId.HasValue && now < expires)
            {
                if (uploadedSession != sessionId) { liveAcknowledged.Clear(); uploadedSession = sessionId; }
                NativeReading[] recent = _recent.ToArray();
                liveAcknowledged.IntersectWith(recent.Select(item => item.SampleId));
                // Prioritize fresh data, then backfill the still-available ring without re-sampling.
                NativeReading[] points = recent.Where(item => !liveAcknowledged.Contains(item.SampleId))
                    .OrderByDescending(item => item.CollectedAt).Take(12).OrderBy(item => item.CollectedAt).ToArray();
                while (points.Length > 1 && JsonSerializer.SerializeToUtf8Bytes(points).Length > 100 * 1024) points = points[1..];
                if (points.Length > 0)
                    try
                    {
                        using CancellationTokenSource budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                        budget.CancelAfter(TimeSpan.FromSeconds(5));
                        await _api.TelemetryLiveAsync(config, new { machine_id = config.MachineId, session_id = sessionId, points }, budget.Token);
                        TransmittedBytes += JsonSerializer.SerializeToUtf8Bytes(points).Length;
                        foreach (NativeReading point in points) liveAcknowledged.Add(point.SampleId);
                    }
                    catch (Exception error)
                    {
                        LiveFailures++;
                        await Log("telemetry.live.failed", new { LiveFailures, error_type = error.GetType().Name });
                    }
            }
            if (now >= nextSummary)
            {
                nextSummary = now + _uploadPeriod;
                try
                {
                    IReadOnlyList<TelemetryWindow> windows = queue.Snapshot(now);
                    if (windows.Count > 0)
                    {
                        object batch = new { schema_version = 2, machine_id = config.MachineId, windows };
                        using CancellationTokenSource budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                        budget.CancelAfter(TimeSpan.FromSeconds(10));
                        Guid[] accepted = await _api.TelemetryWindowsAsync(config, batch, budget.Token);
                        queue.Acknowledge(windows, accepted);
                        TransmittedBytes += JsonSerializer.SerializeToUtf8Bytes(batch).Length;
                        failures = 0;
                        nextSummary = now.AddSeconds(windows.Count == 24 ? 5 : _uploadPeriod.TotalSeconds);
                    }
                }
                catch (Exception error)
                {
                    UploadFailures++;
                    failures = Math.Min(failures + 1, 6);
                    nextSummary = now.AddSeconds(Math.Min(300, Math.Pow(2, failures) * 5));
                    await Log("telemetry.upload.failed", new { UploadFailures, next_retry_at = nextSummary, error_type = error.GetType().Name });
                }
            }
            try { await Task.Delay(_uploadPeriod < TimeSpan.FromSeconds(5) ? _uploadPeriod : TimeSpan.FromSeconds(5), ct); }
            catch (OperationCanceledException) { break; }
        }
    }

    private async Task Log(string name, object data)
    {
        using CancellationTokenSource budget = new(TimeSpan.FromSeconds(1));
        try { await _logger.LogAsync(name, "Continuous telemetry state.", data, budget.Token); }
        catch (Exception) { }
    }
}
