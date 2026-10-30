using System.Text.Json;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows;

internal static class HardwareCadenceTests
{
    internal static void Run()
    {
        void Check(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
        DateTimeOffset now = DateTimeOffset.Parse("2026-10-29T12:00:00Z");
        AgentState state = new();
        Check(InventoryPlan.Due(state, now) == new InventoryPlan(true, true, true, true), "First inventory must collect all groups.");
        state.LastBasicInventoryAt = state.LastDetailedHardwareAt = state.LastVolumesAt = state.LastNetworkInventoryAt = now;
        Check(!InventoryPlan.Due(state, now.AddMinutes(59)).Any, "Hourly inventory ran early.");
        Check(InventoryPlan.Due(state, now.AddHours(1)) == new InventoryPlan(false, false, true, false), "Hourly cadence must contain volumes only.");
        Check(InventoryPlan.Due(state, now.AddDays(1)) == new InventoryPlan(true, false, true, true), "Daily inventory must omit deep hardware.");
        Check(InventoryPlan.Due(state, now.AddDays(7)).Detailed, "Weekly hardware missing.");
        AgentState restored = JsonSerializer.Deserialize<AgentState>(JsonSerializer.Serialize(state))!;
        Check(!InventoryPlan.Due(restored, now).Any, "Cadence was lost on restart.");
        Check(InventoryPlan.Due(restored, now.AddDays(-1)).Any, "Future checkpoints must recover after a clock correction.");
        restored.LastAncillaryInventoryAt = now;
        Check(InventoryPlan.Due(restored, now.AddMinutes(30), 1800).Ancillary &&
            !InventoryPlan.Due(restored, now.AddMinutes(29), 1800).Ancillary,
            "Existing software/security/patch cadence must retain collectSeconds.");
        AgentState partial = new();
        Worker.RecordInventoryCheckpoint(partial, new(true, true, true, true), new()
        {
            System = new() { ["hostname"] = "SYNTH" },
            SectionStates = new() { ["disk"] = "failed" }
        }, now);
        Check(partial.LastBasicInventoryAt == now && partial.LastDetailedHardwareAt is null &&
            partial.LastVolumesAt is null && partial.LastNetworkInventoryAt is null,
            "A partially failed collection must not checkpoint failed groups as complete.");

        Dictionary<string, object?> cpu = (Dictionary<string, object?>)WindowsInventoryCollector.GetHardware(new()
        {
            ["cpu_name"] = "Synthetic CPU", ["cpu_max_clock_mhz"] = 3600L,
            ["physical_cores"] = 6L, ["logical_processors"] = 12L, ["board_product"] = "Synthetic board",
            ["board_version"] = "A1", ["system_sku"] = "SYNTHETIC-SKU"
        })["cpu"]!;
        Check(Equals(cpu["max_clock_mhz"], 3600L) && Equals(cpu["max_clock_ghz"], 3.6d), "CPU maximum unit conversion changed the original value.");
        Check(Equals(cpu["clock_kind"], "reported_maximum_not_instantaneous"), "CPU maximum must not be called instantaneous.");
        Dictionary<string, object?> unknown = (Dictionary<string, object?>)WindowsInventoryCollector.GetHardware(new())["cpu"]!;
        Check(unknown["name"] is null && unknown["physical_cores"] is null && unknown["logical_processors"] is null,
            "Absent OEM data must not be substituted with process architecture or quota.");

        Dictionary<string, object?> memory = HardwareInventoryNormalizer.Memory(new()
        {
            ["modules_query_succeeded"] = true,
            ["memory_slots"] = new object?[] { 2L }, ["maximum_capacity_bytes"] = new object?[] { 64L * 1073741824 },
            ["modules"] = new object?[] {
                new Dictionary<string, object?> { ["capacity_bytes"] = 8L * 1073741824, ["memory_type"] = 26L, ["part_number"] = "SYNTH-A", ["speed_mhz"] = 3200L, ["configured_speed_mhz"] = 2666L },
                new Dictionary<string, object?> { ["capacity_bytes"] = 16L * 1073741824, ["memory_type"] = 26L, ["part_number"] = "SYNTH-B" }
            }
        }, 24L * 1073741824);
        Check(Equals(memory["slots_free"], 0L) && Equals(memory["maximum_supported_bytes"], 64L * 1073741824), "Reported memory topology was not retained.");
        List<Dictionary<string, object?>> modules = (List<Dictionary<string, object?>>)memory["modules"]!;
        Check(Equals(modules[0]["memory_type"], "ddr4") && Equals(modules[0]["configured_speed_mhz"], 2666L) && modules[1]["speed_mhz"] is null,
            "Mixed modules/unknown configured speed were conflated.");
        Dictionary<string, object?> vm = HardwareInventoryNormalizer.Memory(new() { ["modules_query_succeeded"] = true }, 8L * 1073741824);
        Check(vm["slots_used"] is null && Equals(vm["status"], "unknown"), "A VM without physical modules must not imply agent failure or free slots.");
        Dictionary<string, object?> battery = HardwareInventoryNormalizer.Battery(new()
        {
            ["query_succeeded"] = true, ["present"] = true, ["design_capacity_mwh"] = 50000L,
            ["full_charge_capacity_mwh"] = 40000L, ["manufacturer"] = "Synthetic OEM", ["model"] = "SYNTH-BAT"
        });
        Check(Equals(battery["health_percent"], 80d) && battery["part_number"] is null && Equals(battery["model"], "SYNTH-BAT"),
            "Battery identity must preserve available evidence without inventing a replacement part.");
        Check(Equals(HardwareInventoryNormalizer.Battery(new() { ["query_succeeded"] = true, ["present"] = false })["collection_status"], "not_present"), "Desktop absence is not a collection failure.");
        Check(Equals(HardwareInventoryNormalizer.Battery(new())["collection_status"], "failed"), "Failed battery query must not report absence.");

        string source = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "NightOwl.Agent.Windows", "Collectors", "WindowsInventoryCollector.cs"));
        foreach (string type in new[] { "Win32_ComputerSystem", "Win32_OperatingSystem", "Win32_BIOS", "Win32_Battery" })
            Check(source.Split("Get-CimInstance " + type + " ", StringSplitOptions.None).Length == 2, "Duplicated core CIM query: " + type);
        Check(source.Contains("GetSecurityCore(software, bitlocker)", StringComparison.Ordinal) && source.Contains("GetDisksCore(bitlocker)", StringComparison.Ordinal), "Software/BitLocker must be shared by the aggregate collection.");
        Console.WriteLine("Hardware cadence/normalization tests passed (schedule, restart, CPU, mixed RAM, VM, battery, query consolidation).");
    }
}
