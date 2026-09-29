using System.Globalization;

namespace NightOwl.Agent.Windows.Collectors;

internal static class HardwareInventoryNormalizer
{
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
        return new Dictionary<string, object?>
        {
            ["total_bytes"] = PositiveLong(reportedTotalBytes) ?? moduleTotal,
            ["slots_total"] = slotsTotal,
            ["slots_used"] = modules.Count,
            ["slots_free"] = slotsTotal is null ? null : Math.Max(slotsTotal.Value - modules.Count, 0),
            ["modules"] = modules
        };
    }

    internal static Dictionary<string, object?> Battery(Dictionary<string, object?> source)
    {
        bool present = source.GetValueOrDefault("present") is true;
        long? design = present ? PositiveLong(source.GetValueOrDefault("design_capacity_mwh")) : null;
        long? full = present ? PositiveLong(source.GetValueOrDefault("full_charge_capacity_mwh")) : null;
        return new Dictionary<string, object?>
        {
            ["present"] = present,
            ["status"] = present ? BatteryStatus(source.GetValueOrDefault("status_code")) : "not_present",
            ["estimated_charge_remaining"] = present ? Percent(source.GetValueOrDefault("estimated_charge_remaining")) : null,
            ["design_capacity_mwh"] = design,
            ["full_charge_capacity_mwh"] = full,
            ["health_percent"] = design is > 0 && full is > 0
                ? Math.Round(Math.Clamp((double)full.Value / design.Value * 100, 0, 100), 1)
                : null,
            ["cycle_count"] = present ? NonNegativeLong(source.GetValueOrDefault("cycle_count")) : null
        };
    }

    internal static List<Dictionary<string, object?>> PhysicalDisks(object? source, string systemDrive)
    {
        string? systemLetter = NormalizeLetter(systemDrive);
        List<Dictionary<string, object?>> disks = new();
        foreach (Dictionary<string, object?> raw in Rows(source))
        {
            List<string> letters = Values(raw.GetValueOrDefault("drive_letters"))
                .Select(NormalizeLetter)
                .Where(letter => letter is not null)
                .Select(letter => letter!)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(letter => letter, StringComparer.OrdinalIgnoreCase)
                .ToList();
            disks.Add(new Dictionary<string, object?>
            {
                ["disk_number"] = NonNegativeLong(raw.GetValueOrDefault("disk_number")),
                ["device_id"] = Text(raw.GetValueOrDefault("device_id")),
                ["model"] = Text(raw.GetValueOrDefault("model")),
                ["manufacturer"] = Text(raw.GetValueOrDefault("manufacturer")),
                ["serial_number"] = Text(raw.GetValueOrDefault("serial_number")),
                ["firmware_version"] = Text(raw.GetValueOrDefault("firmware_version")),
                ["size_bytes"] = PositiveLong(raw.GetValueOrDefault("size_bytes")),
                ["media_type"] = MediaType(raw.GetValueOrDefault("media_type")),
                ["bus_type"] = BusType(raw.GetValueOrDefault("bus_type")),
                ["health_status"] = Status(raw.GetValueOrDefault("health_status")),
                ["operational_status"] = Status(raw.GetValueOrDefault("operational_status")),
                ["is_system_disk"] = letters.Count == 0 || systemLetter is null
                    ? null : letters.Contains(systemLetter, StringComparer.OrdinalIgnoreCase),
                ["drive_letters"] = letters
            });
        }
        return disks;
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

    private static string Status(object? value) => Text(value)?.ToLowerInvariant().Replace(' ', '_') ?? "unknown";
}
