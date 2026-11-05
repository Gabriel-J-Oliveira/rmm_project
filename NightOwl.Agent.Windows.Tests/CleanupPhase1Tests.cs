using System.Collections;
using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class CleanupPhase1Tests
{
    private static readonly string[] BasicKeys = { "manufacturer", "model", "serial_number", "memory_total_bytes", "total_memory_bytes", "collected_at" };
    private delegate Dictionary<string, object?> Merge(Dictionary<string, object?> core, Dictionary<string, object?> details, Dictionary<string, object?> storage, string drive);
    private static void Check(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }

    internal static void Run(bool baseline)
    {
        Func<Dictionary<string, object?>, Dictionary<string, object?>> basic = baseline ? ReferenceBasic :
            typeof(WindowsInventoryCollector).GetMethod("GetBasicHardware", BindingFlags.Static | BindingFlags.NonPublic)!
                .CreateDelegate<Func<Dictionary<string, object?>, Dictionary<string, object?>>>();
        Merge merge = baseline ? ReferenceMerge : typeof(WindowsInventoryCollector)
            .GetMethod("MergeHardwareSections", BindingFlags.Static | BindingFlags.NonPublic)!.CreateDelegate<Merge>();
        if (!baseline)
        {
            string source = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..",
                "NightOwl.Agent.Windows", "Collectors", "WindowsInventoryCollector.cs"));
            string coreBody = source[source.IndexOf("private AgentCollectPayload BuildCollectPayloadCore", StringComparison.Ordinal)..
                source.IndexOf("public List<Dictionary<string, object?>> GetDisks", StringComparison.Ordinal)];
            Check(coreBody.Contains("GetBasicHardware(platform)", StringComparison.Ordinal) &&
                coreBody.Contains("MergeHardwareSections(hardware, details, storage", StringComparison.Ordinal) &&
                !coreBody.Contains(".Keys.Except(", StringComparison.Ordinal) && !coreBody.Contains(".PhysicalDisks(", StringComparison.Ordinal),
                "Scheduled collection must use the consolidated paths, not rebuild or renormalize their output.");
        }
        foreach (Dictionary<string, object?> platform in new[] { Platform(), new Dictionary<string, object?>() })
        {
            Dictionary<string, object?> expected = ReferenceBasic(platform), actual = basic(platform);
            Check(actual.Keys.ToHashSet().SetEquals(BasicKeys), "Basic hardware shape changed.");
            Check(actual["collected_at"] is DateTimeOffset, "Basic hardware lost its collection timestamp.");
            actual["collected_at"] = expected["collected_at"];
            Check(JsonSerializer.Serialize(actual) == JsonSerializer.Serialize(expected), "Basic hardware values/aliases changed.");
            Check(!platform.ContainsKey("collected_at"), "Basic mapper mutated its source.");
        }
        foreach (bool detailsPresent in new[] { true, false })
        foreach (bool storageSucceeded in new[] { true, false })
        {
            Dictionary<string, object?> expected = ReferenceMerge(Core(), detailsPresent ? Details() : new(), Storage(storageSucceeded), "C:");
            Dictionary<string, object?> actual = merge(Core(), detailsPresent ? Details() : new(), Storage(storageSucceeded), "C:");
            Check(JsonSerializer.Serialize(actual) == JsonSerializer.Serialize(expected), "Merge changed hardware, unknown/absent state, or disk association.");
        }
        CheckConfigRoundTrip();
        Dictionary<string, object?> platformSource = Platform();
        var basicCost = Measure(() => basic(platformSource));
        CountingRows counter = new();
        Dictionary<string, object?> storage = Storage(true); storage["physical_disks"] = counter;
        Dictionary<string, object?> core = Core(), details = Details();
        var mergeCost = Measure(() => merge(core, details, storage, "C:"));
        counter.Enumerations = 0;
        merge(core, details, storage, "C:");
        Check(counter.Enumerations == (baseline ? 2 : 1), "Physical disk normalization was unexpectedly repeated.");

        WindowsInventoryCollector collector = new(new JsonlLogger(Path.Combine(Path.GetTempPath(), "unused-cleanup-heartbeat.jsonl")));
        AgentConfig config = new() { MachineId = "synthetic-cleanup", AgentToken = "synthetic-only", AgentVersion = "1.2.3+synthetic",
            InstallPath = Path.Combine(Path.GetTempPath(), "nightowl-nonexistent-cleanup-binaries") };
        for (int i = 0; i < 5; i++) collector.BuildHeartbeat(config);
        long processes = WindowsInventoryCollector.PowerShellStarted;
        double[] durations = new double[50];
        long before = GC.GetAllocatedBytesForCurrentThread();
        AgentHeartbeatPayload? last = null;
        for (int i = 0; i < durations.Length; i++)
        {
            long start = Stopwatch.GetTimestamp(); last = collector.BuildHeartbeat(config);
            durations[i] = Stopwatch.GetElapsedTime(start).TotalMilliseconds;
        }
        long allocated = GC.GetAllocatedBytesForCurrentThread() - before;
        long children = WindowsInventoryCollector.PowerShellStarted - processes;
        Array.Sort(durations);
        Check(children == 0, "Heartbeat launched PowerShell.");
        Check(last!.AgentVersion == "1.2.3" && Equals(last.Agent["version"], "1.2.3"), "Heartbeat version normalization changed.");
        Console.WriteLine("CLEANUP_PHASE1_MEASURED=" + JsonSerializer.Serialize(new {
            mode = baseline ? "before_cleanup_reference" : "after_cleanup_production",
            basic = basicCost, merge = mergeCost, physical_normalizations = counter.Enumerations,
            heartbeat = new { iterations = durations.Length, p50_ms = durations[25], p95_ms = durations[47],
                allocated_bytes_per_call = allocated / durations.Length, powershell_children = children,
                payload_bytes = JsonSerializer.SerializeToUtf8Bytes(last).Length },
            scope = "local_native_heartbeat_and_synthetic_normalization_not_fleet" }));
        Console.WriteLine("PASS cleanup phase1: basic aliases, complete/failed/absent merge equivalence, disk association, configuration shape, lightweight native heartbeat.");
    }

    private static object Measure(Func<object> action)
    {
        for (int i = 0; i < 20; i++) action();
        const int iterations = 2000;
        long startBytes = GC.GetAllocatedBytesForCurrentThread();
        Stopwatch timer = Stopwatch.StartNew();
        for (int i = 0; i < iterations; i++) action();
        timer.Stop();
        return new { iterations, elapsed_ms = timer.Elapsed.TotalMilliseconds,
            allocated_bytes_per_call = (GC.GetAllocatedBytesForCurrentThread() - startBytes) / iterations };
    }

    private static Dictionary<string, object?> ReferenceBasic(Dictionary<string, object?> source)
    {
        Dictionary<string, object?> hardware = WindowsInventoryCollector.GetHardware(source);
        foreach (string key in hardware.Keys.Except(BasicKeys).ToArray()) hardware.Remove(key);
        return hardware;
    }

    private static Dictionary<string, object?> ReferenceMerge(Dictionary<string, object?> core, Dictionary<string, object?> details, Dictionary<string, object?> storage, string drive)
    {
        Dictionary<string, object?> hardware = details.Count > 0 ? HardwareInventoryNormalizer.MergeHardware(core, details, storage, drive) : core;
        if (storage.GetValueOrDefault("query_succeeded") is true) hardware["physical_disks"] = HardwareInventoryNormalizer.PhysicalDisks(storage, drive);
        return hardware;
    }

    private static Dictionary<string, object?> Platform() => new() { ["manufacturer"] = "Synthetic OEM", ["model"] = "Synthetic model",
        ["serial_number"] = "SYNTHETIC-SERIAL", ["cpu_name"] = "Synthetic CPU", ["cpu_max_clock_mhz"] = 3200L,
        ["physical_cores"] = 4L, ["logical_processors"] = 8L, ["total_memory_bytes"] = 17179869184L };
    private static Dictionary<string, object?> Core() => new() { ["manufacturer"] = "Synthetic OEM", ["memory_total_bytes"] = 17179869184L,
        ["cpu"] = new Dictionary<string, object?> { ["name"] = "Synthetic CPU" } };
    private static Dictionary<string, object?> Details() => new() {
        ["modules_query_succeeded"] = true, ["arrays_query_succeeded"] = true,
        ["modules"] = new List<object?> { new Dictionary<string, object?> { ["capacity_bytes"] = 17179869184L,
            ["part_number"] = "SYNTHETIC-PART", ["serial_number"] = "SYNTHETIC-RAM", ["speed_mhz"] = 3200L } },
        ["memory_slots"] = new List<object?> { 2L }, ["battery"] = new Dictionary<string, object?> { ["query_succeeded"] = true, ["present"] = false } };
    private static Dictionary<string, object?> Storage(bool success) => new() { ["query_succeeded"] = success,
        ["physical_disks"] = new List<object?> { new Dictionary<string, object?> { ["serial_number"] = "SYNTHETIC-DISK", ["size_bytes"] = 512000000000L } },
        ["os_disks"] = new List<object?> { new Dictionary<string, object?> { ["serial_number"] = "SYNTHETIC-DISK", ["disk_number"] = 0L, ["drive_letters"] = new List<object?> { "C:" } } } };

    private static void CheckConfigRoundTrip()
    {
        AgentConfig config = new() { AgentToken = "synthetic-only", MachineId = "synthetic-machine", TelemetryMode = "legacy",
            TelemetryEnabled = false, TelemetrySampleSeconds = 600, TelemetryFlushSeconds = 1200,
            Intervals = new() { HeartbeatSeconds = 300, CollectSeconds = 3600, JobsSeconds = 78 } };
        string json = JsonSerializer.Serialize(config);
        AgentConfig loaded = JsonSerializer.Deserialize<AgentConfig>(json)!;
        Check(JsonSerializer.Serialize(loaded) == json, "Persisted configuration contract changed.");
        Check(loaded.Intervals.JobsSeconds == 78 && !loaded.TelemetryEnabled && loaded.TelemetryMode == "legacy", "Legacy explicit settings were lost.");
    }

    private sealed class CountingRows : IEnumerable<object?>
    {
        internal int Enumerations;
        public IEnumerator<object?> GetEnumerator() { Enumerations++; yield return new Dictionary<string, object?> { ["serial_number"] = "SYNTHETIC-DISK" }; }
        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }
}
