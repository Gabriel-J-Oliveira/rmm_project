using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace NightOwl.Agent.Windows.Collectors;

internal sealed class NativeDiskCounters : IDisposable
{
    private nint _query;
    private bool _primed;
    private bool _disposed;
    internal bool InitializationAttempted { get; private set; }
    private readonly Dictionary<string, nint> _handles = new();
    private void Initialize()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (InitializationAttempted) return;
        InitializationAttempted = true;
        if (!OperatingSystem.IsWindows() || PdhOpenQuery(null, 0, out _query) != 0) return;
        foreach ((string name, string path) in new Dictionary<string, string>
        {
            ["read_bytes_per_sec"] = "Disk Read Bytes/sec", ["write_bytes_per_sec"] = "Disk Write Bytes/sec",
            ["read_ops_per_sec"] = "Disk Reads/sec", ["write_ops_per_sec"] = "Disk Writes/sec",
            ["read_latency_ms"] = "Avg. Disk sec/Read", ["write_latency_ms"] = "Avg. Disk sec/Write",
            ["queue_length"] = "Avg. Disk Queue Length", ["idle_percent"] = "% Idle Time"
        })
            if (PdhAddEnglishCounter(_query, @"\PhysicalDisk(*)\" + path, 0, out nint handle) == 0) _handles[name] = handle;
        if (PdhAddEnglishCounter(_query, @"\Memory\Page Reads/sec", 0, out nint memory) == 0) _handles["paging"] = memory;
    }

    internal Dictionary<string, double> Read(Dictionary<string, object?> quality)
    {
        Initialize();
        Dictionary<string, double> values = new();
        if (_query == 0 || PdhCollectQueryData(_query) != 0)
        {
            quality["disk"] = "counter_unavailable";
            return values;
        }
        if (!_primed) { _primed = true; quality["disk"] = "first_delta_unknown"; return values; }
        Dictionary<string, object?> devices = new();
        foreach ((string metric, nint handle) in _handles)
        {
            if (metric == "paging")
            {
                if (PdhGetFormattedCounterValue(handle, 0x8200, out _, out Formatted value) == 0 && value.Status <= 1 && double.IsFinite(value.Value) && value.Value >= 0)
                    values["memory.page_reads_per_sec"] = value.Value;
                continue;
            }
            uint bytes = 0, count = 0;
            _ = PdhGetFormattedCounterArray(handle, 0x8200, ref bytes, out count, 0);
            if (bytes == 0 || bytes > 1024 * 1024) continue;
            nint buffer = Marshal.AllocHGlobal((int)bytes);
            try
            {
                if (PdhGetFormattedCounterArray(handle, 0x8200, ref bytes, out count, buffer) != 0) continue;
                for (int index = 0; index < Math.Min(count, 17); index++)
                {
                    Item item = Marshal.PtrToStructure<Item>(buffer + index * Marshal.SizeOf<Item>());
                    string instance = Marshal.PtrToStringUni(item.Name) ?? "";
                    if (instance == "_Total" || item.Value.Status > 1 || !double.IsFinite(item.Value.Value) || item.Value.Value < 0) continue;
                    string id = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(instance)))[..12].ToLowerInvariant();
                    string name = metric;
                    double value = item.Value.Value;
                    if (name == "idle_percent")
                    {
                        if (value > 100) continue;
                        name = "active_percent"; value = 100 - value;
                    }
                    else if (name.EndsWith("_latency_ms", StringComparison.Ordinal)) value *= 1000;
                    values["disk." + id + "." + name] = value;
                    devices[id] = new { instance, layer = "windows_disk_instance", association_confidence = "unverified" };
                }
            }
            finally { Marshal.FreeHGlobal(buffer); }
        }
        quality["disk"] = devices.Count > 0 ? "available" : "counter_unavailable";
        quality["disk_instances"] = devices;
        return values;
    }

    public void Dispose() { _disposed = true; if (_query != 0) { PdhCloseQuery(_query); _query = 0; } }
    [StructLayout(LayoutKind.Sequential)] private struct Formatted { public uint Status; public double Value; }
    [StructLayout(LayoutKind.Sequential)] private struct Item { public nint Name; public Formatted Value; }
    [DllImport("pdh.dll", CharSet = CharSet.Unicode, EntryPoint = "PdhOpenQueryW")] private static extern uint PdhOpenQuery(string? source, nint data, out nint query);
    [DllImport("pdh.dll", CharSet = CharSet.Unicode, EntryPoint = "PdhAddEnglishCounterW")] private static extern uint PdhAddEnglishCounter(nint query, string path, nint data, out nint counter);
    [DllImport("pdh.dll")] private static extern uint PdhCollectQueryData(nint query);
    [DllImport("pdh.dll")] private static extern uint PdhGetFormattedCounterValue(nint counter, uint format, out uint type, out Formatted value);
    [DllImport("pdh.dll", EntryPoint = "PdhGetFormattedCounterArrayW")] private static extern uint PdhGetFormattedCounterArray(nint counter, uint format, ref uint bytes, out uint count, nint items);
    [DllImport("pdh.dll")] private static extern uint PdhCloseQuery(nint query);
}
