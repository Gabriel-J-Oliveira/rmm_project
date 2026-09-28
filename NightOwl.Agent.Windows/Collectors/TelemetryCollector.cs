using System.Diagnostics;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Collectors;

public sealed class TelemetryCollector
{
    private readonly TelemetryCollectorHooks? _hooks;
    private (ulong Idle, ulong Kernel, ulong User)? _lastCpu;
    private (long Received, long Sent)? _lastNetwork;
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
        long received = 0;
        long sent = 0;
        foreach (NetworkInterface nic in NetworkInterface.GetAllNetworkInterfaces())
        {
            if (nic.OperationalStatus != OperationalStatus.Up || nic.NetworkInterfaceType == NetworkInterfaceType.Loopback)
                continue;
            IPv4InterfaceStatistics stats = nic.GetIPv4Statistics();
            received = checked(received + stats.BytesReceived);
            sent = checked(sent + stats.BytesSent);
        }
        var previous = _lastNetwork;
        _lastNetwork = (received, sent);
        return new TelemetryNetwork
        {
            ReceivedBytes = previous is not null && received >= previous.Value.Received ? received - previous.Value.Received : null,
            SentBytes = previous is not null && sent >= previous.Value.Sent ? sent - previous.Value.Sent : null,
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
                    (int Pid, long Started) key = (process.Id, process.StartTime.ToUniversalTime().Ticks);
                    TimeSpan cpuTime = process.TotalProcessorTime;
                    current[key] = cpuTime;
                    double? cpu = _lastProcesses.TryGetValue(key, out TimeSpan old)
                        ? CpuPercent(cpuTime - old, wall, Environment.ProcessorCount) : null;
                    processes.Add(new TelemetryProcess
                    {
                        ProcessName = process.ProcessName[..Math.Min(process.ProcessName.Length, 128)],
                        Pid = process.Id,
                        CpuPercent = cpu,
                        WorkingSetBytes = Math.Max(0, process.WorkingSet64),
                    });
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
