using System.Diagnostics;
using System.Net;
using System.Text;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class ContinuousTelemetryTests
{
    private static void Check(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    internal static async Task RunAsync()
    {
        Aggregation(); QueueRecovery(); Network();
        await SlowUploadDoesNotBlockSampler();
        await LateReadCannotOverlap();
        await LiveUsesSameSampler();
        Console.WriteLine("PASS continuous telemetry: two-minute peak, UTC boundaries, weighted latency, missing samples, queue/restart/ACK/corruption/limits, reset, uploader isolation, timeout ownership.");
    }
    private static NativeReading Reading(DateTimeOffset at, double cpu = 97) => new()
    {
        CollectedAt = at, IntervalSeconds = 10,
        Metrics = new() { ["cpu.usage_percent"] = cpu, ["memory.used_percent"] = 60 },
    };
    private static List<TelemetryWindow> Windows(DateTimeOffset at)
    {
        TelemetryMinuteAggregator aggregator = new("synthetic");
        List<TelemetryWindow> windows = new();
        for (int index = 0; index <= 12; index++)
        {
            DateTimeOffset now = at.AddSeconds(index * 10);
            TelemetryWindow? window = aggregator.Advance(now);
            if (window is not null) windows.Add(window);
            aggregator.Add(Reading(now));
        }
        return windows;
    }
    private static void Aggregation()
    {
        DateTimeOffset at = DateTimeOffset.Parse("2026-10-29T12:00:00Z");
        List<TelemetryWindow> windows = Windows(at);
        Check(windows.Count == 2 && windows.All(window => window.ReceivedSamples == 6 && window.Metrics["cpu.usage_percent"].Min == 97 && window.Metrics["cpu.usage_percent"].Max == 97), "A two-minute CPU peak was erased.");
        Check(windows.Select(window => window.WindowId).SequenceEqual(Windows(at).Select(window => window.WindowId)), "Window IDs must be deterministic.");
        MetricWindow latency = new();
        latency.Add(10, 100, false); latency.Add(100, 1, false);
        Check(Math.Abs(latency.WeightedSum / latency.Weight - 1100d / 101) < .0001, "Latency must be operation-weighted.");
        TelemetryMinuteAggregator partial = new("synthetic");
        partial.Add(Reading(at)); partial.Add(Reading(at.AddSeconds(10))); partial.Add(Reading(at.AddSeconds(10)));
        TelemetryWindow incomplete = partial.Advance(at.AddHours(2))!;
        Check(incomplete.ReceivedSamples == 2 && (int)incomplete.Quality["missing_samples"]! == 4 && partial.Advance(at.AddHours(3)) is null,
            "Suspension/duplicate samples must not create fake windows.");
        partial.Add(Reading(at.AddHours(3)));
        partial.Advance(at.AddMinutes(-1));
        Check(partial.ClockDiscardedWindows == 1, "Backwards UTC adjustment must discard an uncertain open window explicitly.");
        partial.Add(Reading(at.AddMinutes(-1)));
        TelemetryMinuteAggregator invalid = new("synthetic-invalid");
        NativeReading bad = Reading(at);
        bad.Metrics["cpu.usage_percent"] = double.PositiveInfinity;
        invalid.Add(bad);
        TelemetryWindow safe = invalid.Advance(at.AddMinutes(1))!;
        Check(!safe.Metrics.ContainsKey("cpu.usage_percent") && safe.Metrics.ContainsKey("memory.used_percent"), "One invalid counter poisoned healthy metrics.");
    }
    private static void QueueRecovery()
    {
        string directory = Path.Combine(Path.GetTempPath(), "nightowl-v2-queue-" + Guid.NewGuid().ToString("N"));
        try
        {
            DateTimeOffset at = TelemetryMinuteAggregator.Minute(DateTimeOffset.UtcNow).AddMinutes(-3);
            TelemetryWindow[] windows = Windows(at).ToArray();
            TelemetryWindowQueue queue = new(directory);
            foreach (TelemetryWindow window in windows) queue.Enqueue(window);
            queue = new(directory);
            Check(queue.Snapshot(DateTimeOffset.UtcNow).Count == 2, "Closed windows did not survive restart.");
            queue.Enqueue(windows[0]);
            queue.Acknowledge(windows, new[] { Guid.NewGuid() });
            Check(queue.Snapshot(DateTimeOffset.UtcNow).Count == 2, "A lost/wrong ACK deleted a window.");
            queue.Acknowledge(windows, new[] { windows[0].WindowId });
            Check(queue.Snapshot(DateTimeOffset.UtcNow).Count == 1, "Partial ACK removed unsent data.");
            File.WriteAllText(Path.Combine(directory, "broken.json"), "{");
            Check(queue.Snapshot(DateTimeOffset.UtcNow).Count == 1 && queue.CorruptWindows == 1, "Partial corruption blocked valid windows.");
            TelemetryWindow damaged = Windows(at.AddMinutes(-5))[0];
            damaged.Metrics["cpu.usage_percent"].Count = 0;
            File.WriteAllText(Path.Combine(directory,$"{damaged.StartAt:yyyyMMddTHHmmssZ}-{damaged.WindowId:N}.json"),JsonSerializer.Serialize(damaged));
            Check(queue.Snapshot(DateTimeOffset.UtcNow).Count == 1 && queue.CorruptWindows == 2, "Semantically corrupt JSON poisoned the queue.");
            damaged = Windows(at.AddMinutes(-5))[0];
            damaged.Metrics["cpu.usage_percent"].Distribution["95"] = 1;
            File.WriteAllText(Path.Combine(directory,$"{damaged.StartAt:yyyyMMddTHHmmssZ}-{damaged.WindowId:N}.json"),JsonSerializer.Serialize(damaged));
            Check(queue.Snapshot(DateTimeOffset.UtcNow).Count == 1 && queue.CorruptWindows == 3, "Corrupt histogram blocked delivery of healthy windows.");
            TelemetryWindowQueue limited = new(directory, 1024);
            foreach (TelemetryWindow window in windows) limited.Enqueue(window);
            limited.Enqueue(Windows(at.AddMinutes(-3))[0]);
            _ = limited.Snapshot(DateTimeOffset.UtcNow);
            Check(limited.LostWindows > 0, "Storage cap must record explicit coverage loss.");
            queue.Snapshot(DateTimeOffset.UtcNow.AddDays(10));
            Check(queue.Snapshot(DateTimeOffset.UtcNow.AddDays(10)).Count == 0, "Expired windows must not poison replay.");
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
    }
    private static void Network()
    {
        var rates = ContinuousNativeReader.Rates(100, 200, 1100, 2200, 10);
        Check(rates == (100d, 200d), "Network rates must use elapsed seconds.");
        Check(ContinuousNativeReader.Rates(100, 200, 5, 6, 10) == (null, null), "Counter reset became traffic.");
        Check(ContinuousNativeReader.Rates(0, 0, 100, 100, 600) == (null, null), "Resume gap became a valid rate.");
    }
    private static async Task SlowUploadDoesNotBlockSampler()
    {
        string directory = Path.Combine(Path.GetTempPath(), "nightowl-v2-slow-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        using CancellationTokenSource stop = new(TimeSpan.FromSeconds(10));
        TaskCompletionSource entered = new(TaskCreationOptions.RunContinuationsAsynchronously);
        FakeFactory factory = new(async (request, token) =>
        {
            if (request.Method == HttpMethod.Get) return Json("{\"session_id\":null}");
            entered.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, token);
            return Json("{}");
        });
        long ticks = TelemetryMinuteAggregator.Minute(DateTimeOffset.UtcNow).AddMinutes(-5).Ticks;
        int samples = 0;
        ContinuousTelemetryPipeline pipeline = new(new AgentApiClient(factory), new JsonlLogger(Path.Combine(directory, "log.jsonl")), (config, token) =>
        {
            samples++;
            return Reading(new DateTimeOffset(Interlocked.Add(ref ticks, TimeSpan.FromSeconds(10).Ticks), TimeSpan.Zero));
        }, samplePeriod:TimeSpan.FromMilliseconds(30), uploadPeriod:TimeSpan.FromMilliseconds(30), now:() => new DateTimeOffset(Interlocked.Read(ref ticks), TimeSpan.Zero));
        AgentConfig config = new() { MachineId = Guid.NewGuid().ToString(), AgentToken="synthetic", ServerBaseUrl="https://isolated.invalid", StatePath=Path.Combine(directory, "state.json") };
        Task running = pipeline.RunAsync(config, stop.Token, null);
        try
        {
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
            int before = samples;
            await Task.Delay(250);
            Check(samples > before + 3, "Slow HTTP blocked the sampler.");
        }
        finally
        {
            stop.Cancel();
            try { await running; } catch (OperationCanceledException) { }
            Directory.Delete(directory, true);
        }
    }
    private static async Task LateReadCannotOverlap()
    {
        string directory = Path.Combine(Path.GetTempPath(), "nightowl-v2-read-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        using ManualResetEventSlim release = new();
        using CancellationTokenSource stop = new();
        int calls = 0;
        ContinuousTelemetryPipeline pipeline = new(new AgentApiClient(new FakeFactory((_, _) => Task.FromResult(Json("{\"session_id\":null}")))) ,
            new JsonlLogger(Path.Combine(directory, "log.jsonl")), (_, _) => { Interlocked.Increment(ref calls); release.Wait(); return Reading(DateTimeOffset.UtcNow); },
            samplePeriod:TimeSpan.FromMilliseconds(25), uploadPeriod:TimeSpan.FromMilliseconds(50), readBudget:TimeSpan.FromMilliseconds(40));
        Task run = pipeline.RunAsync(new() { MachineId="synthetic", StatePath=Path.Combine(directory,"state.json"), ServerBaseUrl="https://isolated.invalid" }, stop.Token, null);
        try { await Task.Delay(250); Check(calls == 1 && pipeline.ReadTimeouts == 1, "Timed out native call released sampling ownership."); }
        finally
        {
            stop.Cancel(); release.Set();
            try { await run; } catch (OperationCanceledException) { }
            Directory.Delete(directory, true);
        }
    }
    private static async Task LiveUsesSameSampler()
    {
        string directory = Path.Combine(Path.GetTempPath(), "nightowl-v2-live-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        using CancellationTokenSource stop = new();
        System.Collections.Concurrent.ConcurrentDictionary<Guid, bool> generated = new();
        TaskCompletionSource received = new(TaskCreationOptions.RunContinuationsAsynchronously);
        Guid sid = Guid.NewGuid();
        FakeFactory factory = new(async (request, token) =>
        {
            if (request.Method == HttpMethod.Get)
                return Json(JsonSerializer.Serialize(new { session_id=sid, expires_at=DateTimeOffset.UtcNow.AddSeconds(30) }));
            using JsonDocument body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            if (request.RequestUri!.AbsolutePath.EndsWith("live/", StringComparison.Ordinal))
            {
                foreach (JsonElement point in body.RootElement.GetProperty("points").EnumerateArray())
                    Check(generated.ContainsKey(point.GetProperty("sample_id").GetGuid()), "Live mode created a separate sample.");
                received.TrySetResult();
                return Json("{\"status\":\"ok\"}");
            }
            return Json(JsonSerializer.Serialize(new { accepted_window_ids=body.RootElement.GetProperty("windows").EnumerateArray().Select(window => window.GetProperty("window_id").GetGuid()).ToArray() }));
        });
        ContinuousTelemetryPipeline pipeline = new(new AgentApiClient(factory), new JsonlLogger(Path.Combine(directory,"log.jsonl")), (_, _) =>
        {
            NativeReading reading = Reading(DateTimeOffset.UtcNow);
            generated[reading.SampleId] = true;
            return reading;
        }, samplePeriod:TimeSpan.FromMilliseconds(40), uploadPeriod:TimeSpan.FromMilliseconds(50));
        Task running = pipeline.RunAsync(new() { MachineId=Guid.NewGuid().ToString(), StatePath=Path.Combine(directory,"state.json"), ServerBaseUrl="https://isolated.invalid" }, stop.Token, null);
        try { await received.Task.WaitAsync(TimeSpan.FromSeconds(5)); Check(generated.Count > 0, "Live upload did not use sampler output."); }
        finally
        {
            stop.Cancel();
            try { await running; } catch (OperationCanceledException) { }
            Directory.Delete(directory,true);
        }
    }
    internal static async Task ProtocolE2E()
    {
        AgentConfig config = new() { MachineId=Environment.GetEnvironmentVariable("NIGHTOWL_TEST_MACHINE")!, AgentToken=Environment.GetEnvironmentVariable("NIGHTOWL_TEST_TOKEN")!, ServerBaseUrl=Environment.GetEnvironmentVariable("NIGHTOWL_TEST_SERVER")! };
        Check(config.ServerBaseUrl.StartsWith("http://localhost:", StringComparison.Ordinal) || config.ServerBaseUrl.StartsWith("http://127.0.0.1:", StringComparison.Ordinal), "E2E is restricted to isolated loopback.");
        AgentApiClient api = new(new FakeFactory(null));
        JsonElement control = await api.TelemetryControlAsync(config, CancellationToken.None);
        Guid session = control.GetProperty("session_id").GetGuid();
        List<TelemetryWindow> windows = Windows(TelemetryMinuteAggregator.Minute(DateTimeOffset.UtcNow).AddMinutes(-3));
        Guid[] accepted = await api.TelemetryWindowsAsync(config, new { schema_version=2, machine_id=config.MachineId, windows }, CancellationToken.None);
        Check(accepted.Length == 2, "Backend did not ACK real .NET windows.");
        await api.TelemetryLiveAsync(config, new { machine_id=config.MachineId, session_id=session,
            points=new[] { Reading(DateTimeOffset.UtcNow.AddSeconds(-10)), Reading(DateTimeOffset.UtcNow) } }, CancellationToken.None);
        Console.WriteLine("PASS: real .NET-to-Django isolated summary/control/live protocol, no endpoint operations.");
    }
    internal static async Task NativeCost(bool minute = false)
    {
        if (!OperatingSystem.IsWindows()) { Console.WriteLine("NOT_TESTED: Windows native counters unavailable."); return; }
        using ContinuousNativeReader reader = new(new TelemetryCollector());
        long beforePs = WindowsInventoryCollector.PowerShellStarted;
        List<double> times = new();
        int disk = 0, cpu = 0, memory = 0;
        long bytes = 0;
        using Process harness = Process.GetCurrentProcess();
        TimeSpan cpuBefore = harness.TotalProcessorTime;
        Stopwatch totalTime = Stopwatch.StartNew();
        long maximumWorkingSet = 0;
        double maxNormalizedCpu = 0;
        TelemetryMinuteAggregator aggregator = new("synthetic-local-benchmark");
        List<TelemetryWindow> completed = new();
        int reads = minute ? 7 : 3;
        for (int index = 0; index < reads; index++)
        {
            Stopwatch watch = Stopwatch.StartNew();
            TimeSpan cpuStart = harness.TotalProcessorTime;
            NativeReading point = reader.Read("synthetic-local-benchmark", CancellationToken.None);
            TelemetryWindow? closed = aggregator.Advance(point.CollectedAt);
            if (closed is not null) completed.Add(closed);
            aggregator.Add(point);
            times.Add(watch.Elapsed.TotalMilliseconds);
            maxNormalizedCpu = Math.Max(maxNormalizedCpu, TelemetryCollector.CpuPercent(harness.TotalProcessorTime-cpuStart, watch.Elapsed, Environment.ProcessorCount) ?? 0);
            harness.Refresh(); maximumWorkingSet = Math.Max(maximumWorkingSet, harness.WorkingSet64);
            disk += point.Metrics.Keys.Count(key => key.StartsWith("disk.", StringComparison.Ordinal));
            cpu += point.Metrics.ContainsKey("cpu.usage_percent") ? 1 : 0;
            memory += point.Metrics.ContainsKey("memory.used_percent") ? 1 : 0;
            bytes += JsonSerializer.SerializeToUtf8Bytes(point).Length;
            await Task.Delay(minute ? 10000 : 1100);
        }
        TelemetryWindow? final = aggregator.Advance(DateTimeOffset.UtcNow);
        if (final is not null) completed.Add(final);
        Check(memory > 0 && cpu > 0 && WindowsInventoryCollector.PowerShellStarted == beforePs, "Native sampler regression.");
        double normalizedCpu = TelemetryCollector.CpuPercent(harness.TotalProcessorTime-cpuBefore,totalTime.Elapsed,Environment.ProcessorCount) ?? 0;
        int maximumWindowBytes = completed.Count > 0 ? completed.Max(window => JsonSerializer.SerializeToUtf8Bytes(window).Length) : 0;
        Console.WriteLine($"MEASURED_LOCAL_WINDOWS: reads={reads} duration_avg_ms={times.Average():F2} duration_max_ms={times.Max():F2} harness_cpu_avg_percent={normalizedCpu:F3} harness_cpu_max_read_average_percent={maxNormalizedCpu:F3} harness_max_working_set_bytes={maximumWorkingSet} cpu_valid={cpu} memory_valid={memory} disk_values={disk} payload_total_bytes={bytes} complete_minute_windows={completed.Count(window => window.ReceivedSamples == 6)} maximum_window_bytes={maximumWindowBytes} powershell_started=0; not a fleet/service benchmark.");
    }
    private static HttpResponseMessage Json(string body) => new(HttpStatusCode.OK) { Content=new StringContent(body, Encoding.UTF8, "application/json") };
    private sealed class FakeFactory(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>>? send) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => send is null ? new HttpClient() : new HttpClient(new Handler(send));
    }
    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => send(request, token);
    }
}
