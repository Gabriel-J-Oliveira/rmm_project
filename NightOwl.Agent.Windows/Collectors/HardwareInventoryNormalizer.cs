using System.Globalization;

namespace NightOwl.Agent.Windows.Collectors;

internal static class HardwareInventoryNormalizer
{
    internal static Dictionary<string, object?> MergeHardware(Dictionary<string, object?> core,
        Dictionary<string, object?> enrichment, Dictionary<string, object?> storage, string systemDrive)
    {
        Dictionary<string, object?> hardware = new(core);
        hardware["memory"] = Memory(enrichment, Long(core.GetValueOrDefault("memory_total_bytes")));
        hardware["battery"] = Battery(
            enrichment.GetValueOrDefault("battery") as Dictionary<string, object?> ?? new(),
            core.GetValueOrDefault("battery_present") as bool?, core.GetValueOrDefault("battery_status"));
        hardware["physical_disks"] = PhysicalDisks(storage, systemDrive);
        return hardware;
    }

    internal static Dictionary<string, object?> Memory(Dictionary<string, object?> source, long? reportedTotalBytes)
    {
        List<Dictionary<string, object?>> modules = new();
        foreach (Dictionary<string, object?> raw in Rows(source.GetValueOrDefault("modules")))
        {
            long? capacity = PositiveLong(raw.GetValueOrDefault("capacity_bytes"));
            modules.Add(new Dictionary<string, object?>
            {
                ["device_locator"] = Text(raw.GetValueOrDefault("device_locator")),
                ["bank_label"] = Text(raw.GetValueOrDefault("bank_label")),
                ["capacity_bytes"] = capacity,
                ["speed_mhz"] = PositiveLong(raw.GetValueOrDefault("speed_mhz")),
                ["configured_speed_mhz"] = PositiveLong(raw.GetValueOrDefault("configured_speed_mhz")),
                ["manufacturer"] = Text(raw.GetValueOrDefault("manufacturer")),
                ["part_number"] = Text(raw.GetValueOrDefault("part_number")),
                ["serial_number"] = Text(raw.GetValueOrDefault("serial_number")),
                ["form_factor"] = FormFactor(raw.GetValueOrDefault("form_factor")),
                ["memory_type"] = MemoryType(raw.GetValueOrDefault("memory_type"))
            });
        }

        List<long?> slots = Values(source.GetValueOrDefault("memory_slots"))
            .Select(Long)
            .ToList();
        long? slotsTotal = slots.Count > 0 && slots.All(value => value is > 0 and <= 1024)
            ? slots.Sum(value => value!.Value)
            : null;
        long? moduleTotal = modules.Count > 0 && modules.All(module => module["capacity_bytes"] is long)
            ? modules.Sum(module => (long)module["capacity_bytes"]!)
            : null;
        int? slotsUsed = source.GetValueOrDefault("modules_query_succeeded") is false ||
                         modules.Count == 0 && reportedTotalBytes is > 0 ? null : modules.Count;
        return new Dictionary<string, object?>
        {
            ["total_bytes"] = PositiveLong(reportedTotalBytes) ?? moduleTotal,
            ["slots_total"] = slotsTotal,
            ["slots_used"] = slotsUsed,
            ["slots_free"] = slotsTotal is null || slotsUsed is null ? null : Math.Max(slotsTotal.Value - slotsUsed.Value, 0),
            ["modules"] = modules
        };
    }

    internal static Dictionary<string, object?> Battery(Dictionary<string, object?> source, bool? legacyPresent = null,
        object? legacyStatus = null)
    {
        bool querySucceeded = source.GetValueOrDefault("query_succeeded") is true;
        bool? present = querySucceeded ? source.GetValueOrDefault("present") is true : legacyPresent;
        long? design = present is true ? BatteryCapacity(source.GetValueOrDefault("design_capacity_mwh")) : null;
        long? full = present is true ? BatteryCapacity(source.GetValueOrDefault("full_charge_capacity_mwh")) : null;
        return new Dictionary<string, object?>
        {
            ["present"] = present,
            ["status"] = present is true ? BatteryStatus(source.GetValueOrDefault("status_code") ?? legacyStatus)
                : present is false ? "not_present" : "unknown",
            ["estimated_charge_remaining"] = present is true ? Percent(source.GetValueOrDefault("estimated_charge_remaining")) : null,
            ["design_capacity_mwh"] = design,
            ["full_charge_capacity_mwh"] = full,
            ["health_percent"] = design is > 0 && full is > 0
                ? Math.Round(Math.Clamp((double)full.Value / design.Value * 100, 0, 100), 1)
                : null,
            ["cycle_count"] = present is true ? BatteryCycleCount(source.GetValueOrDefault("cycle_count")) : null
        };
    }

    internal static List<Dictionary<string, object?>> PhysicalDisks(Dictionary<string, object?> source, string systemDrive)
    {
        string? systemLetter = NormalizeLetter(systemDrive);
        List<Dictionary<string, object?>> physical = Rows(source.GetValueOrDefault("physical_disks")).ToList();
        List<Dictionary<string, object?>> osDisks = Rows(source.GetValueOrDefault("os_disks")).ToList();
        List<Dictionary<string, object?>> wmi = Rows(source.GetValueOrDefault("wmi_disks")).ToList();
        List<Dictionary<string, object?>> result = new();
        foreach (Dictionary<string, object?> raw in physical)
        {
            Dictionary<string, object?> disk = DiskRow(raw);
            AttachOsDisk(disk, raw, physical, osDisks, systemLetter);
            result.Add(disk);
        }
        foreach (Dictionary<string, object?> raw in wmi)
        {
            if (IsUniqueAcross(raw, wmi, physical, "serial_number")) continue;
            Dictionary<string, object?> disk = DiskRow(raw);
            disk["disk_number"] = NonNegativeLong(raw.GetValueOrDefault("disk_number"));
            AttachOsDisk(disk, raw, wmi, osDisks, systemLetter);
            result.Add(disk);
        }
        return result;
    }

    private static Dictionary<string, object?> DiskRow(Dictionary<string, object?> raw) => new()
    {
        ["disk_number"] = null,
        ["device_id"] = Text(raw.GetValueOrDefault("device_id")),
        ["model"] = Text(raw.GetValueOrDefault("model")) ?? Text(raw.GetValueOrDefault("friendly_name")),
        ["manufacturer"] = Text(raw.GetValueOrDefault("manufacturer")),
        ["serial_number"] = Text(raw.GetValueOrDefault("serial_number")),
        ["firmware_version"] = Text(raw.GetValueOrDefault("firmware_version")),
        ["size_bytes"] = PositiveLong(raw.GetValueOrDefault("size_bytes")),
        ["media_type"] = MediaType(raw.GetValueOrDefault("media_type")),
        ["bus_type"] = BusType(raw.GetValueOrDefault("bus_type")),
        ["health_status"] = HealthStatus(raw.GetValueOrDefault("health_status")),
        ["operational_status"] = OperationalStatus(raw.GetValueOrDefault("operational_status")),
        ["is_system_disk"] = null,
        ["drive_letters"] = new List<string>(),
        ["association_source"] = "none",
        ["association_confidence"] = "none"
    };

    private static void AttachOsDisk(Dictionary<string, object?> disk, Dictionary<string, object?> raw,
        List<Dictionary<string, object?>> peers, List<Dictionary<string, object?>> osDisks, string? systemLetter)
    {
        string? serial = StrongId(raw.GetValueOrDefault("serial_number"));
        if (serial is not null && (peers.Count(row => SameId(row.GetValueOrDefault("serial_number"), serial)) != 1 ||
                                   osDisks.Count(row => SameId(row.GetValueOrDefault("serial_number"), serial)) > 1))
            return;
        foreach (string key in new[] { "unique_id", "serial_number" })
        {
            string? id = StrongId(raw.GetValueOrDefault(key));
            if (id is null || peers.Count(row => SameId(row.GetValueOrDefault(key), id)) != 1) continue;
            List<Dictionary<string, object?>> matches = osDisks
                .Where(row => SameId(row.GetValueOrDefault(key), id)).ToList();
            if (matches.Count != 1 || IsLayered(matches[0]) || IsLayered(raw)) continue;
            Dictionary<string, object?> os = matches[0];
            disk["disk_number"] = NonNegativeLong(os.GetValueOrDefault("disk_number"));
            List<string> letters = Values(os.GetValueOrDefault("drive_letters"))
                .Select(NormalizeLetter).OfType<string>()
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(letter => letter, StringComparer.OrdinalIgnoreCase).ToList();
            disk["drive_letters"] = letters;
            disk["is_system_disk"] = letters.Count == 0 || systemLetter is null
                ? null : letters.Contains(systemLetter, StringComparer.OrdinalIgnoreCase);
            disk["association_source"] = key;
            disk["association_confidence"] = "high";
            if ((string)disk["bus_type"]! == "unknown") disk["bus_type"] = BusType(os.GetValueOrDefault("bus_type"));
            return;
        }
    }

    private static bool IsUniqueAcross(Dictionary<string, object?> row, List<Dictionary<string, object?>> left,
        List<Dictionary<string, object?>> right, string key)
    {
        string? id = StrongId(row.GetValueOrDefault(key));
        return id is not null && left.Count(item => SameId(item.GetValueOrDefault(key), id)) == 1 &&
               right.Count(item => SameId(item.GetValueOrDefault(key), id)) == 1;
    }

    private static bool SameId(object? value, string id) =>
        string.Equals(StrongId(value), id, StringComparison.OrdinalIgnoreCase);

    private static string? StrongId(object? value)
    {
        string? id = Text(value);
        if (id is null || id.Length < 4) return null;
        string compact = new(id.Where(char.IsLetterOrDigit).ToArray());
        if (compact.Length < 4 || compact.All(character => character == compact[0])) return null;
        return id.Equals("unknown", StringComparison.OrdinalIgnoreCase) ||
               id.Equals("none", StringComparison.OrdinalIgnoreCase) ||
               id.Equals("n/a", StringComparison.OrdinalIgnoreCase) ||
               id.Equals("not available", StringComparison.OrdinalIgnoreCase) ||
               id.Equals("default string", StringComparison.OrdinalIgnoreCase) ||
               id.Equals("to be filled by o.e.m.", StringComparison.OrdinalIgnoreCase) ||
               id.Equals("not specified", StringComparison.OrdinalIgnoreCase) ? null : id;
    }

    private static bool IsLayered(Dictionary<string, object?> row)
    {
        string bus = BusType(row.GetValueOrDefault("bus_type"));
        string description = string.Join(" ", new[] { "model", "friendly_name", "pnp_device_id" }
            .Select(key => Text(row.GetValueOrDefault(key)) ?? "")).ToLowerInvariant();
        return bus is "raid" or "virtual" or "spaces" or "iscsi" or "fibre_channel" ||
               description.Contains("virtual") || description.Contains("storage space") ||
               description.Contains("raid") || description.Contains("vmbus") ||
               description.Contains("vmware") || description.Contains("vbox") || description.Contains("qemu");
    }

    private static IEnumerable<Dictionary<string, object?>> Rows(object? value)
    {
        if (value is Dictionary<string, object?> row) return new[] { row };
        if (value is IEnumerable<object?> values) return values.OfType<Dictionary<string, object?>>();
        return Array.Empty<Dictionary<string, object?>>();
    }

    private static IEnumerable<object?> Values(object? value) => value switch
    {
        null => Array.Empty<object?>(),
        IEnumerable<object?> list => list,
        _ => new[] { value }
    };

    private static string? Text(object? value)
    {
        string? text = value?.ToString()?.Trim();
        return string.IsNullOrWhiteSpace(text) ? null : text;
    }

    private static long? Long(object? value) => long.TryParse(value?.ToString(), NumberStyles.Integer,
        CultureInfo.InvariantCulture, out long parsed) ? parsed : null;

    private static long? PositiveLong(object? value) => Long(value) is > 0 and var parsed ? parsed : null;
    private static long? NonNegativeLong(object? value) => Long(value) is >= 0 and var parsed ? parsed : null;
    private static long? Percent(object? value) => Long(value) is >= 0 and <= 100 and var parsed ? parsed : null;
    private static long? BatteryCapacity(object? value) => PositiveLong(value) is < uint.MaxValue and var parsed ? parsed : null;
    private static long? BatteryCycleCount(object? value) => NonNegativeLong(value) is < uint.MaxValue and var parsed ? parsed : null;

    private static string? NormalizeLetter(object? value)
    {
        string? text = Text(value)?.TrimEnd('\\');
        if (text is null || text.Length == 0 || !char.IsLetter(text[0])) return null;
        if (text.Length != 1 && !(text.Length == 2 && text[1] == ':')) return null;
        return char.ToUpperInvariant(text[0]) + ":";
    }

    private static string FormFactor(object? value) => Long(value) switch
    {
        8 => "dimm",
        12 => "sodimm",
        _ => "unknown"
    };

    private static string MemoryType(object? value) => Long(value) switch
    {
        20 => "ddr",
        21 => "ddr2",
        24 => "ddr3",
        26 => "ddr4",
        30 => "lpddr4",
        34 => "ddr5",
        35 => "lpddr5",
        _ => "unknown"
    };

    private static string BatteryStatus(object? value) => Long(value) switch
    {
        1 or 4 or 5 => "discharging",
        2 => "on_ac",
        3 => "fully_charged",
        6 or 7 or 8 or 9 => "charging",
        _ => "unknown"
    };

    private static string MediaType(object? value)
    {
        string media = Text(value)?.ToLowerInvariant() ?? "";
        return media switch
        {
            "ssd" or "solid state disk" => "ssd",
            "hdd" or "hard disk drive" => "hdd",
            "scm" or "storage class memory" => "scm",
            _ => "unknown"
        };
    }

    private static string BusType(object? value)
    {
        string bus = Text(value)?.ToLowerInvariant() ?? "";
        return bus switch
        {
            "nvme" => "nvme",
            "sata" => "sata",
            "sas" => "sas",
            "usb" => "usb",
            "raid" => "raid",
            "spaces" or "storage spaces" => "spaces",
            "virtual" or "file backed virtual" or "filebackedvirtual" => "virtual",
            "ata" or "ide" => "ata",
            "scsi" => "scsi",
            "iscsi" => "iscsi",
            "fibre channel" or "fibrechannel" => "fibre_channel",
            "sd" => "sd",
            "mmc" => "mmc",
            _ => "unknown"
        };
    }

    private static string HealthStatus(object? value) => Text(value)?.ToLowerInvariant() switch
    {
        "healthy" => "healthy", "warning" => "warning", "unhealthy" => "unhealthy", _ => "unknown"
    };

    private static string OperationalStatus(object? value) => Text(value)?.ToLowerInvariant() switch
    {
        "ok" => "ok", "online" => "online", "offline" => "offline", "no media" => "no_media",
        "lost communication" => "lost_communication", "degraded" => "degraded", "failed" => "failed",
        _ => "unknown"
    };
}
