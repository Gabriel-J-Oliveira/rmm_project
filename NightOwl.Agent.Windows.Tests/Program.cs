using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Services;
using NightOwl.Agent.Windows;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Text.Json;

try
{
    TestNewConfigContainsTrustedReleaseKeys();
    TestLegacyDefaultConfigReceivesTrustedReleaseKeys();
    TestMigrationIsIdempotent();
    TestMigrationDoesNotDuplicateJobTypes();
    TestPreservesIdentityAndEndpoints();
    TestPreservesIntervals();
    TestPreservesCustomAllowedJobTypes();
    TestMigratedConfigDoesNotRestoreExplicitRemoval();
    TestMigrationResultDoesNotExposeSecrets();
    TestMigrationPersistsAndReloads();
    TestPersistedMigrationIsNotReapplied();
    TestPersistenceFailureDoesNotCorruptExistingConfig();
    TestMigrationLogPayloadDoesNotExposeSecrets();
    TestUninstallerRunnerPayloadCopiesSelfContainedFiles();
    TestUninstallerRunnerPayloadRunsOutsideInstallPath();
    TestLoadedRuntimeFromRunnerDoesNotBlockInstallPathRemoval();
    TestTrayLocalUninstallUsesCentralAclAndAuthorizationFile();
    TestAgentProcessArgumentsDoNotCarrySecrets();
    TestRepairRunnerScriptUsesPinnedReleaseAndNoEnrollment();
    TestExternalRepairRunnerSkipsInterruptedRecoveryUntilTimeout();
    TestExternalRepairRunnerTimeoutAllowsInterruptedRecovery();
    TestRepairRunnerScriptPersistsCompletedResult();
    TestRepairRunnerScriptPersistsFailedResult();
    TestRepairRunnerScriptWritesDiagnosticWhenResultPersistenceFails();
    TestPendingCompletedUpdateFinalizesLocalJobStateOnRestart();
    TestCompletedUpdateJobIsIgnoredOnLaterRestart();
    TestAgentStateHeartbeatPreservesInstalledLifecycle();
    TestAgentStateJobPullAndCollectionPreserveLifecycle();
    TestAgentStateRuntimeDoesNotRewriteUninstalledLifecycle();
    TestAgentStateRuntimePreservesUnknownPropertiesAndRecentJobs();
    TestStateSaveFailureDoesNotEscapeWorkerBoundary();
    TestStateSaveFailureBackoffPreventsAcceleratedLoop();
    TestTelemetryDefaultsAndLegacyConfig();
    TestTelemetryCollectorAndCpuMath();
    TestTelemetryNetworkInterfaceDeltas();
    TestTelemetryMemorySurvivesCpuIdentityFailure();
    TestTelemetryBufferAndOfflineRetry();
    TestTelemetryBufferStartupRecovery();
    TestTelemetryPipelineIsolation();
    TestTelemetryTimeoutAndShutdown();
    TestTelemetryCollectionCost();
    TestHardwareInventoryV2();

    Console.WriteLine("NightOwl agent config migration tests passed.");
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex);
    Environment.Exit(1);
}

static AgentConfig LegacyConfig()
{
    return new AgentConfig
    {
        ConfigMigrationVersion = 1,
        AgentToken = "super-secret-token",
        MachineId = "machine-taxcel",
        ServerBaseUrl = "https://nightowl.controlsul.com.br",
        HeartbeatUrl = "https://nightowl.controlsul.com.br/api/agent/heartbeat/",
        CollectUrl = "https://nightowl.controlsul.com.br/api/agent/collect/",
        JobsPullUrl = "https://nightowl.controlsul.com.br/api/agent/jobs/pull/",
        JobsResultUrl = "https://nightowl.controlsul.com.br/api/agent/jobs/result/",
        Intervals = new AgentIntervals
        {
            HeartbeatSeconds = 123,
            CollectSeconds = 456,
            JobsSeconds = 78
        },
        AllowedJobTypes = new List<string>
        {
            "ping",
            "collect_logs",
            "collect_disks",
            "collect_software",
            "collect_security",
            "windows_update_scan",
            "force_inventory",
            "update_agent",
            "repair_agent",
            "restart_agent"
        }
    };
}

static void TestHardwareInventoryV2()
{
    Dictionary<string, object?> module = new()
    {
        ["device_locator"] = " DIMM A1 ", ["bank_label"] = " BANK 0 ",
        ["capacity_bytes"] = 16L * 1024 * 1024 * 1024, ["speed_mhz"] = 3200L,
        ["configured_speed_mhz"] = 2933L, ["manufacturer"] = " Vendor ",
        ["part_number"] = " Part ", ["serial_number"] = " Serial ",
        ["form_factor"] = 8L, ["memory_type"] = 26L
    };
    Dictionary<string, object?> oneModule = HardwareInventoryNormalizer.Memory(new()
    {
        ["modules"] = new List<object?> { module }, ["memory_slots"] = new List<object?> { 2L }
    }, 16L * 1024 * 1024 * 1024);
    Require(Equals(oneModule["slots_total"], 2L) && Equals(oneModule["slots_used"], 1) && Equals(oneModule["slots_free"], 1L),
        "One physical module must leave one slot free.");
    Dictionary<string, object?> firstModule = ((List<Dictionary<string, object?>>)oneModule["modules"]!)[0];
    Require(Equals(firstModule["device_locator"], "DIMM A1") && Equals(firstModule["manufacturer"], "Vendor") &&
            Equals(firstModule["memory_type"], "ddr4") && Equals(firstModule["form_factor"], "dimm") &&
            Equals(firstModule["configured_speed_mhz"], 2933L), "Module details must be normalized and trimmed.");

    Dictionary<string, object?> full = HardwareInventoryNormalizer.Memory(new()
    {
        ["modules"] = new List<object?>
        {
            new Dictionary<string, object?> { ["capacity_bytes"] = 8L * 1024 * 1024 * 1024 },
            new Dictionary<string, object?> { ["capacity_bytes"] = 8L * 1024 * 1024 * 1024 }
        },
        ["memory_slots"] = new List<object?> { 2L }
    }, null);
    Require(Equals(full["slots_used"], 2) && Equals(full["slots_free"], 0L) &&
            Equals(full["total_bytes"], 16L * 1024 * 1024 * 1024), "Occupied slots and module sum must be accurate.");
    Dictionary<string, object?> unknownSlots = HardwareInventoryNormalizer.Memory(new()
    {
        ["modules"] = new List<object?> { module }, ["memory_slots"] = new List<object?> { 0L }
    }, null);
    Require(unknownSlots["slots_total"] is null && unknownSlots["slots_free"] is null &&
            Equals(unknownSlots["slots_used"], 1), "Unavailable MemoryArray must not invent slot capacity.");
    Dictionary<string, object?> partial = HardwareInventoryNormalizer.Memory(new()
    {
        ["modules"] = new List<object?> { new Dictionary<string, object?> { ["capacity_bytes"] = null } }
    }, null);
    Require(partial["total_bytes"] is null && Equals(partial["slots_used"], 1),
        "An installed module with unknown capacity still occupies a slot without implying total RAM.");

    List<Dictionary<string, object?>> disks = HardwareInventoryNormalizer.PhysicalDisks(new List<object?>
    {
        new Dictionary<string, object?>
        {
            ["disk_number"] = 0L, ["model"] = " NVMe model ", ["size_bytes"] = 256L * 1024 * 1024 * 1024,
            ["media_type"] = "SSD", ["bus_type"] = "NVMe", ["drive_letters"] = new List<object?> { "c", "D:" }
        },
        new Dictionary<string, object?>
        {
            ["disk_number"] = 1L, ["model"] = " SATA model ", ["media_type"] = "HDD",
            ["bus_type"] = "SATA", ["drive_letters"] = new List<object?> { "E:" }
        },
        new Dictionary<string, object?>
        {
            ["disk_number"] = 2L, ["media_type"] = "Fixed hard disk media",
            ["bus_type"] = "SCSI", ["drive_letters"] = new List<object?>()
        }
    }, "C:");
    Require(disks.Count == 3 && Equals(disks[0]["media_type"], "ssd") && Equals(disks[0]["bus_type"], "nvme") &&
            Equals(disks[0]["is_system_disk"], true) && ((List<string>)disks[0]["drive_letters"]!).SequenceEqual(new[] { "C:", "D:" }),
        "NVMe is a bus, SSD is media, and C: must map to the physical disk.");
    Require(Equals(disks[1]["media_type"], "hdd") && Equals(disks[1]["bus_type"], "sata") &&
            Equals(disks[1]["is_system_disk"], false), "A second SATA HDD must not be marked as the system disk.");
    Require(Equals(disks[2]["media_type"], "unknown") && Equals(disks[2]["bus_type"], "scsi") &&
            disks[2]["is_system_disk"] is null, "WMI fallback must not infer HDD or a volume association.");
    List<Dictionary<string, object?>> unavailable = HardwareInventoryNormalizer.PhysicalDisks(new List<object?>
    {
        new Dictionary<string, object?> { ["media_type"] = null, ["bus_type"] = "Unspecified" }
    }, "C:");
    Require(Equals(unavailable[0]["media_type"], "unknown") && Equals(unavailable[0]["bus_type"], "unknown"),
        "Missing Get-PhysicalDisk data and unknown bus values must remain unknown.");
    List<Dictionary<string, object?>> noSystemDrive = HardwareInventoryNormalizer.PhysicalDisks(new List<object?>
    {
        new Dictionary<string, object?> { ["drive_letters"] = new List<object?> { "C:" } }
    }, "");
    Require(noSystemDrive[0]["is_system_disk"] is null,
        "A missing SystemDrive must not imply that C: is the system disk.");

    Dictionary<string, object?> notebook = HardwareInventoryNormalizer.Battery(new()
    {
        ["present"] = true, ["status_code"] = 6L, ["estimated_charge_remaining"] = 75L,
        ["design_capacity_mwh"] = 50000L, ["full_charge_capacity_mwh"] = 40000L, ["cycle_count"] = 250L
    });
    Require(Equals(notebook["status"], "charging") && Equals(notebook["health_percent"], 80d) &&
            Equals(notebook["cycle_count"], 250L), "Battery health requires valid design and full capacities.");
    Dictionary<string, object?> desktop = HardwareInventoryNormalizer.Battery(new() { ["present"] = false });
    Require(Equals(desktop["status"], "not_present") && desktop["health_percent"] is null &&
            desktop["estimated_charge_remaining"] is null, "Desktop battery metrics must remain absent.");
    Dictionary<string, object?> incomplete = HardwareInventoryNormalizer.Battery(new()
    {
        ["present"] = true, ["design_capacity_mwh"] = 0L, ["full_charge_capacity_mwh"] = 40000L
    });
    Require(incomplete["health_percent"] is null, "Incomplete battery capacity must never divide by zero.");

    AgentCollectPayload oldPayload = new() { MachineId = "synthetic-machine" };
    using JsonDocument oldJson = JsonDocument.Parse(JsonSerializer.Serialize(oldPayload));
    Require(oldJson.RootElement.TryGetProperty("hardware", out _) && oldJson.RootElement.TryGetProperty("disks", out _),
        "Legacy collect sections must remain in the payload.");
    oldPayload.Hardware["memory"] = oneModule;
    oldPayload.Hardware["physical_disks"] = disks;
    oldPayload.Hardware["battery"] = notebook;
    oldPayload.Hardware["memory_total_bytes"] = oneModule["total_bytes"];
    oldPayload.Disks.Add(new Dictionary<string, object?> { ["letter"] = "C:", ["size_bytes"] = 256L });
    using JsonDocument newJson = JsonDocument.Parse(JsonSerializer.Serialize(oldPayload));
    Require(newJson.RootElement.GetProperty("hardware").GetProperty("memory").GetProperty("slots_free").GetInt64() == 1 &&
            newJson.RootElement.GetProperty("hardware").GetProperty("physical_disks").GetArrayLength() == 3 &&
            newJson.RootElement.GetProperty("disks").GetArrayLength() == 1,
        "Enriched hardware must coexist with the existing logical-disk contract.");
}

static void TestTelemetryDefaultsAndLegacyConfig()
{
    AgentConfig legacy = JsonSerializer.Deserialize<AgentConfig>("{\"machineId\":\"synthetic-machine\",\"agentToken\":\"synthetic-token\"}")!;
    Require(!legacy.TelemetryEnabled, "Legacy config must keep telemetry disabled.");
    Require(legacy.TelemetrySampleSeconds == 300 && legacy.TelemetryFlushSeconds == 3600, "Telemetry cadence defaults must be safe.");
    Require(legacy.TelemetryBufferMaxSamples == 2304 && legacy.TelemetryBufferMaxAgeHours == 192, "Buffer defaults must cover eight days.");
    Require(!new AgentConfig().TelemetryEnabled, "New config must be opt-in.");
    Require(TelemetryPipeline.NextFlushDelay(3600, delivered: true, backlog: false) == TimeSpan.FromHours(1),
        "Normal sampling must not send one request per sample.");
    Require(TelemetryPipeline.NextFlushDelay(3600, delivered: true, backlog: true) == TimeSpan.FromMinutes(1),
        "Offline backlog must drain in full batches.");
    Require(TelemetryPipeline.NextFlushDelay(3600, delivered: false, backlog: true) == TimeSpan.FromHours(1),
        "Failed sends must back off rather than hammer the backend.");
}

static void TestTelemetryNetworkInterfaceDeltas()
{
    TelemetryCollector collector = new();
    Dictionary<string, (long Received, long Sent)> first = new()
    {
        ["nic-a"] = (1000, 500), ["nic-b"] = (200, 100),
    };
    TelemetryNetwork initial = collector.CalculateNetworkDelta(first);
    Require(initial.ReceivedBytes is null && initial.SentBytes is null, "First counters have no delta.");
    TelemetryNetwork added = collector.CalculateNetworkDelta(new Dictionary<string, (long, long)>
    {
        ["nic-a"] = (1100, 550), ["nic-b"] = (220, 110), ["nic-new"] = (1000000, 1000000),
    });
    Require(added.ReceivedBytes == 120 && added.SentBytes == 60, "New NIC must not create a historical traffic spike.");
    TelemetryNetwork removed = collector.CalculateNetworkDelta(new Dictionary<string, (long, long)>
    {
        ["nic-a"] = (1150, 575), ["nic-new"] = (1000020, 1000010),
    });
    Require(removed.ReceivedBytes == 70 && removed.SentBytes == 35, "Removed NIC must not invalidate surviving deltas.");
    TelemetryNetwork reset = collector.CalculateNetworkDelta(new Dictionary<string, (long, long)>
    {
        ["nic-a"] = (5, 600), ["nic-new"] = (1000030, 5),
    });
    Require(reset.ReceivedBytes == 10 && reset.SentBytes == 25, "Reset/negative counters must be excluded per direction.");
    TelemetryNetwork allReset = collector.CalculateNetworkDelta(new Dictionary<string, (long, long)>
    {
        ["nic-a"] = (1, 1), ["nic-new"] = (1, 1),
    });
    Require(allReset.ReceivedBytes is null && allReset.SentBytes is null, "No valid delta must remain null.");
}

static void TestTelemetryMemorySurvivesCpuIdentityFailure()
{
    TelemetryCollector collector = new();
    Dictionary<(int Pid, long Started), TimeSpan> current = new();
    TelemetryProcess sampled = collector.SampleProcess(42, "synthetic-process", 4096,
        () => throw new System.ComponentModel.Win32Exception("synthetic access denied"),
        () => TimeSpan.FromSeconds(1), current, TimeSpan.FromMinutes(5));
    Require(sampled.WorkingSetBytes == 4096 && sampled.CpuPercent is null && current.Count == 0,
        "Unavailable StartTime must not remove a readable process from memory ranking.");
    Require(TelemetryCollector.SelectTopProcesses(new[] { sampled }).Memory.Single().Pid == 42,
        "Memory top must retain a process whose CPU identity is inaccessible.");
}

static void TestTelemetryCollectorAndCpuMath()
{
    Require(TelemetryCollector.CpuPercent(TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(1), 4) == 50,
        "Process CPU must divide by elapsed time and logical processor count.");
    Require(TelemetryCollector.CpuPercent(TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(1), 2) == 100,
        "Process CPU must be clamped.");
    Require(TelemetryCollector.CpuPercent(TimeSpan.FromSeconds(-1), TimeSpan.FromSeconds(1), 2) is null,
        "Invalid process CPU deltas must be unavailable.");
    var processes = Enumerable.Range(1, 10).Select(index => new TelemetryProcess
    {
        ProcessName = $"synthetic-{index}", Pid = index, CpuPercent = index, WorkingSetBytes = index * 100,
    }).ToList();
    TelemetryProcesses top = TelemetryCollector.SelectTopProcesses(processes);
    Require(top.Cpu.Count == 5 && top.Memory.Count == 5, "Top process lists must be capped at five.");
    Require(top.Cpu[0].Pid == 10 && top.Memory[0].Pid == 10, "Top process ordering must be descending.");
    TelemetryCollector collector = new(new TelemetryCollectorHooks
    {
        Cpu = () => throw new IOException("synthetic metric unavailable"),
        Memory = () => new TelemetryMemory { TotalBytes = 1000, AvailableBytes = 500, UsedPercent = 50 },
        Network = () => new TelemetryNetwork { ReceivedBytes = 5, SentBytes = 7 },
        Processes = (_, _) => top,
    });
    TelemetrySample sample = collector.Collect("synthetic-machine", CancellationToken.None);
    Require(sample.TelemetryErrorsCount == 1 && sample.Cpu.UsagePercent is null, "A failed metric must not discard the sample.");
    Require(sample.Memory.TotalBytes == 1000 && sample.Network.ReceivedBytes == 5, "Healthy metrics must survive partial failure.");
    using JsonDocument serialized = JsonDocument.Parse(JsonSerializer.Serialize(sample));
    Require(serialized.RootElement.GetProperty("sample_id").GetGuid() == sample.SampleId, "Telemetry sample must serialize with its stable ID.");
    Require(JsonSerializer.Deserialize<TelemetrySample>(JsonSerializer.Serialize(sample))?.SampleId == sample.SampleId,
        "Telemetry sample must deserialize without changing ID.");
}

static void TestTelemetryBufferAndOfflineRetry()
{
    string root = CreateTempDir();
    try
    {
        string path = Path.Combine(root, "telemetry-buffer.json");
        DateTimeOffset now = DateTimeOffset.UtcNow;
        TelemetryBuffer buffer = new(path, 6, TimeSpan.FromHours(48), () => now);
        TelemetryBuffer defaults = new(Path.Combine(root, "default-buffer.json"), 2304, TimeSpan.FromHours(192), () => now);
        Require(defaults.MaxSamples == 2304 && defaults.MaxAge == TimeSpan.FromDays(8), "Offline spool limits must cover eight days.");
        List<Guid> ids = new();
        for (int index = 0; index < 7; index++)
        {
            TelemetrySample sample = new() { CollectedAt = now.AddMinutes(index - 7) };
            ids.Add(sample.SampleId);
            Require(buffer.Add(sample) == (index == 6 ? 1 : 0), "Overflow must evict only the oldest sample.");
        }
        TelemetryBuffer reloaded = new(path, 6, TimeSpan.FromHours(48), () => now);
        Require(reloaded.Count == 6 && !reloaded.Snapshot(24).Any(item => item.SampleId == ids[0]),
            "Buffer must survive restart and discard oldest sample on overflow.");
        Require(reloaded.Snapshot(24).Select(item => item.SampleId).SequenceEqual(ids.Skip(1)),
            "Offline retry must retain sample IDs and ordering.");
        reloaded.Acknowledge(new[] { ids[2], ids[3] });
        Require(reloaded.Count == 4 && reloaded.Snapshot(24).Any(item => item.SampleId == ids[1]),
            "Flush confirmation must remove only acknowledged samples.");
        now = now.AddHours(49);
        Require(reloaded.Add(new TelemetrySample { CollectedAt = now }) == 0 && reloaded.Count == 1,
            "Age limit must discard expired samples before accepting a new one.");
        Require(!Directory.GetFiles(root, "*.tmp", SearchOption.AllDirectories).Any(), "Successful atomic writes must not leave temporary files.");
    }
    finally { DeleteTempDir(root); }
}

static void TestTelemetryBufferStartupRecovery()
{
    string root = CreateTempDir();
    try
    {
        string path = Path.Combine(root, "telemetry-buffer.json");
        JsonlLogger logger = new(Path.Combine(root, "telemetry.jsonl"));
        AgentConfig config = new() { MachineId = Guid.NewGuid().ToString(), TelemetryEnabled = true };
        TelemetrySample existing = new() { CollectedAt = DateTimeOffset.UtcNow };
        new TelemetryBuffer(path, 2304, TimeSpan.FromDays(8)).Add(existing);
        int attempts = 0;
        TelemetryPipeline transient = new(new TelemetryCollector(), new AgentApiClient(new FakeTelemetryHttpClientFactory()), logger,
            (file, count, age) =>
            {
                if (Interlocked.Increment(ref attempts) == 1) throw new IOException("synthetic transient read failure");
                return new TelemetryBuffer(file, count, age);
            }, path, TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(100));
        Stopwatch clock = Stopwatch.StartNew();
        TelemetryBuffer recovered = transient.LoadBufferWithRetryAsync(config, CancellationToken.None).GetAwaiter().GetResult();
        Require(attempts == 2 && clock.Elapsed >= TimeSpan.FromMilliseconds(20), "Transient startup IO must retry with a real delay.");
        Require(recovered.Count == 1 && recovered.Snapshot(1)[0].SampleId == existing.SampleId,
            "Transient startup failure must not lose previously valid buffered data.");
        recovered.Add(new TelemetrySample { CollectedAt = DateTimeOffset.UtcNow });
        string original = File.ReadAllText(path);
        File.WriteAllText(path, "{broken-json");
        TelemetryPipeline corrupt = new(new TelemetryCollector(), new AgentApiClient(new FakeTelemetryHttpClientFactory()), logger,
            (file, count, age) => new TelemetryBuffer(file, count, age), path,
            TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(100));
        TelemetryBuffer afterQuarantine = corrupt.LoadBufferWithRetryAsync(config, CancellationToken.None).GetAwaiter().GetResult();
        string[] quarantined = Directory.GetFiles(Path.Combine(root, "quarantine"), "*.json");
        Require(quarantined.Length == 1 && File.ReadAllText(quarantined[0]) == "{broken-json" && afterQuarantine.Count == 0,
            "Corrupt bytes must be quarantined intact before a fresh spool starts.");
        Require(original.Contains("sample_id"), "The previously valid spool must have been persisted before corruption simulation.");
        string logs = File.ReadAllText(Path.Combine(root, "telemetry.jsonl"));
        Require(logs.Contains("telemetry.buffer.failed") && logs.Contains("telemetry.buffer.corrupt"),
            "Transient and corrupt startup failures must be distinguishable in diagnostics.");
        File.WriteAllText(path, "x");
        TelemetryPipeline invalid = new(new TelemetryCollector(), new AgentApiClient(new FakeTelemetryHttpClientFactory()), logger,
            (file, count, age) => File.Exists(file)
                ? throw new InvalidDataException("synthetic oversized spool")
                : new TelemetryBuffer(file, count, age), path,
            TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(100));
        Require(invalid.LoadBufferWithRetryAsync(config, CancellationToken.None).GetAwaiter().GetResult().Count == 0 &&
            Directory.GetFiles(Path.Combine(root, "quarantine"), "*.json").Length == 2,
            "An oversized/invalid spool must also be quarantined, not retried forever.");
    }
    finally { DeleteTempDir(root); }
}

static void TestTelemetryPipelineIsolation()
{
    string root = CreateTempDir();
    try
    {
        AgentConfig disabled = new() { MachineId = Guid.NewGuid().ToString(), TelemetryEnabled = false };
        int collectionCalls = 0;
        using ManualResetEventSlim entered = new(false);
        using ManualResetEventSlim release = new(false);
        TelemetryCollector collector = new(new TelemetryCollectorHooks
        {
            Cpu = () => 10,
            Memory = () => new TelemetryMemory(),
            Network = () => new TelemetryNetwork(),
            Processes = (_, _) =>
            {
                Interlocked.Increment(ref collectionCalls);
                entered.Set();
                release.Wait(TimeSpan.FromSeconds(3));
                return new TelemetryProcesses();
            },
        });
        FakeTelemetryHttpClientFactory http = new();
        AgentApiClient api = new(http);
        JsonlLogger logger = new(Path.Combine(root, "telemetry.jsonl"));
        TelemetryPipeline pipeline = new(collector, api, logger);
        pipeline.RunAsync(disabled, CancellationToken.None).GetAwaiter().GetResult();
        Require(collectionCalls == 0 && http.Calls == 0, "Disabled telemetry must neither collect nor send.");

        AgentConfig enabled = new() { MachineId = disabled.MachineId, TelemetryEnabled = true,
            TelemetryUrl = "https://localhost/api/agent/telemetry/", AgentToken = "synthetic-token" };
        TelemetryBuffer buffer = new(Path.Combine(root, "buffer.json"), 6, TimeSpan.FromHours(48));
        Task first = pipeline.CollectOnceAsync(enabled, buffer, CancellationToken.None);
        Require(entered.Wait(TimeSpan.FromSeconds(3)), "First collection must start.");
        pipeline.CollectOnceAsync(enabled, buffer, CancellationToken.None).GetAwaiter().GetResult();
        Require(collectionCalls == 1, "A second collection must skip while the first is active.");
        release.Set();
        first.GetAwaiter().GetResult();
        Require(buffer.Count == 1, "First collection must persist its sample.");
        Guid sampleId = buffer.Snapshot(1)[0].SampleId;
        http.Succeed = false;
        pipeline.FlushOnceAsync(enabled, buffer, CancellationToken.None).GetAwaiter().GetResult();
        Require(buffer.Count == 1 && buffer.Snapshot(1)[0].SampleId == sampleId,
            "Failed HTTP flush must preserve the same sample ID.");
        http.Succeed = true;
        pipeline.FlushOnceAsync(enabled, buffer, CancellationToken.None).GetAwaiter().GetResult();
        Require(buffer.Count == 0 && http.Calls == 2, "Successful flush must acknowledge the sample.");
        Require(http.SampleIds.All(id => id == sampleId), "Retries must send the same sample ID.");
        TelemetrySample lostResponse = new() { CollectedAt = DateTimeOffset.UtcNow };
        buffer.Add(lostResponse);
        http.LoseResponseAfterPersist = true;
        pipeline.FlushOnceAsync(enabled, buffer, CancellationToken.None).GetAwaiter().GetResult();
        Require(buffer.Count == 1 && http.PersistedSampleIds.Contains(lostResponse.SampleId),
            "A lost response after server persistence must retain the local sample for replay.");
        http.LoseResponseAfterPersist = false;
        pipeline.FlushOnceAsync(enabled, buffer, CancellationToken.None).GetAwaiter().GetResult();
        Require(buffer.Count == 0 && http.PersistedSampleIds.Count == 2,
            "A later 2xx must clear the replayed sample without duplicating server identity.");
        string logs = File.ReadAllText(Path.Combine(root, "telemetry.jsonl"));
        Require(logs.Contains("telemetry.sample.skipped") && logs.Contains("telemetry.flush.failed"),
            "Telemetry skip and retry must be observable.");
    }
    finally { DeleteTempDir(root); }
}

static void TestTelemetryTimeoutAndShutdown()
{
    string root = CreateTempDir();
    try
    {
        using ManualResetEventSlim entered = new(false);
        using ManualResetEventSlim release = new(false);
        int calls = 0;
        TelemetryCollector collector = new(new TelemetryCollectorHooks
        {
            Cpu = () => 10,
            Memory = () => new TelemetryMemory(),
            Network = () => new TelemetryNetwork(),
            Processes = (_, _) =>
            {
                Interlocked.Increment(ref calls);
                entered.Set();
                release.Wait(TimeSpan.FromSeconds(5));
                return new TelemetryProcesses();
            },
        });
        string path = Path.Combine(root, "buffer.json");
        JsonlLogger logger = new(Path.Combine(root, "telemetry.jsonl"));
        TelemetryPipeline pipeline = new(collector, new AgentApiClient(new FakeTelemetryHttpClientFactory()), logger,
            (file, count, age) => new TelemetryBuffer(file, count, age), path,
            TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(150));
        AgentConfig config = new() { MachineId = Guid.NewGuid().ToString(), TelemetryEnabled = true,
            TelemetryUrl = "https://localhost/api/agent/telemetry/", AgentToken = "synthetic-token" };
        TelemetryBuffer buffer = new(path, 6, TimeSpan.FromDays(8));
        Task timedOut = pipeline.CollectOnceAsync(config, buffer, CancellationToken.None);
        Require(entered.Wait(TimeSpan.FromSeconds(2)), "Collection must enter before timeout.");
        timedOut.GetAwaiter().GetResult();
        Require(pipeline.CollectionActive, "Timed-out synchronous collection must remain tracked.");
        pipeline.CollectOnceAsync(config, buffer, CancellationToken.None).GetAwaiter().GetResult();
        Require(calls == 1, "Timeout must not launch another collection while the old one runs.");
        release.Set();
        Require(SpinWait.SpinUntil(() => !pipeline.CollectionActive, TimeSpan.FromSeconds(2)), "Late collection must eventually finish.");
        pipeline.CollectOnceAsync(config, buffer, CancellationToken.None).GetAwaiter().GetResult();
        Require(calls == 2 && buffer.Count == 1, "A later healthy collection must resume without buffering the timed-out sample.");

        using ManualResetEventSlim shutdownEntered = new(false);
        using ManualResetEventSlim shutdownRelease = new(false);
        TelemetryCollector shutdownCollector = new(new TelemetryCollectorHooks
        {
            Cpu = () => 10, Memory = () => new TelemetryMemory(), Network = () => new TelemetryNetwork(),
            Processes = (_, _) =>
            {
                shutdownEntered.Set();
                shutdownRelease.Wait(TimeSpan.FromSeconds(5));
                return new TelemetryProcesses();
            },
        });
        TelemetryPipeline shutdownPipeline = new(shutdownCollector, new AgentApiClient(new FakeTelemetryHttpClientFactory()), logger,
            (file, count, age) => new TelemetryBuffer(file, count, age), Path.Combine(root, "shutdown-buffer.json"),
            TimeSpan.FromSeconds(2), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(20), TimeSpan.FromMilliseconds(150));
        using CancellationTokenSource cts = new();
        Task run = shutdownPipeline.RunAsync(config, cts.Token);
        Require(shutdownEntered.Wait(TimeSpan.FromSeconds(2)), "Pipeline must start collection before shutdown.");
        Stopwatch shutdownClock = Stopwatch.StartNew();
        cts.Cancel();
        run.WaitAsync(TimeSpan.FromSeconds(2)).GetAwaiter().GetResult();
        Require(shutdownClock.Elapsed < TimeSpan.FromSeconds(2) && shutdownPipeline.CollectionActive,
            "Shutdown must finish within grace without pretending a synchronous collection was cancelled.");
        shutdownRelease.Set();
        Require(SpinWait.SpinUntil(() => !shutdownPipeline.CollectionActive, TimeSpan.FromSeconds(2)),
            "Outstanding collection must finish after the blocking API returns.");
        Require(File.ReadAllText(Path.Combine(root, "telemetry.jsonl")).Contains("telemetry.shutdown.deferred"),
            "Bounded shutdown with an uncooperative API must be observable.");
    }
    finally { DeleteTempDir(root); }
}

static void TestTelemetryCollectionCost()
{
    if (!OperatingSystem.IsWindows()) return;
    Stopwatch clock = Stopwatch.StartNew();
    TelemetrySample sample = new TelemetryCollector().Collect("synthetic-machine", CancellationToken.None);
    Require(sample.Memory.TotalBytes > 0, "Windows memory total should be available through GlobalMemoryStatusEx.");
    long sampleBytes = JsonSerializer.SerializeToUtf8Bytes(sample).Length;
    long batchBytes = JsonSerializer.SerializeToUtf8Bytes(new TelemetryBatch
    {
        MachineId = "synthetic-machine",
        Samples = Enumerable.Repeat(sample, 6).ToList(),
    }).Length;
    Console.WriteLine($"Telemetry collector: duration_ms={clock.ElapsedMilliseconds} sample_bytes={sampleBytes} batch_6_bytes={batchBytes}");
}

static void TestNewConfigContainsTrustedReleaseKeys()
{
    AgentConfig config = new();
    ConfigService.ApplyConfigMigrations(config);

    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "New config should allow update_trusted_release_keys.");
    Require(config.AllowedJobTypes.Contains("uninstall_agent", StringComparer.OrdinalIgnoreCase), "New config should allow uninstall_agent.");
    Require(config.AllowedJobTypes.Contains("repair_agent", StringComparer.OrdinalIgnoreCase), "New config should allow repair_agent.");
    Require(config.ConfigMigrationVersion == ConfigService.CurrentConfigMigrationVersion, "New config should persist current migration version.");
}

static void TestLegacyDefaultConfigReceivesTrustedReleaseKeys()
{
    AgentConfig config = LegacyConfig();

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);

    Require(result.Applied, "Legacy config should receive migration.");
    Require(result.FromVersion == 1, "Legacy migration should start at v1.");
    Require(result.ToVersion == ConfigService.CurrentConfigMigrationVersion, "Legacy migration should finish at current version.");
    Require(result.AddedAllowedJobTypes.Contains("update_trusted_release_keys"), "Migration result should report added job type.");
    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Legacy config should allow update_trusted_release_keys.");
    Require(config.AllowedJobTypes.Contains("uninstall_agent", StringComparer.OrdinalIgnoreCase), "Legacy config should allow uninstall_agent.");
    Require(config.AllowedJobTypes.Contains("repair_agent", StringComparer.OrdinalIgnoreCase), "Legacy config should allow repair_agent.");
}

static void TestMigrationIsIdempotent()
{
    AgentConfig config = LegacyConfig();
    ConfigService.ApplyConfigMigrations(config);
    List<string> afterFirst = config.AllowedJobTypes.ToList();

    ConfigMigrationResult second = ConfigService.ApplyConfigMigrations(config);

    Require(!second.AddedAllowedJobTypes.Any(), "Second migration should not add job types.");
    Require(afterFirst.SequenceEqual(config.AllowedJobTypes), "Second migration should not alter allowed job types.");
}

static void TestMigrationDoesNotDuplicateJobTypes()
{
    AgentConfig config = LegacyConfig();
    config.AllowedJobTypes.Add("update_trusted_release_keys");
    config.AllowedJobTypes.Add("UPDATE_TRUSTED_RELEASE_KEYS");
    config.AllowedJobTypes.Add("uninstall_agent");
    config.AllowedJobTypes.Add("UNINSTALL_AGENT");
    config.AllowedJobTypes.Add("repair_agent");
    config.AllowedJobTypes.Add("REPAIR_AGENT");

    ConfigService.ApplyConfigMigrations(config);

    Require(config.AllowedJobTypes.Count(job => job.Equals("update_trusted_release_keys", StringComparison.OrdinalIgnoreCase)) == 1, "Migration should not duplicate update_trusted_release_keys.");
    Require(config.AllowedJobTypes.Count(job => job.Equals("uninstall_agent", StringComparison.OrdinalIgnoreCase)) == 1, "Migration should not duplicate uninstall_agent.");
    Require(config.AllowedJobTypes.Count(job => job.Equals("repair_agent", StringComparison.OrdinalIgnoreCase)) == 1, "Migration should not duplicate repair_agent.");
}

static void TestPreservesIdentityAndEndpoints()
{
    AgentConfig config = LegacyConfig();

    ConfigService.ApplyConfigMigrations(config);

    Require(config.MachineId == "machine-taxcel", "Migration should preserve machineId.");
    Require(config.AgentToken == "super-secret-token", "Migration should preserve agentToken.");
    Require(config.ServerBaseUrl == "https://nightowl.controlsul.com.br", "Migration should preserve serverBaseUrl.");
    Require(config.HeartbeatUrl.EndsWith("/api/agent/heartbeat/"), "Migration should preserve heartbeatUrl.");
    Require(config.JobsPullUrl.EndsWith("/api/agent/jobs/pull/"), "Migration should preserve jobsPullUrl.");
}

static void TestPreservesIntervals()
{
    AgentConfig config = LegacyConfig();

    ConfigService.ApplyConfigMigrations(config);

    Require(config.Intervals.HeartbeatSeconds == 123, "Migration should preserve heartbeat interval.");
    Require(config.Intervals.CollectSeconds == 456, "Migration should preserve collect interval.");
    Require(config.Intervals.JobsSeconds == 78, "Migration should preserve jobs interval.");
}

static void TestPreservesCustomAllowedJobTypes()
{
    AgentConfig config = LegacyConfig();
    config.AllowedJobTypes.Remove("collect_logs");
    config.AllowedJobTypes.Add("custom_local_job");

    ConfigService.ApplyConfigMigrations(config);

    Require(!config.AllowedJobTypes.Contains("collect_logs", StringComparer.OrdinalIgnoreCase), "Migration should preserve explicit legacy removal of unrelated job types.");
    Require(config.AllowedJobTypes.Contains("custom_local_job", StringComparer.OrdinalIgnoreCase), "Migration should preserve custom allowed job types.");
    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Migration should add the new known default to legacy configs.");
    Require(config.AllowedJobTypes.Contains("uninstall_agent", StringComparer.OrdinalIgnoreCase), "Migration should add uninstall_agent to legacy configs.");
    Require(config.AllowedJobTypes.Contains("repair_agent", StringComparer.OrdinalIgnoreCase), "Migration should add repair_agent to legacy configs.");
}

static void TestMigratedConfigDoesNotRestoreExplicitRemoval()
{
    AgentConfig config = LegacyConfig();
    config.ConfigMigrationVersion = ConfigService.CurrentConfigMigrationVersion;
    config.AllowedJobTypes.Remove("update_trusted_release_keys");

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);

    Require(!result.AddedAllowedJobTypes.Any(), "Already migrated config should not add removed job types.");
    Require(!config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Already migrated config should preserve explicit removal.");
}

static void TestMigrationResultDoesNotExposeSecrets()
{
    AgentConfig config = LegacyConfig();

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);
    string text = string.Join("|", result.AddedAllowedJobTypes) + "|" + result.FromVersion + "|" + result.ToVersion;

    Require(!text.Contains(config.AgentToken, StringComparison.OrdinalIgnoreCase), "Migration result should not expose agent token.");
    Require(!text.Contains(config.MachineId, StringComparison.OrdinalIgnoreCase), "Migration result should not expose machine id.");
}

static void TestMigrationPersistsAndReloads()
{
    string dir = CreateTempDir();
    try
    {
        string path = Path.Combine(dir, "agent.config.json");
        AgentConfig config = LegacyConfig();
        ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);

        Require(ConfigService.PersistConfigAtomic(path, config, out string error), $"Migrated config should persist atomically: {error}");

        AgentConfig reloaded = JsonSerializer.Deserialize<AgentConfig>(File.ReadAllText(path), new JsonSerializerOptions(JsonSerializerDefaults.Web)) ?? throw new InvalidOperationException("Persisted config could not be reloaded.");
        Require(result.Applied, "Legacy config should have been migrated before persistence.");
        Require(reloaded.ConfigMigrationVersion == ConfigService.CurrentConfigMigrationVersion, "Persisted config should keep migration version v2.");
    Require(reloaded.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Persisted config should include update_trusted_release_keys.");
    Require(reloaded.AllowedJobTypes.Contains("repair_agent", StringComparer.OrdinalIgnoreCase), "Persisted config should include repair_agent.");
        Require(reloaded.AgentToken == "super-secret-token", "Persisted migration should preserve agent token.");
        Require(reloaded.MachineId == "machine-taxcel", "Persisted migration should preserve machine id.");
        Require(reloaded.ServerBaseUrl == "https://nightowl.controlsul.com.br", "Persisted migration should preserve server URL.");
        Require(reloaded.Intervals.HeartbeatSeconds == 123, "Persisted migration should preserve intervals.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestPersistedMigrationIsNotReapplied()
{
    AgentConfig config = LegacyConfig();
    ConfigService.ApplyConfigMigrations(config);

    ConfigMigrationResult second = ConfigService.ApplyConfigMigrations(config);

    Require(!second.Applied, "Reloaded v2 config should not apply migration again.");
    Require(!second.AddedAllowedJobTypes.Any(), "Reloaded v2 config should not add job types again.");
}

static void TestPersistenceFailureDoesNotCorruptExistingConfig()
{
    string dir = CreateTempDir();
    try
    {
        string path = Path.Combine(dir, "agent.config.json");
        AgentConfig existing = LegacyConfig();
        existing.AgentToken = "existing-token";
        Require(ConfigService.PersistConfigAtomic(path, existing, out string initialError), $"Initial config should persist: {initialError}");
        string before = File.ReadAllText(path);

        AgentConfig migrated = LegacyConfig();
        ConfigService.ApplyConfigMigrations(migrated);
        bool persisted;
        string error;
        using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.None))
        {
            persisted = ConfigService.PersistConfigAtomic(path, migrated, out error);
        }

        Require(!persisted, "Persistence should report failure when destination cannot be replaced.");
        Require(!string.IsNullOrWhiteSpace(error), "Persistence failure should expose a sanitized error code/message.");
        Require(File.ReadAllText(path) == before, "Persistence failure should not corrupt or partially replace existing config.");
        AgentConfig reloaded = JsonSerializer.Deserialize<AgentConfig>(File.ReadAllText(path), new JsonSerializerOptions(JsonSerializerDefaults.Web)) ?? throw new InvalidOperationException("Existing config should remain valid JSON.");
        Require(reloaded.AgentToken == "existing-token", "Existing config should remain unchanged after failed persistence.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestMigrationLogPayloadDoesNotExposeSecrets()
{
    AgentConfig config = LegacyConfig();
    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);
    string line = ConfigService.BuildConfigMigrationLogLine(result, "config.migration.persist_failed", $"agentToken={config.AgentToken}");

    Require(!line.Contains(config.AgentToken, StringComparison.OrdinalIgnoreCase), "Migration log line should redact agent token.");
    Require(line.Contains("config.migration.persist_failed", StringComparison.OrdinalIgnoreCase), "Migration log line should include event type.");
}

static void TestUninstallerRunnerPayloadCopiesSelfContainedFiles()
{
    string dir = CreateTempDir();
    try
    {
        string installPath = Path.Combine(dir, "AgentDotNet");
        string runnerPath = Path.Combine(dir, "Runner", "uninstall-job");
        Directory.CreateDirectory(Path.Combine(installPath, "runtimes", "win-x64", "native"));
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Uninstaller.exe"), "fake exe");
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Uninstaller.dll"), "fake dll");
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Shared.dll"), "shared");
        File.WriteAllText(Path.Combine(installPath, "hostfxr.dll"), "runtime");
        File.WriteAllText(Path.Combine(installPath, "runtimes", "win-x64", "native", "dependency.dll"), "native");

        int copied = JobExecutor.CopyUninstallerRunnerPayload(installPath, runnerPath);

        Require(copied >= 5, "Runner payload copy should include all self-contained files.");
        Require(File.Exists(Path.Combine(runnerPath, "NightOwl.Agent.Uninstaller.exe")), "Runner payload should include uninstaller exe.");
        Require(File.Exists(Path.Combine(runnerPath, "hostfxr.dll")), "Runner payload should include runtime dependency.");
        Require(File.Exists(Path.Combine(runnerPath, "runtimes", "win-x64", "native", "dependency.dll")), "Runner payload should include nested dependencies.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestUninstallerRunnerPayloadRunsOutsideInstallPath()
{
    string dir = CreateTempDir();
    try
    {
        string installPath = Path.Combine(dir, "AgentDotNet");
        string runnerPath = Path.Combine(dir, "Updates", "Runner", "uninstall-job");
        Directory.CreateDirectory(installPath);
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Uninstaller.exe"), "fake exe");
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Uninstaller.dll"), "fake dll");
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Shared.dll"), "shared");

        UninstallerRunnerPayloadResult result = UninstallerRunnerPayload.Prepare(installPath, runnerPath);

        Require(File.Exists(result.RunnerExecutable), "Runner executable should exist.");
        Require(!result.RunnerExecutable.StartsWith(installPath, StringComparison.OrdinalIgnoreCase), "Tray uninstall runner must execute outside AgentDotNet.");
        Require(result.RunnerExecutable.StartsWith(runnerPath, StringComparison.OrdinalIgnoreCase), "Runner executable should be inside Updates Runner.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestLoadedRuntimeFromRunnerDoesNotBlockInstallPathRemoval()
{
    string dir = CreateTempDir();
    FileStream? runnerRuntimeLock = null;
    try
    {
        string installPath = Path.Combine(dir, "AgentDotNet");
        string runnerPath = Path.Combine(dir, "Updates", "Runner", "uninstall-job");
        Directory.CreateDirectory(installPath);
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Uninstaller.exe"), "fake exe");
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Uninstaller.dll"), "fake dll");
        File.WriteAllText(Path.Combine(installPath, "NightOwl.Agent.Shared.dll"), "shared");
        File.WriteAllText(Path.Combine(installPath, "clrjit.dll"), "runtime");

        UninstallerRunnerPayloadResult result = UninstallerRunnerPayload.Prepare(installPath, runnerPath);
        string runnerRuntime = Path.Combine(runnerPath, "clrjit.dll");
        runnerRuntimeLock = File.Open(runnerRuntime, FileMode.Open, FileAccess.Read, FileShare.Read);

        Directory.Delete(installPath, recursive: true);

        Require(!Directory.Exists(installPath), "AgentDotNet should be removable while runtime files are loaded from external runner.");
        Require(File.Exists(result.RunnerExecutable), "Temporary runner may remain without failing uninstall.");
    }
    finally
    {
        runnerRuntimeLock?.Dispose();
        DeleteTempDir(dir);
    }
}

static void TestTrayLocalUninstallUsesCentralAclAndAuthorizationFile()
{
    string source = FindRepoRootFile("NightOwl.Agent.Tray", "TrayApplicationContext.cs");
    string text = File.ReadAllText(source);
    Require(text.Contains("ProtectAdminOnlyFile(path, \"tray\", \"local-uninstall-authorization\")", StringComparison.Ordinal), "Tray authorization file should use central admin-only ACL.");
    Require(text.Contains("ProtectAdminOnlyTree(path, \"tray\", \"local-uninstall-runner\")", StringComparison.Ordinal), "Tray uninstall runner should use central admin-only ACL.");
    Require(!text.Contains("RunIcacls(", StringComparison.Ordinal), "Tray should not use best-effort icacls for secret authorization files.");
    Require(text.Contains("--authorization-file", StringComparison.Ordinal), "Tray should pass only an authorization file to the uninstaller.");
    Require(!text.Contains("--authorization-token", StringComparison.Ordinal), "Tray must not pass authorization token in process arguments.");
}

static void TestAgentProcessArgumentsDoNotCarrySecrets()
{
    foreach (string source in new[]
    {
        FindRepoRootFile("NightOwl.Agent.Windows", "Jobs", "JobExecutor.cs"),
        FindRepoRootFile("NightOwl.Agent.Tray", "TrayApplicationContext.cs"),
        FindRepoRootFile("NightOwl.Agent.Updater", "Program.cs"),
        FindRepoRootFile("NightOwl.Agent.Uninstaller", "Program.cs")
    })
    {
        string text = File.ReadAllText(source);
        foreach (string forbidden in new[] { "--agent-token", "--enrollment-token", "--authorization-token", "AgentToken = " })
        {
            Require(!text.Contains(forbidden, StringComparison.OrdinalIgnoreCase), $"Process argument source should not contain secret argument marker {forbidden}: {source}");
        }
    }
}

static void TestRepairRunnerScriptUsesPinnedReleaseAndNoEnrollment()
{
    AgentConfig config = new()
    {
        ServerBaseUrl = "https://nightowl.controlsul.com.br",
        InstallPath = @"C:\ProgramData\NightOwl\AgentDotNet",
        PendingResultsPath = @"C:\ProgramData\NightOwl\State\pending-results",
        AgentVersion = "0.1.1.0-rc19",
        MachineId = "machine-repair-test",
        AgentToken = "super-secret-token"
    };
    AgentJobRequest job = new()
    {
        Id = Guid.NewGuid().ToString(),
        Type = "repair_agent",
        CorrelationId = Guid.NewGuid().ToString(),
    };

    string script = JobExecutor.BuildRepairRunnerScript(
        job,
        config,
        DateTimeOffset.UtcNow,
        @"C:\ProgramData\NightOwl\AgentDotNet\Install-NightOwlAgentDotNet.ps1",
        @"C:\ProgramData\NightOwl\Trust\release-public-keys.json",
        "0.1.1.0-rc19",
        "0.1.1.0-rc19",
        "development",
        "release-id-123",
        "https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc19/NightOwl.Agent.Windows.zip",
        new string('a', 64),
        new string('b', 64),
        new string('c', 64),
        "nightowl-release-2026-02");

    Require(script.Contains("'-Repair'", StringComparison.OrdinalIgnoreCase), "Repair runner should call installer with -Repair.");
    Require(script.Contains("'-InstallAsService'", StringComparison.OrdinalIgnoreCase), "Repair runner should repair service installation.");
    Require(script.Contains("'-TrustedPublicKeysPath'", StringComparison.OrdinalIgnoreCase), "Repair runner should pass local trust bundle.");
    Require(script.Contains("'-ExpectedVersion'", StringComparison.OrdinalIgnoreCase), "Repair runner should pin expected version.");
    Require(script.Contains("0.1.1.0-rc19", StringComparison.OrdinalIgnoreCase), "Repair runner should include pinned version.");
    Require(script.Contains("release-id-123", StringComparison.OrdinalIgnoreCase), "Repair runner should include release id.");
    Require(script.Contains("enrollment_performed = $false", StringComparison.OrdinalIgnoreCase), "Repair result should state enrollment was not performed.");
    Require(!script.Contains("super-secret-token", StringComparison.OrdinalIgnoreCase), "Repair runner script must not contain the agent token.");
    Require(!script.Contains("-EnrollmentToken", StringComparison.OrdinalIgnoreCase), "Repair runner must not pass enrollment token.");
    Require(script.Contains("function Write-JsonAtomic", StringComparison.OrdinalIgnoreCase), "Repair runner should persist JSON through an atomic helper.");
    Require(script.Contains("[datetimeoffset]::UtcNow", StringComparison.OrdinalIgnoreCase), "Repair runner should use DateTimeOffset for finish timestamps.");
    Require(script.Contains("Complete-RepairJobState", StringComparison.OrdinalIgnoreCase), "Repair runner should finalize the local external-runner marker.");
    Require(script.Contains("Save-MinimalRepairFailure", StringComparison.OrdinalIgnoreCase), "Repair runner should have a non-recursive minimal failure path.");
}

static void TestExternalRepairRunnerSkipsInterruptedRecoveryUntilTimeout()
{
    JobStateRecord record = new()
    {
        JobId = Guid.NewGuid().ToString(),
        JobType = "repair_agent",
        Status = "running",
        ExternalRunnerActive = true,
        ExternalRunnerStartedAt = DateTimeOffset.UtcNow.AddMinutes(-3),
        ExternalRunnerTimeoutSeconds = 900,
        ExternalRunnerPath = @"C:\ProgramData\NightOwl\Updates\Runner\repair-test\Run-NightOwlAgentRepair.ps1",
    };

    Require(
        JobExecutionCoordinator.ShouldSkipInterruptedRecoveryForExternalRunner(record, DateTimeOffset.UtcNow),
        "Active repair external runner should not be marked interrupted during service restart.");
}

static void TestExternalRepairRunnerTimeoutAllowsInterruptedRecovery()
{
    JobStateRecord record = new()
    {
        JobId = Guid.NewGuid().ToString(),
        JobType = "repair_agent",
        Status = "running",
        ExternalRunnerActive = true,
        ExternalRunnerStartedAt = DateTimeOffset.UtcNow.AddMinutes(-30),
        ExternalRunnerTimeoutSeconds = 900,
        ExternalRunnerPath = @"C:\ProgramData\NightOwl\Updates\Runner\repair-test\Run-NightOwlAgentRepair.ps1",
    };

    Require(
        !JobExecutionCoordinator.ShouldSkipInterruptedRecoveryForExternalRunner(record, DateTimeOffset.UtcNow),
        "Expired repair external runner marker should allow JOB_INTERRUPTED recovery.");
}

static void TestRepairRunnerScriptPersistsCompletedResult()
{
    RunRepairRunnerScriptFunctionalTest(installerExitCode: 0, breakPendingDirectory: false, expectedStatus: JobFinalStatuses.Completed);
}

static void TestRepairRunnerScriptPersistsFailedResult()
{
    RunRepairRunnerScriptFunctionalTest(installerExitCode: 7, breakPendingDirectory: false, expectedStatus: JobFinalStatuses.Failed);
}

static void TestRepairRunnerScriptWritesDiagnosticWhenResultPersistenceFails()
{
    RepairRunnerTestResult result = RunRepairRunnerScriptFunctionalTest(installerExitCode: 0, breakPendingDirectory: true, expectedStatus: "");
    string runnerLog = Path.Combine(result.RunnerDir, "repair.runner.log");
    Require(File.Exists(runnerLog), "Repair runner should write a diagnostic log when result persistence fails.");
    string log = File.ReadAllText(runnerLog);
    Require(log.Contains("runner_failed", StringComparison.OrdinalIgnoreCase) || log.Contains("minimal_result_persist_failed", StringComparison.OrdinalIgnoreCase), "Repair runner diagnostic should include a failure stage.");
    Require(log.Contains(result.JobId, StringComparison.OrdinalIgnoreCase), "Repair runner diagnostic should include job_id.");
}

static void TestPendingCompletedUpdateFinalizesLocalJobStateOnRestart()
{
    string dir = CreateTempDir();
    try
    {
        string jobsDir = Path.Combine(dir, "jobs");
        string pendingDir = Path.Combine(dir, "pending-results");
        string configPath = Path.Combine(dir, "agent.config.json");
        string logPath = Path.Combine(dir, "agent.log");
        Directory.CreateDirectory(Path.GetDirectoryName(configPath)!);
        File.WriteAllText(configPath, JsonSerializer.Serialize(new AgentConfig
        {
            AgentToken = "test-token",
            MachineId = "machine-update-restart",
            AgentVersion = "0.1.1.0-rc23",
            LogPath = logPath,
            StatePath = Path.Combine(dir, "agent.state.json"),
            InstallPath = Path.Combine(dir, "AgentDotNet"),
            JobsPath = jobsDir,
            PendingResultsPath = pendingDir
        }, new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true }));

        string? previousConfig = Environment.GetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG");
        Environment.SetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG", configPath);
        try
        {
            JobStore store = new(jobsDir);
            PendingResultQueue queue = new(pendingDir);
            string jobId = Guid.NewGuid().ToString();
            store.Mark(jobId, "update_agent", "running", 1, "corr-update");
            queue.Enqueue("update_agent", NewCompletedUpdateResult(jobId), critical: true, resultId: $"update-{jobId}");

            JobExecutionCoordinator coordinator = new(new JobExecutionPolicy(store), new JsonlLogger(logPath));
            coordinator.RecoverInterruptedJobsAsync(new AgentConfig
            {
                MachineId = "machine-update-restart",
                AgentVersion = "0.1.1.0-rc23"
            }, queue, CancellationToken.None).GetAwaiter().GetResult();

            JobStateRecord state = store.Load(jobId) ?? throw new InvalidOperationException("Update job state was not reloaded.");
            Require(state.Status == JobFinalStatuses.Completed, "Pending completed update result should finalize local JobStore state.");
            Require(state.Result?.Status == JobFinalStatuses.Completed, "Recovered local JobStore state should keep the completed result.");
            Require(!state.ErrorCode.Equals(JobErrorCodes.JobInterrupted, StringComparison.OrdinalIgnoreCase), "Pending completed update result must not become JOB_INTERRUPTED.");
            Require(queue.LoadAll().Count == 1, "Recovery should not create an additional interrupted pending result.");
            JobExecutionResult pending = queue.LoadAll()[0].Payload.Deserialize<JobExecutionResult>(new JsonSerializerOptions(JsonSerializerDefaults.Web)) ?? throw new InvalidOperationException("Pending result was not deserialized.");
            Require(pending.Status == JobFinalStatuses.Completed, "Original pending result should remain completed.");
        }
        finally
        {
            Environment.SetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG", previousConfig);
        }
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestCompletedUpdateJobIsIgnoredOnLaterRestart()
{
    string dir = CreateTempDir();
    try
    {
        string jobsDir = Path.Combine(dir, "jobs");
        string pendingDir = Path.Combine(dir, "pending-results");
        string configPath = Path.Combine(dir, "agent.config.json");
        string logPath = Path.Combine(dir, "agent.log");
        Directory.CreateDirectory(Path.GetDirectoryName(configPath)!);
        File.WriteAllText(configPath, JsonSerializer.Serialize(new AgentConfig
        {
            AgentToken = "test-token",
            MachineId = "machine-update-completed",
            AgentVersion = "0.1.1.0-rc23",
            LogPath = logPath,
            StatePath = Path.Combine(dir, "agent.state.json"),
            InstallPath = Path.Combine(dir, "AgentDotNet"),
            JobsPath = jobsDir,
            PendingResultsPath = pendingDir
        }, new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true }));

        string? previousConfig = Environment.GetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG");
        Environment.SetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG", configPath);
        try
        {
            JobStore store = new(jobsDir);
            PendingResultQueue queue = new(pendingDir);
            string jobId = Guid.NewGuid().ToString();
            RemoteJobResult final = NewCompletedRemoteUpdateResult(jobId);
            store.MarkFinal(final, "corr-update");

            JobExecutionCoordinator coordinator = new(new JobExecutionPolicy(store), new JsonlLogger(logPath));
            coordinator.RecoverInterruptedJobsAsync(new AgentConfig
            {
                MachineId = "machine-update-completed",
                AgentVersion = "0.1.1.0-rc23"
            }, queue, CancellationToken.None).GetAwaiter().GetResult();

            JobStateRecord state = store.Load(jobId) ?? throw new InvalidOperationException("Completed update job state was not reloaded.");
            Require(state.Status == JobFinalStatuses.Completed, "Completed update job should remain completed after later restart.");
            Require(!state.ErrorCode.Equals(JobErrorCodes.JobInterrupted, StringComparison.OrdinalIgnoreCase), "Completed update job must not be reclassified as JOB_INTERRUPTED.");
            Require(queue.LoadAll().Count == 0, "Completed update restart should not enqueue an interrupted result.");
        }
        finally
        {
            Environment.SetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG", previousConfig);
        }
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestAgentStateHeartbeatPreservesInstalledLifecycle()
{
    string dir = CreateTempDir();
    try
    {
        string statePath = Path.Combine(dir, "agent.state.json");
        string installedAt = "2026-01-15T10:20:30Z";
        File.WriteAllText(statePath, $$"""
        {
          "machine_id": "machine-lifecycle",
          "install_status": "installed",
          "installed_at": "{{installedAt}}",
          "custom_persistent": "keep"
        }
        """);

        AgentConfig config = NewStateTestConfig(statePath);
        StateService service = new();
        AgentState state = service.Load(config);
        state.LastHeartbeatAt = DateTimeOffset.Parse("2026-01-15T10:25:00Z");
        service.SaveAsync(config, state, CancellationToken.None).GetAwaiter().GetResult();

        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(statePath));
        JsonElement root = document.RootElement;
        Require(root.GetProperty("install_status").GetString() == "installed", "Heartbeat save should preserve installed lifecycle status.");
        Require(DateTimeOffset.Parse(root.GetProperty("installed_at").GetString() ?? "") == DateTimeOffset.Parse(installedAt), "Heartbeat save should preserve installed_at.");
        Require(!root.TryGetProperty("uninstalled_at", out _), "Heartbeat save should not create uninstalled_at for installed lifecycle.");
        Require(root.GetProperty("lastHeartbeatAt").GetString() == "2026-01-15T10:25:00+00:00", "Heartbeat save should persist lastHeartbeatAt.");
        Require(root.GetProperty("custom_persistent").GetString() == "keep", "Heartbeat save should preserve unknown persistent fields.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestAgentStateJobPullAndCollectionPreserveLifecycle()
{
    string dir = CreateTempDir();
    try
    {
        string statePath = Path.Combine(dir, "agent.state.json");
        string installedAt = "2026-01-15T11:00:00Z";
        File.WriteAllText(statePath, $$"""
        {
          "machine_id": "machine-lifecycle",
          "install_status": "installed",
          "installed_at": "{{installedAt}}"
        }
        """);

        AgentConfig config = NewStateTestConfig(statePath);
        StateService service = new();
        AgentState state = service.Load(config);
        state.LastJobPullAt = DateTimeOffset.Parse("2026-01-15T11:05:00Z");
        state.LastCollectionAt = DateTimeOffset.Parse("2026-01-15T11:06:00Z");
        service.SaveAsync(config, state, CancellationToken.None).GetAwaiter().GetResult();

        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(statePath));
        JsonElement root = document.RootElement;
        Require(root.GetProperty("install_status").GetString() == "installed", "Job pull save should preserve installed lifecycle status.");
        Require(DateTimeOffset.Parse(root.GetProperty("installed_at").GetString() ?? "") == DateTimeOffset.Parse(installedAt), "Job pull save should preserve installed_at.");
        Require(root.GetProperty("lastJobPullAt").GetString() == "2026-01-15T11:05:00+00:00", "Job pull save should persist lastJobPullAt.");
        Require(root.GetProperty("lastCollectionAt").GetString() == "2026-01-15T11:06:00+00:00", "Collection save should persist lastCollectionAt.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestAgentStateRuntimeDoesNotRewriteUninstalledLifecycle()
{
    string dir = CreateTempDir();
    try
    {
        string statePath = Path.Combine(dir, "agent.state.json");
        string uninstalledAt = "2026-01-15T12:00:00Z";
        File.WriteAllText(statePath, $$"""
        {
          "machine_id": "machine-lifecycle",
          "install_status": "uninstalled",
          "uninstalled_at": "{{uninstalledAt}}"
        }
        """);

        AgentConfig config = NewStateTestConfig(statePath);
        StateService service = new();
        AgentState state = service.Load(config);
        state.LastHeartbeatAt = DateTimeOffset.Parse("2026-01-15T12:05:00Z");
        service.SaveAsync(config, state, CancellationToken.None).GetAwaiter().GetResult();

        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(statePath));
        JsonElement root = document.RootElement;
        Require(root.GetProperty("install_status").GetString() == "uninstalled", "Runtime save must not rewrite uninstalled lifecycle status.");
        Require(DateTimeOffset.Parse(root.GetProperty("uninstalled_at").GetString() ?? "") == DateTimeOffset.Parse(uninstalledAt), "Runtime save should preserve uninstalled_at.");
        Require(!root.TryGetProperty("installed_at", out _), "Runtime save should not create installed_at for uninstalled lifecycle.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestAgentStateRuntimePreservesUnknownPropertiesAndRecentJobs()
{
    string dir = CreateTempDir();
    try
    {
        string statePath = Path.Combine(dir, "agent.state.json");
        File.WriteAllText(statePath, """
        {
          "machine_id": "machine-lifecycle",
          "install_status": "installed",
          "installed_at": "2026-01-15T13:00:00Z",
          "recentJobIds": ["job-old"],
          "future_field": {
            "nested": true
          }
        }
        """);

        AgentConfig config = NewStateTestConfig(statePath);
        StateService service = new();
        AgentState state = service.Load(config);
        state.RememberJob("job-new");
        service.SaveAsync(config, state, CancellationToken.None).GetAwaiter().GetResult();

        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(statePath));
        JsonElement root = document.RootElement;
        Require(root.GetProperty("install_status").GetString() == "installed", "Recent job save should preserve installed lifecycle status.");
        Require(root.GetProperty("future_field").GetProperty("nested").GetBoolean(), "Runtime save should preserve unknown JSON objects.");
        string[] recentJobIds = root.GetProperty("recentJobIds").EnumerateArray().Select(item => item.GetString() ?? "").ToArray();
        Require(recentJobIds.Contains("job-old"), "Runtime save should preserve existing recentJobIds.");
        Require(recentJobIds.Contains("job-new"), "Runtime save should persist new recentJobIds.");
        Require(!Directory.EnumerateFiles(dir, "*.tmp").Any(), "Successful state save should not leave temp files.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static AgentConfig NewStateTestConfig(string statePath)
{
    return new AgentConfig
    {
        MachineId = "machine-lifecycle",
        StatePath = statePath
    };
}

static void TestStateSaveFailureDoesNotEscapeWorkerBoundary()
{
    string dir = CreateTempDir();
    try
    {
        string logPath = Path.Combine(dir, "agent.log");
        JsonlLogger logger = new(logPath);
        bool saveAttempted = false;

        StateSaveOutcome outcome = Worker.SaveStateWithBoundaryAsync(
            _ =>
            {
                saveAttempted = true;
                throw new UnauthorizedAccessException("Synthetic access denied while saving state.");
            },
            logger,
            TimeSpan.FromSeconds(10),
            CancellationToken.None,
            (_, _) => Task.CompletedTask).GetAwaiter().GetResult();

        Require(saveAttempted, "Worker state save boundary should attempt persistence.");
        Require(!outcome.Saved, "Worker state save boundary should report failed save.");
        Require(outcome.NextBackoff == TimeSpan.FromSeconds(20), "Worker state save boundary should increase backoff after failure.");
        string log = File.ReadAllText(logPath);
        Require(log.Contains("state.save.failed", StringComparison.Ordinal), "State save failure should be logged as a structured event.");
        Require(log.Contains("STATE_SAVE_UNAUTHORIZED", StringComparison.Ordinal), "Unauthorized state save failure should get a specific error code.");
        Require(!log.Contains("agentToken", StringComparison.OrdinalIgnoreCase), "State save failure log should not contain agent token material.");

        StateSaveOutcome ioOutcome = Worker.SaveStateWithBoundaryAsync(
            _ => throw new IOException("Synthetic IO failure while saving state."),
            logger,
            TimeSpan.FromSeconds(10),
            CancellationToken.None,
            (_, _) => Task.CompletedTask).GetAwaiter().GetResult();
        Require(!ioOutcome.Saved, "Worker state save boundary should contain IOException.");

        bool unexpectedEscaped = false;
        try
        {
            Worker.SaveStateWithBoundaryAsync(
                _ => throw new InvalidOperationException("Synthetic unexpected state save failure."),
                logger,
                TimeSpan.FromSeconds(10),
                CancellationToken.None,
                (_, _) => Task.CompletedTask).GetAwaiter().GetResult();
        }
        catch (InvalidOperationException)
        {
            unexpectedEscaped = true;
        }
        Require(unexpectedEscaped, "Unexpected state save exceptions should escape to normal BackgroundService failure handling.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static void TestStateSaveFailureBackoffPreventsAcceleratedLoop()
{
    string dir = CreateTempDir();
    try
    {
        string logPath = Path.Combine(dir, "agent.log");
        JsonlLogger logger = new(logPath);
        List<TimeSpan> delays = new();

        StateSaveOutcome first = Worker.SaveStateWithBoundaryAsync(
            _ => throw new IOException("Synthetic transient IO failure."),
            logger,
            TimeSpan.FromSeconds(10),
            CancellationToken.None,
            (delay, _) =>
            {
                delays.Add(delay);
                return Task.CompletedTask;
            }).GetAwaiter().GetResult();
        StateSaveOutcome second = Worker.SaveStateWithBoundaryAsync(
            _ => throw new IOException("Synthetic transient IO failure."),
            logger,
            first.NextBackoff,
            CancellationToken.None,
            (delay, _) =>
            {
                delays.Add(delay);
                return Task.CompletedTask;
            }).GetAwaiter().GetResult();
        StateSaveOutcome success = Worker.SaveStateWithBoundaryAsync(
            _ => Task.CompletedTask,
            logger,
            second.NextBackoff,
            CancellationToken.None,
            (delay, _) =>
            {
                delays.Add(delay);
                return Task.CompletedTask;
            }).GetAwaiter().GetResult();

        Require(delays.SequenceEqual(new[] { TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(20) }), "Worker should delay between repeated state save failures.");
        Require(first.NextBackoff == TimeSpan.FromSeconds(20), "First failed state save should double backoff.");
        Require(second.NextBackoff == TimeSpan.FromSeconds(40), "Second failed state save should continue bounded backoff.");
        Require(success.Saved, "Successful state save should report success.");
        Require(success.NextBackoff == TimeSpan.FromSeconds(10), "Successful state save should reset backoff.");
        string log = File.ReadAllText(logPath);
        Require(log.Contains("STATE_SAVE_IO_FAILED", StringComparison.Ordinal), "IO state save failure should get a specific error code.");
    }
    finally
    {
        DeleteTempDir(dir);
    }
}

static JobExecutionResult NewCompletedUpdateResult(string jobId)
{
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return new JobExecutionResult
    {
        JobId = jobId,
        Status = JobFinalStatuses.Completed,
        StartedAt = now.AddSeconds(-5),
        FinishedAt = now,
        DurationSeconds = 5,
        ExitCode = 0,
        Stdout = "Agent updated successfully.",
        Result = new
        {
            type = "update_agent",
            update_status = "success",
            installed_version = "0.1.1.0-rc23",
            previous_version = "0.1.1.0-rc22",
            target_version = "0.1.1.0-rc23",
            rollback_performed = false,
            health_check = new { confirmed = true }
        }
    };
}

static RemoteJobResult NewCompletedRemoteUpdateResult(string jobId)
{
    JobExecutionResult result = NewCompletedUpdateResult(jobId);
    return new RemoteJobResult
    {
        JobId = jobId,
        JobType = "update_agent",
        Status = result.Status,
        StartedAt = result.StartedAt,
        CompletedAt = result.FinishedAt,
        DurationMs = (long)Math.Round(result.DurationSeconds * 1000),
        Attempt = 1,
        Output = result.Result,
        AgentVersion = "0.1.1.0-rc23",
        MachineId = "machine-update-completed"
    };
}

static RepairRunnerTestResult RunRepairRunnerScriptFunctionalTest(int installerExitCode, bool breakPendingDirectory, string expectedStatus)
{
    string powershell = GetWindowsPowerShellPath();
    Require(File.Exists(powershell), "Windows PowerShell 5.1 is required for repair runner functional tests.");

    string root = CreateTempDir();
    try
    {
        string installPath = Path.Combine(root, "AgentDotNet");
        string pendingDir = Path.Combine(root, "pending-results");
        string jobsDir = Path.Combine(root, "jobs");
        string runnerDir = Path.Combine(root, "runner");
        string trustPath = Path.Combine(root, "Trust", "release-public-keys.json");
        Directory.CreateDirectory(installPath);
        Directory.CreateDirectory(pendingDir);
        Directory.CreateDirectory(jobsDir);
        Directory.CreateDirectory(runnerDir);
        Directory.CreateDirectory(Path.GetDirectoryName(trustPath)!);
        File.WriteAllText(trustPath, "{}");
        File.WriteAllText(Path.Combine(installPath, "agent.config.json"), "{}");

        string installerPath = Path.Combine(root, "FakeInstall-NightOwlAgentDotNet.ps1");
        File.WriteAllText(installerPath, @"
param(
    [switch]$Repair,
    [switch]$InstallAsService,
    [string]$ServerUrl,
    [string]$PackageUrl,
    [string]$TrustedPublicKeysPath,
    [string]$ExpectedVersion,
    [string]$ExpectedChannel,
    [string]$ExpectedPackageSha256,
    [string]$ExpectedReleaseId,
    [switch]$RunCheck,
    [switch]$NonInteractive
)
Set-Content -Path '" + Path.Combine(installPath, "agent.version.json").Replace("'", "''") + @"' -Value ('{""version"":""' + $ExpectedVersion + '""}') -Encoding UTF8
Write-Output 'status=completed'
Write-Output 'operation=repair'
Write-Output ('installed_version=' + $ExpectedVersion)
exit " + installerExitCode.ToString(System.Globalization.CultureInfo.InvariantCulture) + @"
");

        if (breakPendingDirectory)
        {
            Directory.Delete(pendingDir);
            File.WriteAllText(pendingDir, "not a directory");
        }

        string jobId = Guid.NewGuid().ToString();
        JobStore store = new(jobsDir);
        store.Mark(jobId, "repair_agent", "running", 1, "corr-repair");
        string jobStatePath = store.PathFor(jobId);
        store.MarkExternalRunnerStarted(jobId, "repair_agent", Path.Combine(runnerDir, "Run-NightOwlAgentRepair.ps1"), 900);

        AgentConfig config = new()
        {
            ServerBaseUrl = "https://nightowl.controlsul.com.br",
            InstallPath = installPath,
            PendingResultsPath = pendingDir,
            AgentVersion = "0.1.1.0-rc23",
            MachineId = "machine-repair-test",
            AgentToken = "super-secret-token"
        };
        AgentJobRequest job = new()
        {
            Id = jobId,
            Type = "repair_agent",
            Attempt = 1,
            CorrelationId = "corr-repair",
        };
        string script = JobExecutor.BuildRepairRunnerScript(
            job,
            config,
            DateTimeOffset.UtcNow.AddSeconds(-1),
            installerPath,
            trustPath,
            "0.1.1.0-rc23",
            "0.1.1.0-rc23",
            "development",
            "release-id-123",
            "https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc23/NightOwl.Agent.Windows.zip",
            new string('a', 64),
            new string('b', 64),
            new string('c', 64),
            "nightowl-release-2026-02",
            jobStatePath);
        string runnerScript = Path.Combine(runnerDir, "Run-NightOwlAgentRepair.ps1");
        File.WriteAllText(runnerScript, script);

        using Process process = Process.Start(new ProcessStartInfo
        {
            FileName = powershell,
            Arguments = "-NoProfile -ExecutionPolicy Bypass -File " + QuoteArg(runnerScript),
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        }) ?? throw new InvalidOperationException("Failed to start repair runner functional test.");
        string stdout = process.StandardOutput.ReadToEnd();
        string stderr = process.StandardError.ReadToEnd();
        process.WaitForExit(15000);
        Require(process.HasExited, "Repair runner script did not exit.");

        if (!breakPendingDirectory)
        {
            string resultPath = Path.Combine(pendingDir, $"job-result-{jobId}.json");
            Require(File.Exists(resultPath), "Repair runner should write pending result.");
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(resultPath));
            JsonElement rootElement = document.RootElement;
            Require(rootElement.GetProperty("status").GetString() == expectedStatus, $"Repair runner pending result should be {expectedStatus}.");
            Require(rootElement.GetProperty("duration_seconds").ValueKind == JsonValueKind.Number, "Repair runner duration_seconds should be numeric.");
            Require(rootElement.GetProperty("duration_seconds").GetDouble() >= 0, "Repair runner duration_seconds should be non-negative.");
            JsonElement resultElement = rootElement.GetProperty("result");
            Require(resultElement.GetProperty("type").GetString() == "repair_agent", "Repair runner result type mismatch.");
            Require(resultElement.GetProperty("installed_version").GetString() == "0.1.1.0-rc23", "Repair runner installed version mismatch.");

            JobStateRecord state = store.Load(jobId) ?? throw new InvalidOperationException("Repair job state was not reloaded.");
            Require(!state.ExternalRunnerActive, "Repair runner should finalize external runner marker.");
        }

        return new RepairRunnerTestResult(root, runnerDir, jobId, stdout, stderr);
    }
    catch
    {
        DeleteTempDir(root);
        throw;
    }
}

static string QuoteArg(string value)
{
    return "\"" + value.Replace("\"", "\\\"") + "\"";
}

static string GetWindowsPowerShellPath()
{
    string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
    if (File.Exists(path))
    {
        return path;
    }
    return "powershell.exe";
}

static string FindRepoRootFile(params string[] relativeParts)
{
    DirectoryInfo? dir = new(AppContext.BaseDirectory);
    while (dir is not null)
    {
        string candidate = Path.Combine(new[] { dir.FullName }.Concat(relativeParts).ToArray());
        if (File.Exists(candidate))
        {
            return candidate;
        }
        dir = dir.Parent;
    }

    throw new FileNotFoundException("Could not find repository file.", Path.Combine(relativeParts));
}

static string CreateTempDir()
{
    string path = Path.Combine(Path.GetTempPath(), "NightOwlConfigTests", Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(path);
    return path;
}

static void DeleteTempDir(string path)
{
    try
    {
        Directory.Delete(path, recursive: true);
    }
    catch
    {
        // Best-effort cleanup for local tests.
    }
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}

sealed record RepairRunnerTestResult(string RootDir, string RunnerDir, string JobId, string Stdout, string Stderr);

sealed class FakeTelemetryHttpClientFactory : IHttpClientFactory
{
    public bool Succeed { get; set; } = true;
    public bool LoseResponseAfterPersist { get; set; }
    public int Calls { get; private set; }
    public List<Guid> SampleIds { get; } = new();
    public HashSet<Guid> PersistedSampleIds { get; } = new();

    public HttpClient CreateClient(string name)
    {
        return new HttpClient(new FakeTelemetryHandler(this));
    }

    private sealed class FakeTelemetryHandler(FakeTelemetryHttpClientFactory owner) : HttpMessageHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            owner.Calls++;
            using JsonDocument body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellationToken));
            foreach (JsonElement sample in body.RootElement.GetProperty("samples").EnumerateArray())
            {
                Guid id = sample.GetProperty("sample_id").GetGuid();
                owner.SampleIds.Add(id);
                if (owner.Succeed) owner.PersistedSampleIds.Add(id);
            }
            if (owner.LoseResponseAfterPersist)
                throw new HttpRequestException("synthetic response lost after persistence");
            return new HttpResponseMessage(owner.Succeed ? HttpStatusCode.OK : HttpStatusCode.ServiceUnavailable)
            {
                Content = new StringContent(owner.Succeed ? "{}" : "offline"),
            };
        }
    }
}
