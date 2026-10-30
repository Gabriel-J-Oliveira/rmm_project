using System.Diagnostics;
using System.Text.Json;
using NightOwl.Agent.Shared;

internal static class RetentionClosureTests
{
    public static void WriteChild(string path, string worker)
    {
        for (int i = 0; i < 60; i++)
            RotatingJsonlLog.Append(path, JsonSerializer.Serialize(new { event_type = "concurrent", worker, i }));
    }

    public static void Run(string root)
    {
        string dir = Path.Combine(root, "retention");
        Directory.CreateDirectory(dir);
        LogRetentionPolicy small = new(1024, 3, 512, 30);
        string path = Path.Combine(dir, "bounded.jsonl");
        string essential = Path.Combine(dir, "job-result-pending.json");
        File.WriteAllText(essential, "unacknowledged-evidence");
        for (int i = 0; i < 180; i++)
        {
            RotatingJsonlLog.Append(path, JsonSerializer.Serialize(new { event_type = "test", i, text = new string('x', 75) }), small);
            _ = LogTailReader.Read(path);
        }
        string[] owned = Directory.GetFiles(dir, "bounded.jsonl*").Where(p => !p.EndsWith(".lock")).ToArray();
        Require(owned.Length == 4 && owned.All(p => new FileInfo(p).Length <= 1024), "Rotation exceeded file/count quota.");
        Require(owned.Sum(p => new FileInfo(p).Length) <= 4096, "Rotation exceeded total quota.");
        foreach (string file in owned)
            foreach (string line in File.ReadAllLines(file)) { using JsonDocument parsed = JsonDocument.Parse(line); }
        Require(File.ReadAllText(essential) == "unacknowledged-evidence", "Log rotation touched job evidence.");
        File.SetLastWriteTimeUtc(path + ".3", DateTime.UtcNow.AddDays(-31));
        RotatingJsonlLog.Append(path, "{\"event_type\":\"age\"}", small);
        Require(!File.Exists(path + ".3") || File.GetLastWriteTimeUtc(path + ".3") > DateTime.UtcNow.AddDays(-30), "Expired archive survived.");

        string secrets = Path.Combine(dir, "sanitized.jsonl");
        RotatingJsonlLog.Append(secrets, "{\"event_type\":\"test\",\"data\":{\"agentToken\":\"fixture-secret-token\",\"message\":\"Bearer abcdefghijklmnopqrstuvwxyz\"}}");
        string content = File.ReadAllText(secrets);
        Require(!content.Contains("fixture-secret-token") && !content.Contains("abcdefghijklmnopqrstuvwxyz"), "Secret was written to log.");
        Require(File.ReadAllLines(secrets).Length == 1, "Sanitized JSONL became multiline.");
        RotatingJsonlLog.Append(secrets, JsonSerializer.Serialize(new { event_type = "huge", text = new string('x', 2000) }), small);
        Require(File.ReadAllText(secrets).Contains("log.record_omitted"), "Oversized record missing omission marker.");

        string denied = Path.Combine(dir, "open.jsonl");
        File.WriteAllText(denied, new string('x', 1020));
        using (FileStream reader = new(denied, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        {
            ExpectIoFailure(() => RotatingJsonlLog.Append(denied, "{\"event_type\":\"blocked\"}", small));
            Require(new FileInfo(denied).Length == 1020, "Failed rename truncated or grew active log.");
        }
        RotatingJsonlLog.Append(denied, "{\"event_type\":\"recovered\"}", small);
        Require(File.ReadAllText(denied).Contains("recovered"), "Rotation did not recover after reader closed.");
        string readOnly = Path.Combine(dir, "readonly.jsonl");
        File.WriteAllText(readOnly, "{}");
        File.SetAttributes(readOnly, FileAttributes.ReadOnly);
        try { ExpectIoFailure(() => RotatingJsonlLog.Append(readOnly, "{}")); }
        finally { File.SetAttributes(readOnly, FileAttributes.Normal); }
        Require(File.ReadAllText(readOnly) == "{}", "Denied append changed existing evidence.");

        string fullDisk = Path.Combine(dir, "diskfull.jsonl");
        RotatingJsonlLog.Append(fullDisk, "{\"event_type\":\"before\"}", small);
        byte[] prior = File.ReadAllBytes(fullDisk);
        ExpectIoFailure(() => RotatingJsonlLog.AppendWithWriterForTest(fullDisk, "{\"event_type\":\"failed\"}", small,
            (_, _) => throw new IOException("Synthetic disk full", unchecked((int)0x80070070))));
        Require(File.ReadAllBytes(fullDisk).SequenceEqual(prior), "Disk-full write discarded existing evidence.");
        RotatingJsonlLog.Append(fullDisk, "{\"event_type\":\"recovered\"}", small);
        Require(File.ReadAllLines(fullDisk).Length == 2, "Disk-full write failed to release the process lock.");
        ExpectIoFailure(() => RotatingJsonlLog.AppendWithWriterForTest(fullDisk, "{}", small,
            (stream, _) => { stream.Write(new byte[] { (byte)'{', (byte)'"' }); throw new IOException("Synthetic partial write"); }));
        RotatingJsonlLog.Append(fullDisk, "{\"event_type\":\"after_partial\"}", small);
        string[] recovered = File.ReadAllLines(fullDisk);
        using (JsonDocument last = JsonDocument.Parse(recovered[^1]))
            Require(last.RootElement.GetProperty("event_type").GetString() == "after_partial", "Partial write swallowed the next event.");
        Require(recovered[^2] == "{\"", "Partial evidence must be preserved rather than truncated.");

        string rotating = Path.Combine(dir, "concurrent-rotation.jsonl");
        using (CancellationTokenSource stop = new())
        {
            RotatingJsonlLog.Append(rotating, "{}", small);
            Task reading = Task.Run(() =>
            {
                while (!stop.IsCancellationRequested)
                {
                    try { _ = LogTailReader.Read(rotating); }
                    catch (FileNotFoundException) { } // Expected between rename and new active file.
                    Thread.Yield();
                }
            });
            try
            {
                Parallel.For(0, 4, worker =>
                {
                    for (int i = 0; i < 60; i++)
                        RotatingJsonlLog.Append(rotating, JsonSerializer.Serialize(new { worker, i, text = new string('x', 75) }), small);
                });
            }
            finally { stop.Cancel(); reading.GetAwaiter().GetResult(); }
        }
        foreach (string file in Directory.GetFiles(dir, "concurrent-rotation.jsonl*").Where(p => !p.EndsWith(".lock")))
        {
            Require(new FileInfo(file).Length <= small.MaxFileBytes, "Concurrent rotation exceeded file quota.");
            foreach (string line in File.ReadAllLines(file)) { using JsonDocument parsed = JsonDocument.Parse(line); }
        }

        string concurrent = Path.Combine(dir, "concurrent.jsonl");
        Parallel.For(0, 4, i => WriteChild(concurrent, "thread-" + i));
        List<Process> children = new();
        try
        {
            for (int i = 0; i < 3; i++)
            {
                ProcessStartInfo info = new(Environment.ProcessPath!) { UseShellExecute = false, CreateNoWindow = true };
                info.ArgumentList.Add("--log-writer"); info.ArgumentList.Add(concurrent); info.ArgumentList.Add("process-" + i);
                children.Add(Process.Start(info)!);
            }
            foreach (Process child in children)
            {
                Require(child.WaitForExit(15000), "Log writer timed out.");
                Require(child.ExitCode == 0, "Cross-process log writer failed.");
            }
        }
        finally
        {
            foreach (Process child in children)
            {
                if (!child.HasExited) { child.Kill(true); child.WaitForExit(); }
                child.Dispose();
            }
        }
        string[] lines = File.ReadAllLines(concurrent);
        Require(lines.Length == 420, "Concurrent writers lost or duplicated a record.");
        foreach (string line in lines) { using JsonDocument parsed = JsonDocument.Parse(line); }

        string queueDir = Path.Combine(dir, "pending");
        PendingResultQueue queue = new(queueDir, maxRecords: 1);
        PendingResultRecord record = queue.Enqueue("ping", new { job_id = Guid.NewGuid().ToString(), status = "completed" });
        string recordPath = Directory.GetFiles(queueDir, "*.json").Single();
        File.SetLastWriteTimeUtc(recordPath, DateTime.UtcNow.AddDays(-365));
        byte[] original = File.ReadAllBytes(recordPath);
        queue.Prune();
        Require(File.ReadAllBytes(recordPath).SequenceEqual(original), "Prune deleted aged noncritical unacknowledged result.");
        ExpectFull(() => queue.Enqueue("ping", new { job_id = Guid.NewGuid().ToString(), status = "completed" }));
        Require(File.ReadAllBytes(recordPath).SequenceEqual(original), "Full admission changed existing evidence.");
        queue.MarkSent(record);
        queue.Enqueue("ping", new { job_id = Guid.NewGuid().ToString(), status = "completed" });
        Require(queue.LoadAll().Count == 1 && queue.IsAcknowledged(record.ResultId), "ACK did not release capacity and retain receipt.");
        PendingResultQueue bytesQueue = new(Path.Combine(dir, "byte-quota"), maxTotalBytes: 512);
        ExpectFull(() => bytesQueue.Enqueue("ping", new { job_id = Guid.NewGuid().ToString(), status = "completed", text = new string('x', 500) }));
        Require(bytesQueue.LoadAll().Count == 0, "New record exceeded byte quota.");
        string corruptQueueDir = Path.Combine(dir, "corrupt-quota");
        PendingResultQueue corruptQueue = new(corruptQueueDir, maxRecords: 1);
        corruptQueue.Enqueue("ping", new { job_id = Guid.NewGuid().ToString(), status = "completed" });
        File.WriteAllText(Directory.GetFiles(corruptQueueDir, "*.json").Single(), "{corrupt-evidence");
        Require(corruptQueue.LoadAll().Count == 0, "Corrupt result should leave active delivery.");
        ExpectFull(() => corruptQueue.Enqueue("ping", new { job_id = Guid.NewGuid().ToString(), status = "completed" }));
        Require(File.ReadAllText(Directory.GetFiles(corruptQueue.QuarantineDirectory, "*.json").Single()) == "{corrupt-evidence",
            "Corrupt evidence must retain capacity and survive backpressure rather than grow without quota.");
        Console.WriteLine("PASS retention: size/count/age, JSONL/redaction, concurrent rotation/readers, permission/simulated disk-full failures, threads/processes, pending ACK/backpressure.");
    }

    private static void ExpectIoFailure(Action action)
    {
        try { action(); }
        catch (IOException) { return; }
        catch (UnauthorizedAccessException) { return; }
        throw new InvalidOperationException("Expected filesystem failure.");
    }

    private static void ExpectFull(Action action)
    {
        try { action(); }
        catch (InvalidOperationException ex) when (ex.Message == JobErrorCodes.ResultQueueFull) { return; }
        throw new InvalidOperationException("Expected queue backpressure.");
    }

    private static void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }
}
