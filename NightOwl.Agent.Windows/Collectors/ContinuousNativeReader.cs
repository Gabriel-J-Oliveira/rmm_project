using System.Diagnostics;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Collectors;

internal sealed class ContinuousNativeReader : IDisposable
{
    private readonly TelemetryCollector _collector;
    private readonly NativeDiskCounters _disks = new();
    private readonly Dictionary<string, (long Rx, long Tx)> _network = new();
    private long? _lastTick;
    private DateTimeOffset? _lastProcesses;
    private TimeSpan? _lastAgentCpu;
    internal ContinuousNativeReader(TelemetryCollector collector) => _collector = collector;

    internal NativeReading Read(string machineId, CancellationToken token)
    {
        long tick = Stopwatch.GetTimestamp();
        double interval = _lastTick is long last ? Stopwatch.GetElapsedTime(last, tick).TotalSeconds : 0;
        _lastTick = tick;
        bool validInterval = interval is >= 1 and <= 30;
        NativeReading reading = new() { IntervalSeconds = validInterval ? interval : 0 };
        reading.Quality["interval_seconds"] = interval;
        reading.Quality["interval"] = validInterval ? "available" : "first_or_resume_delta_unknown";
        Stopwatch group = Stopwatch.StartNew();
        bool processesDue = _lastProcesses is null || reading.CollectedAt - _lastProcesses >= TimeSpan.FromMinutes(5);
        TelemetrySample sample = _collector.Collect(machineId, token, processesDue, includeNetwork:false);
        if (processesDue)
        {
            _lastProcesses = reading.CollectedAt;
            reading.Quality["processes_collected_at"] = reading.CollectedAt;
            reading.Quality["top_processes"] = sample.TopProcesses;
        }
        reading.Quality["base_duration_ms"] = group.ElapsedMilliseconds;
        void Add(string key, double? value) { if (value is double number && double.IsFinite(number) && number >= 0) reading.Metrics[key] = number; }
        bool cpuSupported = !OperatingSystem.IsWindows() || GetActiveProcessorGroupCount() <= 1;
        Add("cpu.usage_percent", validInterval && cpuSupported ? sample.Cpu.UsagePercent : null);
        reading.Quality["cpu"] = !cpuSupported ? "processor_groups_unsupported" : reading.Metrics.ContainsKey("cpu.usage_percent") ? "available" : "delta_unavailable";
        Add("memory.total_bytes", sample.Memory.TotalBytes);
        Add("memory.available_bytes", sample.Memory.AvailableBytes);
        Add("memory.used_bytes", sample.Memory.TotalBytes is ulong total && sample.Memory.AvailableBytes is ulong available && total >= available ? total - available : null);
        Add("memory.used_percent", sample.Memory.UsedPercent);
        Add("memory.available_percent", sample.Memory.UsedPercent is double used ? 100 - used : null);
        Add("memory.committed_bytes", sample.Memory.CommittedBytes);
        Add("memory.commit_limit_bytes", sample.Memory.CommitLimitBytes);
        Add("memory.committed_percent", sample.Memory.CommittedPercent);
        reading.Quality["memory"] = sample.Memory.TotalBytes is > 0 && sample.Memory.AvailableBytes is not null ? "available" : "native_read_unavailable";
        reading.Quality["commit"] = sample.Memory.CommittedPercent is not null ? "available" : "native_read_unavailable";
        group.Restart();
        try
        {
            foreach ((string key, double value) in _disks.Read(reading.Quality))
                if (validInterval) reading.Metrics[key] = value;
            foreach (string key in reading.Metrics.Keys.Where(key => key.EndsWith("_latency_ms", StringComparison.Ordinal)).ToArray())
            {
                string prefix = key[..key.LastIndexOf('.')];
                string operations = prefix + (key.EndsWith("read_latency_ms", StringComparison.Ordinal) ? ".read_ops_per_sec" : ".write_ops_per_sec");
                if (reading.Metrics.TryGetValue(operations, out double rate) && rate > 0)
                    reading.Weights[key] = rate * interval;
                else reading.Metrics.Remove(key);
            }
        }
        catch (Exception error) when (error is not OperationCanceledException) { reading.Quality["disk"] = "native_counter_failed"; }
        reading.Quality["disk_duration_ms"] = group.ElapsedMilliseconds;
        group.Restart();
        HashSet<string> seen = new();
        Dictionary<string, object?> interfaces = new();
        foreach (NetworkInterface nic in NetworkInterface.GetAllNetworkInterfaces().Where(nic => nic.OperationalStatus == OperationalStatus.Up && nic.NetworkInterfaceType != NetworkInterfaceType.Loopback).Take(16))
        {
            try
            {
                IPInterfaceStatistics stats = nic.GetIPStatistics();
                string id = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(nic.Id)))[..12].ToLowerInvariant();
                seen.Add(id);
                string prefix = "network." + id + ".";
                bool virtualLink = nic.NetworkInterfaceType is NetworkInterfaceType.Tunnel or NetworkInterfaceType.Ppp || nic.Description.Contains("virtual", StringComparison.OrdinalIgnoreCase) || nic.Description.Contains("vpn", StringComparison.OrdinalIgnoreCase);
                interfaces[id] = new { name = nic.Name, type = nic.NetworkInterfaceType.ToString(), virtual_or_tunnel = virtualLink,
                    interpretation = "OS_nominal_interface_not_internet_capacity; virtual_detection_is_a_hint",
                    nominal_speed_bps = nic.Speed > 0 ? nic.Speed : (long?)null };
                if (_network.TryGetValue(id, out var previous) && validInterval)
                {
                    (double? rx, double? tx) = Rates(previous.Rx, previous.Tx, stats.BytesReceived, stats.BytesSent, interval);
                    Add(prefix + "received_bytes_per_sec", rx); Add(prefix + "sent_bytes_per_sec", tx);
                    if (nic.Speed > 0 && !virtualLink)
                    {
                        double? rxUse = rx * 800 / nic.Speed, txUse = tx * 800 / nic.Speed;
                        if (rxUse <= 100) Add(prefix + "rx_utilization_percent", rxUse);
                        if (txUse <= 100) Add(prefix + "tx_utilization_percent", txUse);
                    }
                }
                _network[id] = (stats.BytesReceived, stats.BytesSent);
            }
            catch (Exception error) when (error is not OperationCanceledException) { reading.Quality["network"] = "partial_interface_failure"; }
        }
        foreach (string id in _network.Keys.Where(id => !seen.Contains(id)).ToArray()) _network.Remove(id);
        reading.Quality["interfaces"] = interfaces;
        reading.Quality.TryAdd("network", interfaces.Count > 0 ? "available_or_first_delta" : "no_active_interface");
        reading.Quality["network_duration_ms"] = group.ElapsedMilliseconds;
        using Process process = Process.GetCurrentProcess();
        TimeSpan agentCpu = process.TotalProcessorTime;
        Add("agent.cpu_percent", validInterval && _lastAgentCpu is TimeSpan old ? TelemetryCollector.CpuPercent(agentCpu - old, TimeSpan.FromSeconds(interval), Environment.ProcessorCount) : null);
        _lastAgentCpu = agentCpu;
        Add("agent.working_set_bytes", sample.AgentWorkingSetBytes);
        Add("agent.collection_duration_ms", Stopwatch.GetElapsedTime(tick).TotalMilliseconds);
        Add("agent.uptime_seconds", sample.UptimeSeconds);
        Add("agent.telemetry_errors_count", sample.TelemetryErrorsCount);
        Add("agent.inventory_powershell_started_total", WindowsInventoryCollector.PowerShellStarted);
        return reading;
    }

    internal static (double? Rx, double? Tx) Rates(long oldRx, long oldTx, long rx, long tx, double seconds) =>
        seconds is > 0 and <= 30
            ? (oldRx >= 0 && rx >= oldRx ? (rx - oldRx) / seconds : null, oldTx >= 0 && tx >= oldTx ? (tx - oldTx) / seconds : null)
            : (null, null);

    public void Dispose() => _disks.Dispose();
    [DllImport("kernel32.dll")] private static extern ushort GetActiveProcessorGroupCount();
}
