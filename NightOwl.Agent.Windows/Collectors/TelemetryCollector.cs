using System.Diagnostics;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Collectors;

public sealed class TelemetryCollector
{
    private readonly TelemetryCollectorHooks? _hooks;
    private (ulong Idle, ulong Kernel, ulong User)? _lastCpu;
    private Dictionary<string, (long Received, long Sent)> _lastNetwork = new(StringComparer.OrdinalIgnoreCase);
    private DateTimeOffset? _lastProcessAt;
    private Dictionary<(int Pid, long Started), TimeSpan> _lastProcesses = new();

    public TelemetryCollector() { }

    internal TelemetryCollector(TelemetryCollectorHooks hooks) => _hooks = hooks;

    public TelemetrySample Collect(string machineId, CancellationToken ct)
    {
        Stopwatch clock = Stopwatch.StartNew();
        DateTimeOffset now = DateTimeOffset.UtcNow;
        TelemetrySample sample = new()
        {
            CollectedAt = now,
            UptimeSeconds = Math.Max(0, Environment.TickCount64 / 1000),
        };
        // Disk counters are intentionally unavailable in v1: logical-volume and physical-disk values cannot be mixed reliably.
        try { sample.Cpu.UsagePercent = (_hooks?.Cpu ?? ReadCpu)(); }
        catch (Exception ex) when (ex is not OperationCanceledException) { sample.TelemetryErrorsCount++; }
        ct.ThrowIfCancellationRequested();
        try { CopyMemory((_hooks?.Memory ?? ReadMemory)(), sample.Memory); }
        catch (Exception ex) when (ex is not OperationCanceledException) { sample.TelemetryErrorsCount++; }
        ct.ThrowIfCancellationRequested();
        try { CopyNetwork((_hooks?.Network ?? ReadNetwork)(), sample.Network); }
        catch (Exception ex) when (ex is not OperationCanceledException) { sample.TelemetryErrorsCount++; }
        ct.ThrowIfCancellationRequested();
        try { sample.TopProcesses = (_hooks?.Processes ?? ReadProcesses)(now, ct); }
        catch (Exception ex) when (ex is not OperationCanceledException) { sample.TelemetryErrorsCount++; }
        ct.ThrowIfCancellationRequested();
        try { sample.AgentWorkingSetBytes = Process.GetCurrentProcess().WorkingSet64; }
        catch (Exception ex) when (ex is not OperationCanceledException) { sample.TelemetryErrorsCount++; }
        sample.CollectionDurationMs = clock.ElapsedMilliseconds;
        return sample;
    }

    internal static double? CpuPercent(TimeSpan processorDelta, TimeSpan wallDelta, int logicalProcessors)
    {
        if (processorDelta < TimeSpan.Zero || wallDelta <= TimeSpan.Zero || logicalProcessors <= 0)
            return null;
        return Math.Clamp(100 * processorDelta.TotalSeconds / wallDelta.TotalSeconds / logicalProcessors, 0, 100);
    }

    private double? ReadCpu()
    {
        if (!OperatingSystem.IsWindows() || !GetSystemTimes(out FileTime idle, out FileTime kernel, out FileTime user))
            return null;
        (ulong Idle, ulong Kernel, ulong User) current = (idle.Value, kernel.Value, user.Value);
        var previous = _lastCpu;
        _lastCpu = current;
        if (previous is null || current.Idle < previous.Value.Idle || current.Kernel < previous.Value.Kernel || current.User < previous.Value.User)
            return null;
        ulong total = current.Kernel - previous.Value.Kernel + current.User - previous.Value.User;
        if (total == 0) return null;
        ulong busy = total - Math.Min(total, current.Idle - previous.Value.Idle);
        return Math.Clamp(100.0 * busy / total, 0, 100);
    }

    private static TelemetryMemory ReadMemory()
    {
        if (!OperatingSystem.IsWindows()) return new();
        MemoryStatus status = new() { Length = (uint)Marshal.SizeOf<MemoryStatus>() };
        if (!GlobalMemoryStatusEx(ref status)) return new();
        TelemetryMemory memory = new()
        {
            TotalBytes = status.TotalPhysical,
            AvailableBytes = status.AvailablePhysical,
            UsedPercent = status.TotalPhysical == 0 ? null : Math.Clamp(100.0 * (status.TotalPhysical - status.AvailablePhysical) / status.TotalPhysical, 0, 100),
        };
        PerformanceInformation perf = new() { Size = (uint)Marshal.SizeOf<PerformanceInformation>() };
        if (GetPerformanceInfo(out perf, (uint)Marshal.SizeOf<PerformanceInformation>()) && perf.PageSize > 0)
        {
            memory.CommittedBytes = (ulong)perf.CommitTotal * (ulong)perf.PageSize;
            memory.CommitLimitBytes = (ulong)perf.CommitLimit * (ulong)perf.PageSize;
            memory.CommittedPercent = memory.CommitLimitBytes == 0 ? null : Math.Clamp(100.0 * memory.CommittedBytes.Value / memory.CommitLimitBytes.Value, 0, 100);
        }
        return memory;
    }

    private TelemetryNetwork ReadNetwork()
    {
        Dictionary<string, (long Received, long Sent)> current = new(StringComparer.OrdinalIgnoreCase);
        foreach (NetworkInterface nic in NetworkInterface.GetAllNetworkInterfaces())
        {
            if (nic.OperationalStatus != OperationalStatus.Up || nic.NetworkInterfaceType == NetworkInterfaceType.Loopback)
                continue;
            try
            {
                IPv4InterfaceStatistics stats = nic.GetIPv4Statistics();
                current[nic.Id] = (stats.BytesReceived, stats.BytesSent);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                // One disappearing interface must not invalidate the others.
            }
        }
        return CalculateNetworkDelta(current);
    }

    internal TelemetryNetwork CalculateNetworkDelta(IReadOnlyDictionary<string, (long Received, long Sent)> current)
    {
        long received = 0;
        long sent = 0;
        bool hasReceived = false;
        bool hasSent = false;
        foreach (var (id, counters) in current)
        {
            if (!_lastNetwork.TryGetValue(id, out var previous)) continue;
            if (counters.Received >= previous.Received && previous.Received >= 0)
            {
                received = checked(received + counters.Received - previous.Received);
                hasReceived = true;
            }
            if (counters.Sent >= previous.Sent && previous.Sent >= 0)
            {
                sent = checked(sent + counters.Sent - previous.Sent);
                hasSent = true;
            }
        }
        _lastNetwork = current.ToDictionary(item => item.Key, item => item.Value, StringComparer.OrdinalIgnoreCase);
        return new TelemetryNetwork
        {
            ReceivedBytes = hasReceived ? received : null,
            SentBytes = hasSent ? sent : null,
        };
    }

    private TelemetryProcesses ReadProcesses(DateTimeOffset now, CancellationToken ct)
    {
        Dictionary<(int Pid, long Started), TimeSpan> current = new();
        List<TelemetryProcess> processes = new();
        TimeSpan wall = _lastProcessAt is null ? TimeSpan.Zero : now - _lastProcessAt.Value;
        foreach (Process process in Process.GetProcesses())
        {
            using (process)
            {
                ct.ThrowIfCancellationRequested();
                try
                {
                    int pid = process.Id;
                    string name = process.ProcessName;
                    long workingSet = process.WorkingSet64;
                    processes.Add(SampleProcess(pid, name, workingSet, () => process.StartTime,
                        () => process.TotalProcessorTime, current, wall));
                }
                catch (Exception ex) when (ex is not OperationCanceledException)
                {
                    // Processes may exit or deny access while sampled.
                }
            }
        }
        _lastProcesses = current;
        _lastProcessAt = now;
        return SelectTopProcesses(processes);
    }

    internal TelemetryProcess SampleProcess(int pid, string name, long workingSet,
        Func<DateTime> startTime, Func<TimeSpan> processorTime,
        Dictionary<(int Pid, long Started), TimeSpan> current, TimeSpan wall)
    {
        double? cpu = null;
        try
        {
            (int Pid, long Started) key = (pid, startTime().ToUniversalTime().Ticks);
            TimeSpan total = processorTime();
            current[key] = total;
            if (_lastProcesses.TryGetValue(key, out TimeSpan old))
                cpu = CpuPercent(total - old, wall, Environment.ProcessorCount);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            // CPU identity/access can fail while working set remains readable.
        }
        return new TelemetryProcess
        {
            ProcessName = name[..Math.Min(name.Length, 128)],
            Pid = pid,
            CpuPercent = cpu,
            WorkingSetBytes = Math.Max(0, workingSet),
        };
    }

    internal static TelemetryProcesses SelectTopProcesses(IEnumerable<TelemetryProcess> processes)
    {
        List<TelemetryProcess> items = processes.ToList();
        return new TelemetryProcesses
        {
            Cpu = items.Where(item => item.CpuPercent.HasValue).OrderByDescending(item => item.CpuPercent).Take(5).ToList(),
            Memory = items.OrderByDescending(item => item.WorkingSetBytes).Take(5).ToList(),
        };
    }

    private static void CopyMemory(TelemetryMemory source, TelemetryMemory target)
    {
        target.TotalBytes = source.TotalBytes;
        target.AvailableBytes = source.AvailableBytes;
        target.UsedPercent = source.UsedPercent;
        target.CommittedBytes = source.CommittedBytes;
        target.CommitLimitBytes = source.CommitLimitBytes;
        target.CommittedPercent = source.CommittedPercent;
    }

    private static void CopyNetwork(TelemetryNetwork source, TelemetryNetwork target)
    {
        target.ReceivedBytes = source.ReceivedBytes;
        target.SentBytes = source.SentBytes;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileTime
    {
        public uint Low;
        public uint High;
        public ulong Value => ((ulong)High << 32) | Low;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryStatus
    {
        public uint Length;
        public uint MemoryLoad;
        public ulong TotalPhysical;
        public ulong AvailablePhysical;
        public ulong TotalPageFile;
        public ulong AvailablePageFile;
        public ulong TotalVirtual;
        public ulong AvailableVirtual;
        public ulong AvailableExtendedVirtual;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PerformanceInformation
    {
        public uint Size;
        public nint CommitTotal;
        public nint CommitLimit;
        public nint CommitPeak;
        public nint PhysicalTotal;
        public nint PhysicalAvailable;
        public nint SystemCache;
        public nint KernelTotal;
        public nint KernelPaged;
        public nint KernelNonpaged;
        public nint PageSize;
        public uint HandleCount;
        public uint ProcessCount;
        public uint ThreadCount;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetSystemTimes(out FileTime idle, out FileTime kernel, out FileTime user);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);

    [DllImport("psapi.dll", SetLastError = true)]
    private static extern bool GetPerformanceInfo(out PerformanceInformation info, uint size);
}

internal sealed class TelemetryCollectorHooks
{
    public Func<double?>? Cpu { get; init; }
    public Func<TelemetryMemory>? Memory { get; init; }
    public Func<TelemetryNetwork>? Network { get; init; }
    public Func<DateTimeOffset, CancellationToken, TelemetryProcesses>? Processes { get; init; }
}
