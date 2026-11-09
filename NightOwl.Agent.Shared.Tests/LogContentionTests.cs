using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using NightOwl.Agent.Shared;

internal static class LogContentionTests
{
    private static readonly LogRetentionPolicy Policy = new(4096, 20, 512, 30);

    public static void Baseline(string root)
    {
        string path = Path.Combine(root, "baseline.jsonl");
        using ManualResetEventSlim held = new(false);
        Task holder = Task.Factory.StartNew(() => RotatingJsonlLog.AppendWithWriterForTest(path, "{}", new(),
            (stream, bytes) => { held.Set(); Thread.Sleep(700); stream.Write(bytes); }), TaskCreationOptions.LongRunning);
        Require(held.Wait(TimeSpan.FromSeconds(5)), "holder not ready");
        Stopwatch wait = Stopwatch.StartNew();
        try { RotatingJsonlLog.Append(path, "{}"); Console.WriteLine($"CONTROL outcome=acquired elapsed_ms={wait.ElapsedMilliseconds}"); }
        catch (IOException ex) { Console.WriteLine($"CONTROL outcome=io_failure elapsed_ms={wait.ElapsedMilliseconds} win32_code={ex.HResult & 0xffff}"); }
        holder.GetAwaiter().GetResult();
    }

    public static void Child(string path, string worker)
    {
        List<double> waits = new(), holds = new(), rotations = new();
        if (worker == "process-2" && Environment.GetEnvironmentVariable("NIGHTOWL_TEST_SLOW_CHILD") == "1") Thread.Sleep(4400);
        string dir = Path.GetDirectoryName(path)!;
        File.WriteAllText(Path.Combine(dir, "ready-" + worker), "ready");
        Require(SpinWait.SpinUntil(() => File.Exists(Path.Combine(dir, "start")), TimeSpan.FromSeconds(30)), "child start signal timed out");
        try { WriteMany(path, worker, waits, holds, rotations); }
        finally { Report(worker, waits, holds, rotations); }
    }

    private static void WriteMany(string path, string worker, List<double> waits, List<double> holds, List<double> rotations)
    {
        for (int i = 0; i < 60; i++)
        {
            Stopwatch clock = Stopwatch.StartNew(); double start = 0, acquired = 0, rotation = 0;
            string json = JsonSerializer.Serialize(new { worker, i, payload = new string('x', 36) });
            try
            {
                RotatingJsonlLog.AppendObservedForTest(path, json, Policy, phase =>
                {
                double now = clock.Elapsed.TotalMilliseconds;
                if (phase == "wait_start") start = now;
                else if (phase == "acquired") { acquired = now; waits.Add(now - start); }
                else if (phase == "rotation_start") rotation = now;
                else if (phase == "rotation_end") rotations.Add(now - rotation);
                    else if (phase == "write_complete") holds.Add(now - acquired);
                });
            }
            catch (IOException ex)
            {
                Console.WriteLine("LOCK_FAILURE " + JsonSerializer.Serialize(new { worker, process_id = Environment.ProcessId,
                    thread_id = Environment.CurrentManagedThreadId, iteration = i, elapsed_ms = Math.Round(clock.Elapsed.TotalMilliseconds, 2),
                    acquisition_started = start > 0, acquired = acquired > 0,
                    error_code = ex.HResult & 0xffff, inner_error_code = ex.InnerException?.HResult & 0xffff }));
                throw;
            }
        }
    }

    public static void Run(string root)
    {
        string dir = Path.Combine(root, "contention"); Directory.CreateDirectory(dir);
        string path = Path.Combine(dir, "mixed.jsonl");
        using ManualResetEventSlim held = new(false);
        using ManualResetEventSlim release = new(false);
        using ManualResetEventSlim start = new(false);
        using CancellationTokenSource stop = new();
        using CountdownEvent contendersReady = new(4);
        Task reader = Task.Run(() => { while (!stop.IsCancellationRequested) { try { _ = LogTailReader.Read(path); } catch (FileNotFoundException) { } Thread.Yield(); } });
        List<Task<(List<double>,List<double>,List<double>)>> threads = new();
        List<(Process Process, Task<string> Output)> children = new();
        Task? holder = null;
        Stopwatch preparation = Stopwatch.StartNew();
        try
        {
            // Contenders report ready before the lock is held: process startup cannot
            // consume the acquisition timeout being tested.
            for (int i = 0; i < 4; i++)
            {
                string worker = "thread-" + i;
                threads.Add(Task.Factory.StartNew(() =>
                {
                    List<double> w = new(), h = new(), r = new();
                    contendersReady.Signal();
                    Require(start.Wait(TimeSpan.FromSeconds(30)), "thread start signal timed out");
                    try { WriteMany(path, worker, w, h, r); return (w,h,r); }
                    finally { Report(worker, w, h, r); }
                }, TaskCreationOptions.LongRunning));
            }
            for (int i = 0; i < 3; i++)
            {
                ProcessStartInfo info = new(Environment.ProcessPath!) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
                info.ArgumentList.Add("--contention-child"); info.ArgumentList.Add(path); info.ArgumentList.Add("process-" + i);
                Process child = Process.Start(info) ?? throw new Exception("child failed to start");
                children.Add((child, child.StandardOutput.ReadToEndAsync()));
            }
            Require(contendersReady.Wait(TimeSpan.FromSeconds(20)), "thread contenders did not start");
            Require(SpinWait.SpinUntil(() => Enumerable.Range(0, 3).All(i => File.Exists(Path.Combine(dir, "ready-process-" + i))), TimeSpan.FromSeconds(20)), "child contenders did not start");
            Console.WriteLine($"LOCK_PREPARATION elapsed_ms={preparation.ElapsedMilliseconds} processes=3 threads=4");
            holder = Task.Factory.StartNew(() => RotatingJsonlLog.AppendWithWriterForTest(path, "{\"worker\":\"holder\",\"i\":0}", Policy,
                (stream, bytes) => { Stopwatch hold = Stopwatch.StartNew(); held.Set(); if (!release.Wait(TimeSpan.FromSeconds(10))) throw new Exception("holder release timed out"); stream.Write(bytes); Console.WriteLine($"LOCK_CONTROLLED_HOLD elapsed_ms={hold.ElapsedMilliseconds} process_id={Environment.ProcessId} thread_id={Environment.CurrentManagedThreadId}"); }), TaskCreationOptions.LongRunning);
            Require(held.Wait(TimeSpan.FromSeconds(5)), "holder did not acquire lock");
            File.WriteAllText(Path.Combine(dir, "start"), "start");
            start.Set();
            Thread.Sleep(800);
            release.Set();
            holder.GetAwaiter().GetResult();
            Task.WaitAll(threads.ToArray());
            foreach (var child in children)
            {
                Require(child.Process.WaitForExit(20000), "log writer timed out");
                Require(child.Process.ExitCode == 0, "cross-process log writer failed");
            }
        }
        finally
        {
            release.Set(); start.Set(); stop.Cancel();
            reader.GetAwaiter().GetResult();
            foreach (var child in children)
            {
                if (!child.Process.HasExited) { child.Process.Kill(true); child.Process.WaitForExit(); }
                string output = child.Output.GetAwaiter().GetResult();
                foreach (string line in output.Split('\n', StringSplitOptions.RemoveEmptyEntries))
                    if (line.StartsWith("LOCK_METRICS ") || line.StartsWith("LOCK_FAILURE ")) Console.WriteLine(line.Trim());
                Console.WriteLine($"LOCK_CHILD_EXIT code={child.Process.ExitCode} process_id={child.Process.Id}");
                child.Process.Dispose();
            }
            if (holder is not null) holder.GetAwaiter().GetResult();
        }
        List<double> waits = threads.SelectMany(t => t.Result.Item1).ToList();
        List<double> holds = threads.SelectMany(t => t.Result.Item2).ToList();
        List<double> rotations = threads.SelectMany(t => t.Result.Item3).ToList();
        Report("threads", waits, holds, rotations);
        Require(waits.Max() >= 600, "controlled contention was not observed");
        Require(Directory.GetFiles(dir, "mixed.jsonl.*").Any(f => !f.EndsWith(".lock")), "concurrent rotation was not exercised");
        HashSet<string> records = new();
        foreach (string file in Directory.GetFiles(dir, "mixed.jsonl*").Where(f => !f.EndsWith(".lock")))
        {
            Require(new FileInfo(file).Length <= Policy.MaxFileBytes, "rotation size exceeded");
            foreach (string line in File.ReadAllLines(file))
            {
                using JsonDocument doc = JsonDocument.Parse(line);
                string key = doc.RootElement.GetProperty("worker").GetString() + ":" + doc.RootElement.GetProperty("i").GetInt32();
                Require(records.Add(key), "duplicate record");
            }
        }
        Require(records.Count == 421, "records lost or duplicated");
        for (int w = 0; w < 7; w++) for (int i = 0; i < 60; i++) Require(records.Contains((w < 4 ? "thread-" + w : "process-" + (w-4)) + ":" + i), "missing record");

        string timeoutPath = Path.Combine(dir, "timeout.lock");
        using (FileStream blocker = new(timeoutPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
        {
            Stopwatch clock = Stopwatch.StartNew();
            try { using FileStream unexpected = RotatingJsonlLog.AcquireForTest(timeoutPath, TimeSpan.FromMilliseconds(200)); throw new Exception("lock unexpectedly acquired"); }
            catch (IOException ex) when (ex.InnerException is IOException) { Require(clock.ElapsedMilliseconds >= 180 && clock.ElapsedMilliseconds < 3000, "invalid timeout"); Console.WriteLine($"LOCK_TIMEOUT elapsed_ms={clock.ElapsedMilliseconds} win32_code={ex.InnerException.HResult & 0xffff}"); }
        }
        using (FileStream blocker = new(timeoutPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
        {
            // Exercise the public path and its production deadline, not only the injected shorter timeout.
            string publicPath = Path.Combine(dir, "timeout");
            Stopwatch clock = Stopwatch.StartNew();
            try { RotatingJsonlLog.Append(publicPath, "{}"); throw new Exception("public lock unexpectedly acquired"); }
            catch (IOException ex) when (ex.InnerException is IOException)
            {
                Require(clock.ElapsedMilliseconds >= 4800 && clock.ElapsedMilliseconds < 12000, "invalid production timeout");
                Console.WriteLine($"LOCK_PUBLIC_TIMEOUT elapsed_ms={clock.ElapsedMilliseconds}");
            }
        }
        RotatingJsonlLog.Append(Path.Combine(dir, "timeout"), "{}");
        try
        {
            using FileStream unexpected = RotatingJsonlLog.AcquireForTest(Path.Combine(dir, "missing-parent", "lock"), TimeSpan.FromSeconds(5));
            throw new Exception("non-sharing I/O error unexpectedly succeeded");
        }
        catch (DirectoryNotFoundException ex) { Require(ex.InnerException is null, "non-sharing error was converted to a timeout"); }
        using (RotatingJsonlLog.AcquireForTest(timeoutPath, TimeSpan.FromSeconds(1))) { }
        Require(File.Exists(timeoutPath), "stable lock sidecar removed");
        Console.WriteLine("PASS contention: threads/processes, rotation/readers, exact records, timeout, recovery and stable lock.");
    }

    private static void Report(string kind, List<double> waits, List<double> holds, List<double> rotations)
    {
        waits.Sort(); holds.Sort(); rotations.Sort();
        static double P(List<double> a, double p) => a.Count == 0 ? 0 : Math.Round(a[(int)Math.Ceiling(a.Count * p) - 1], 2);
        Console.WriteLine($"LOCK_METRICS kind={kind} process_id={Environment.ProcessId} thread_id={Environment.CurrentManagedThreadId} count={waits.Count} wait_p50_ms={P(waits,.5)} wait_p95_ms={P(waits,.95)} wait_max_ms={P(waits,1)} hold_max_ms={P(holds,1)} rotation_count={rotations.Count} rotation_max_ms={P(rotations,1)}");
    }
    private static void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }
}
