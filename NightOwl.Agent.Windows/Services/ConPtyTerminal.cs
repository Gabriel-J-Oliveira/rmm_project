using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Channels;
using Microsoft.Win32.SafeHandles;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed record ConPtyObservation(double CreateMs, double ProcessStartMs, double FirstOutputMs,
    long OutputBytes, double CpuMs, long WorkingSetBytes, long PeakCommitBytes, double CloseMs);

/// <summary>One owned root per endpoint. ConPTY is a PTY, not a security sandbox.</summary>
public sealed class ConPtyTerminal : IAsyncDisposable
{
    private static readonly SemaphoreSlim Admission = new(1, 1);
    public const long MaxOutputBytes = 16 * 1024 * 1024;
    public const long MaxInputBytes = 1024 * 1024;
    private readonly Channel<byte[]> _output = Channel.CreateBounded<byte[]>(new BoundedChannelOptions(16)
        { SingleReader = true, SingleWriter = true, FullMode = BoundedChannelFullMode.Wait });
    private readonly CancellationTokenSource _stop = new();
    private readonly SemaphoreSlim _inputGate = new(1, 1);
    private readonly object _gate = new();
    private readonly Stopwatch _elapsed = Stopwatch.StartNew();
    private TerminalIdentity? _identity;
    private FileStream? _input, _read;
    private nint _console, _job, _process, _thread;
    private Task _reader = Task.CompletedTask, _exit = Task.CompletedTask, _processWait = Task.CompletedTask, _consentMonitor = Task.CompletedTask;
    private Task? _close;
    private CancellationTokenRegistration _external;
    private bool _admitted;
    private long _bytes;
    private long _outputLimit = MaxOutputBytes;
    private long _inputLimit = MaxInputBytes, _inputBytes;
    private double _createMs, _startMs, _firstMs = -1, _closeMs;
    private long _workingSet, _peakCommit;
    private double _cpuMs;
    public uint ProcessId { get; private set; }
    public TerminalTokenFacts Identity => _identity!.Facts;
    public ChannelReader<byte[]> Output => _output.Reader;
    public Task Completion => _exit;
    public ConPtyObservation Observe()
    {
        lock (_gate)
        {
            if (_process != 0)
            {
                if (GetProcessTimes(_process, out _, out _, out long kernel, out long user)) _cpuMs = (kernel + user) / 10000d;
                if (GetProcessMemoryInfo(_process, out var memory, (uint)Marshal.SizeOf<MemoryCounters>())) _workingSet = (long)memory.WorkingSet;
                if (_job != 0 && QueryInformationJobObject(_job, 9, out ExtendedLimits limits, (uint)Marshal.SizeOf<ExtendedLimits>(), 0))
                    _peakCommit = (long)limits.PeakJobMemory;
                if (_job != 0 && QueryInformationJobObject(_job, 1, out Accounting accounting, (uint)Marshal.SizeOf<Accounting>(), 0))
                    _cpuMs = (accounting.UserTime + accounting.KernelTime) / 10000d;
            }
            return new(_createMs, _startMs, _firstMs, Interlocked.Read(ref _bytes), _cpuMs, _workingSet, _peakCommit, _closeMs);
        }
    }

    public static async Task<ConPtyTerminal> StartAsync(AgentConfig config, TerminalAuthorization authorization,
        int cols, int rows, CancellationToken ct, long maxOutputBytes = MaxOutputBytes, long maxInputBytes = MaxInputBytes)
    {
        if (maxOutputBytes is < 1024 or > MaxOutputBytes) throw new InvalidDataException("terminal_output_limit_invalid");
        if (maxInputBytes is < 1024 or > MaxInputBytes) throw new InvalidDataException("terminal_input_limit_invalid");
        if (!await Admission.WaitAsync(0, ct)) throw new InvalidOperationException("terminal_session_limit");
        var terminal = new ConPtyTerminal { _admitted = true, _outputLimit = maxOutputBytes, _inputLimit = maxInputBytes };
        try
        {
            terminal._identity = TerminalIdentity.Acquire(config, authorization);
            ValidateSize(cols, rows);
            terminal.StartNative(cols, rows);
            terminal._external = ct.Register(() => terminal.RequestStop());
            TimeSpan remaining = authorization.ExpiresAt - DateTimeOffset.UtcNow;
            terminal._stop.CancelAfter(remaining > TimeSpan.Zero ? remaining : TimeSpan.Zero);
            terminal._exit = Task.Run(async () =>
            {
                // Native process wait, independent of heartbeat/jobs and output draining.
                await terminal._processWait;
                await terminal.DisposeAsync();
            });
            // Cancellation and timeout initiate the same owned cleanup path.
            terminal._stop.Token.Register(() => _ = terminal.DisposeAsync().AsTask());
            terminal._consentMonitor = Task.Run(async () =>
            {
                try
                {
                    while (!terminal._stop.IsCancellationRequested)
                    {
                        await Task.Delay(1000, terminal._stop.Token);
                        if (!terminal._identity.ConsentStillValid()) terminal.RequestStop();
                    }
                }
                catch (OperationCanceledException) { }
            });
            ct.ThrowIfCancellationRequested();
            return terminal;
        }
        catch { await terminal.DisposeAsync(); throw; }
    }

    private static void ValidateSize(int cols, int rows)
    { if (cols is < 1 or > 500 || rows is < 1 or > 500) throw new InvalidDataException("terminal_resize_invalid"); }

    public void Resize(int cols, int rows)
    {
        ValidateSize(cols, rows);
        lock (_gate)
        {
            if (_close is not null || _console == 0) throw new InvalidOperationException("terminal_closed");
            if (ResizePseudoConsole(_console, new((short)cols, (short)rows)) < 0) throw new InvalidOperationException("terminal_resize_failed");
        }
    }

    public async Task WriteAsync(ReadOnlyMemory<byte> data, CancellationToken ct)
    {
        if (data.Length is < 1 or > RemoteTerminalTransport.MaxDataBytes) throw new InvalidDataException("terminal_input_limit");
        if (Interlocked.Add(ref _inputBytes, data.Length) > _inputLimit)
        { RequestStop(); throw new InvalidDataException("terminal_input_limit"); }
        using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct, _stop.Token);
        budget.CancelAfter(TimeSpan.FromSeconds(2));
        using var abortOnDeadline = budget.Token.Register(RequestStop);
        await _inputGate.WaitAsync(budget.Token);
        try { await _input!.WriteAsync(data, budget.Token); await _input.FlushAsync(budget.Token); }
        catch { RequestStop(); throw; }
        finally { _inputGate.Release(); }
    }

    private async Task DrainAsync()
    {
        byte[] buffer = new byte[RemoteTerminalTransport.MaxDataBytes];
        try
        {
            int size;
            // Keep draining even during ClosePseudoConsole: synchronous close can
            // emit a final screen and must never compete with a stopped reader.
            while ((size = await _read!.ReadAsync(buffer)) > 0)
            {
                if (_firstMs < 0) _firstMs = _elapsed.Elapsed.TotalMilliseconds;
                if (Interlocked.Add(ref _bytes, size) > _outputLimit) RequestStop();
                if (_stop.IsCancellationRequested) continue;
                try
                {
                    using var pressure = CancellationTokenSource.CreateLinkedTokenSource(_stop.Token);
                    pressure.CancelAfter(TimeSpan.FromSeconds(2));
                    await _output.Writer.WriteAsync(buffer.AsSpan(0, size).ToArray(), pressure.Token);
                }
                catch (OperationCanceledException) { RequestStop(); }
            }
        }
        catch (IOException) { RequestStop(); }
        finally { _output.Writer.TryComplete(); }
    }

    private void RequestStop() { try { _stop.Cancel(); } catch (ObjectDisposedException) { } }

    public ValueTask DisposeAsync()
    {
        lock (_gate) return new(_close ??= Task.Run(CloseAsync));
    }

    private async Task CloseAsync()
    {
        Stopwatch watch = Stopwatch.StartNew();
        _stop.Cancel();
        Observe();
        if (_job != 0) TerminateJobObject(_job, 1);
        if (_process != 0)
        {
            // Job-list assignment is atomic at creation; root and normal descendants
            // cannot run in the gap before ownership is established.
            await _processWait;
        }
        if (_job != 0)
        {
            while (true)
            {
                if (!QueryInformationJobObject(_job, 1, out Accounting accounting, (uint)Marshal.SizeOf<Accounting>(), 0))
                    throw new InvalidOperationException("terminal_cleanup_unconfirmed");
                if (accounting.ActiveProcesses == 0) break;
                await Task.Delay(10);
            }
        }
        nint console;
        lock (_gate) { console = _console; _console = 0; }
        if (console != 0) await Task.Run(() => ClosePseudoConsole(console));
        _input?.Dispose(); // Closing HPCON first breaks a blocked input write before stream disposal/flush.
        await _reader;
        await _consentMonitor;
        _read?.Dispose();
        _output.Writer.TryComplete();
        lock (_gate)
        {
            if (_thread != 0) { CloseHandle(_thread); _thread = 0; }
            if (_process != 0) { CloseHandle(_process); _process = 0; }
            if (_job != 0) { CloseHandle(_job); _job = 0; }
        }
        _external.Unregister();
        _identity?.Dispose();
        _closeMs = watch.Elapsed.TotalMilliseconds;
        if (_admitted) { _admitted = false; Admission.Release(); }
    }

    private void StartNative(int cols, int rows)
    {
        nint inputRead = 0, inputWrite = 0, outputRead = 0, outputWrite = 0, attributes = 0, jobArray = 0, environment = 0, desktop = 0;
        bool listInitialized = false;
        try
        {
            _job = CreateJobObject(0, null);
            if (_job == 0) throw new InvalidOperationException("terminal_job_failed");
            ExtendedLimits limits = new() { Basic = new() { LimitFlags = 0x2000 | 0x8 | 0x100 | 0x200 | 0x400,
                ActiveLimit = 8 }, ProcessMemory = 256 * 1024 * 1024, JobMemory = 512 * 1024 * 1024 };
            if (!SetInformationJobObject(_job, 9, ref limits, (uint)Marshal.SizeOf<ExtendedLimits>())) throw new InvalidOperationException("terminal_job_failed");
            CpuRate cpu = new() { Flags = 0x1 | 0x4, Rate = 2000 };
            if (!SetInformationJobObject(_job, 15, ref cpu, (uint)Marshal.SizeOf<CpuRate>())) throw new InvalidOperationException("terminal_cpu_limit_failed");
            if (!CreatePipe(out inputRead, out inputWrite, 0, 4096) || !CreatePipe(out outputRead, out outputWrite, 0, 4096))
                throw new InvalidOperationException("terminal_pipe_failed");
            var watch = Stopwatch.StartNew();
            if (CreatePseudoConsole(new((short)cols, (short)rows), inputRead, outputWrite, 0, out _console) < 0)
                throw new InvalidOperationException("terminal_conpty_unavailable");
            _createMs = watch.Elapsed.TotalMilliseconds;
            _input = new FileStream(new SafeFileHandle(inputWrite, true), FileAccess.Write, 1, false); inputWrite = 0;
            _read = new FileStream(new SafeFileHandle(outputRead, true), FileAccess.Read, 4096, false); outputRead = 0;
            _reader = Task.Run(DrainAsync);
            nuint bytes = 0;
            InitializeProcThreadAttributeList(0, 2, 0, ref bytes);
            attributes = Marshal.AllocHGlobal(checked((int)bytes));
            if (!InitializeProcThreadAttributeList(attributes, 2, 0, ref bytes)) throw new InvalidOperationException("terminal_attributes_failed");
            listInitialized = true;
            jobArray = Marshal.AllocHGlobal(nint.Size); Marshal.WriteIntPtr(jobArray, _job);
            if (!UpdateProcThreadAttribute(attributes, 0, 0x20016, _console, (nuint)nint.Size, 0, 0) ||
                !UpdateProcThreadAttribute(attributes, 0, 0x2000D, jobArray, (nuint)nint.Size, 0, 0))
                throw new InvalidOperationException("terminal_attributes_failed");
            // Explicit null standard handles prevent inheritance of the service's
            // redirected stdio; console initialization binds them to this ConPTY.
            StartupEx startup = new() { Startup = new() { Size = Marshal.SizeOf<StartupEx>(), Flags = 0x100 }, Attributes = attributes };
            if (!_identity!.IsCaller)
            {
                // Existing user desktop ACLs only; never grant station access or
                // substitute a SYSTEM desktop if the target cannot access it.
                desktop = Marshal.StringToHGlobalUni("winsta0\\default");
                startup.Startup.Desktop = desktop;
            }
            string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            string shell = Path.Combine(windows, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
            string profile = _identity!.Profile;
            // Closed environment: do not inherit service tokens, DB secrets, PATH or PS profiles.
            SortedDictionary<string, string> variables = new(StringComparer.OrdinalIgnoreCase)
            {
                ["SystemRoot"] = windows, ["WINDIR"] = windows,
                ["PATH"] = Path.Combine(windows, "System32") + ";" + windows + ";" + Path.GetDirectoryName(shell),
                ["PSModulePath"] = Path.Combine(windows, "System32", "WindowsPowerShell", "v1.0", "Modules"),
                ["USERPROFILE"] = profile, ["APPDATA"] = Path.Combine(profile, "AppData", "Roaming"),
                ["LOCALAPPDATA"] = Path.Combine(profile, "AppData", "Local"),
                ["TEMP"] = Path.Combine(profile, "AppData", "Local", "Temp"), ["TMP"] = Path.Combine(profile, "AppData", "Local", "Temp"),
                ["COMSPEC"] = Path.Combine(windows, "System32", "cmd.exe")
            };
            environment = Marshal.StringToHGlobalUni(string.Join('\0', variables.Select(p => p.Key + "=" + p.Value)) + "\0\0");
            StringBuilder command = new('"' + shell + "\" -NoLogo -NoProfile -NoExit -Command \"[Console]::InputEncoding=[Text.UTF8Encoding]::new($false);[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\"");
            watch.Restart();
            ProcessInfo process;
            const uint flags = 0x80000 | 0x400 | 0x4 | 0x4000; // Extended startup, Unicode environment, suspended, below-normal priority.
            bool created;
            if (_identity.IsCaller)
                created = CreateProcess(shell, command, 0, 0, false, flags, environment, profile, ref startup, out process);
            else
            {
                // Restricted target token, never the service's primary token.
                if (!CreateRestrictedToken(_identity.Token, 1, 0, 0, 0, 0, 0, 0, out var restricted))
                    throw new InvalidOperationException("terminal_restricted_identity_failed");
                using (restricted) created = CreateProcessAsUser(restricted, shell, command, 0, 0, false, flags, environment, profile, ref startup, out process);
            }
            if (!created) throw new InvalidOperationException("terminal_process_creation_failed");
            _process = process.Process; _thread = process.Thread; ProcessId = process.ProcessId;
            nint ownedProcess = _process;
            _processWait = Task.Run(() => WaitForSingleObject(ownedProcess, uint.MaxValue));
            if (!TerminalIdentity.OpenProcessToken(_process, 0x000A, out var childToken)) throw new InvalidOperationException("terminal_child_identity_failed");
            using (childToken)
            {
                var facts = TerminalIdentity.Inspect(childToken);
                if (facts.Sid != _identity.Facts.Sid || facts.SessionId != _identity.Facts.SessionId || facts.LogonId != _identity.Facts.LogonId ||
                    facts.Elevated || facts.Administrator || facts.Integrity > 8192 || facts.DangerousPrivileges || !facts.Interactive)
                    throw new InvalidOperationException("terminal_child_identity_failed");
            }
            if (ResumeThread(_thread) == uint.MaxValue) throw new InvalidOperationException("terminal_process_start_failed");
            _startMs = watch.Elapsed.TotalMilliseconds;
        }
        finally
        {
            if (listInitialized) DeleteProcThreadAttributeList(attributes);
            if (attributes != 0) Marshal.FreeHGlobal(attributes);
            if (jobArray != 0) Marshal.FreeHGlobal(jobArray);
            if (environment != 0) Marshal.FreeHGlobal(environment);
            if (desktop != 0) Marshal.FreeHGlobal(desktop);
            foreach (nint handle in new[] { inputRead, inputWrite, outputRead, outputWrite }) if (handle != 0) CloseHandle(handle);
        }
    }

    [StructLayout(LayoutKind.Sequential)] private readonly struct Coord(short x, short y) { public readonly short X = x, Y = y; }
    [StructLayout(LayoutKind.Sequential)] private struct Startup { public int Size; public nint Reserved, Desktop, Title; public uint X,Y,XSize,YSize,XChars,YChars,Fill,Flags; public ushort Show,ReservedBytes; public nint ReservedPointer,Input,Output,Error; }
    [StructLayout(LayoutKind.Sequential)] private struct StartupEx { public Startup Startup; public nint Attributes; }
    [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public nint Process, Thread; public uint ProcessId, ThreadId; }
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimits { public long ProcessTime,JobTime; public uint LimitFlags; public nuint MinWorking,MaxWorking; public uint ActiveLimit; public nuint Affinity; public uint Priority,Scheduling; }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong A,B,C,D,E,F; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits { public BasicLimits Basic; public IoCounters Io; public nuint ProcessMemory,JobMemory,PeakProcessMemory,PeakJobMemory; }
    [StructLayout(LayoutKind.Sequential)] private struct Accounting { public long UserTime,KernelTime,PeriodUserTime,PeriodKernelTime; public uint Faults,TotalProcesses,ActiveProcesses,TerminatedProcesses; }
    [StructLayout(LayoutKind.Sequential)] private struct CpuRate { public uint Flags, Rate; }
    [StructLayout(LayoutKind.Sequential)] private struct MemoryCounters { public uint Size,Faults; public nuint PeakWorkingSet,WorkingSet,PeakPaged,Paged,PeakNonPaged,NonPaged,Pagefile,PeakPagefile; }
    [DllImport("kernel32.dll")] private static extern int CreatePseudoConsole(Coord size, nint input, nint output, uint flags, out nint console);
    [DllImport("kernel32.dll")] private static extern int ResizePseudoConsole(nint console, Coord size);
    [DllImport("kernel32.dll")] private static extern void ClosePseudoConsole(nint console);
    [DllImport("kernel32.dll", SetLastError=true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreatePipe(out nint read, out nint write, nint security, uint size);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] private static extern nint CreateJobObject(nint attributes, string? name);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool SetInformationJobObject(nint job, int kind, ref ExtendedLimits limits, uint size);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool SetInformationJobObject(nint job, int kind, ref CpuRate cpu, uint size);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool QueryInformationJobObject(nint job, int kind, out ExtendedLimits limits, uint size, nint returned);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool QueryInformationJobObject(nint job, int kind, out Accounting accounting, uint size, nint returned);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool InitializeProcThreadAttributeList(nint list, int count, uint flags, ref nuint size);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool UpdateProcThreadAttribute(nint list, uint flags, nuint attribute, nint value, nuint size, nint previous, nint returned);
    [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(nint list);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, EntryPoint="CreateProcessW")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreateProcess(string app, StringBuilder command, nint pa, nint ta, bool inherit, uint flags, nint environment, string directory, ref StartupEx startup, out ProcessInfo process);
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, EntryPoint="CreateProcessAsUserW")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreateProcessAsUser(SafeAccessTokenHandle token, string app, StringBuilder command, nint pa, nint ta, bool inherit, uint flags, nint environment, string directory, ref StartupEx startup, out ProcessInfo process);
    [DllImport("advapi32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreateRestrictedToken(SafeAccessTokenHandle token, uint flags, uint disableCount, nint disable, uint deleteCount, nint delete, uint restrictCount, nint restrict, out SafeAccessTokenHandle restricted);
    [DllImport("kernel32.dll")] private static extern uint ResumeThread(nint thread);
    [DllImport("kernel32.dll")] private static extern uint WaitForSingleObject(nint handle, uint milliseconds);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool TerminateJobObject(nint job, uint code);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CloseHandle(nint handle);
    [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetProcessTimes(nint process, out long created, out long exited, out long kernel, out long user);
    [DllImport("psapi.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetProcessMemoryInfo(nint process, out MemoryCounters counters, uint size);
}
