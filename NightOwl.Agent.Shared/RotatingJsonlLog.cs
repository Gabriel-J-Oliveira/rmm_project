using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace NightOwl.Agent.Shared;

public sealed record LogRetentionPolicy(long MaxFileBytes = 10 * 1024 * 1024,
    int ArchiveCount = 5, int MaxRecordBytes = 64 * 1024, int ArchiveDays = 30);

/// <summary>Bounded technical logs only. Never use for job results or recovery state.</summary>
public static class RotatingJsonlLog
{
    private static readonly LogRetentionPolicy DefaultPolicy = new();
    private static readonly TimeSpan LockTimeout = TimeSpan.FromSeconds(5);

    public static void Append(string path, string json, LogRetentionPolicy? policy = null)
        => AppendCore(path, json, policy ?? DefaultPolicy, null, null);

    internal static void AppendWithWriterForTest(string path, string json, LogRetentionPolicy policy,
        Action<FileStream, byte[]> writer) => AppendCore(path, json, policy, writer, null);

    internal static void AppendObservedForTest(string path, string json, LogRetentionPolicy policy,
        Action<string> observer, Action<FileStream, byte[]>? writer = null)
        => AppendCore(path, json, policy, writer, observer);

    internal static FileStream AcquireForTest(string path, TimeSpan timeout) => Acquire(path, timeout);

    private static void AppendCore(string path, string json, LogRetentionPolicy policy, Action<FileStream, byte[]>? writer, Action<string>? observer)
    {
        if (policy.MaxFileBytes < 256 || policy.ArchiveCount is < 1 or > 20
            || policy.MaxRecordBytes < 128 || policy.MaxRecordBytes > policy.MaxFileBytes
            || policy.ArchiveDays < 1)
            throw new ArgumentOutOfRangeException(nameof(policy));

        // Re-serialize to keep exactly one JSON object on each physical line.
        SanitizationResult sanitized = NightOwlSanitizer.SanitizeJson(json);
        using JsonDocument document = JsonDocument.Parse(sanitized.Value);
        string line = JsonSerializer.Serialize(document.RootElement);
        if (sanitized.UnsafeContentDetected || Encoding.UTF8.GetByteCount(line) + 1 > policy.MaxRecordBytes)
        {
            string eventType = document.RootElement.TryGetProperty("event_type", out JsonElement field)
                && field.ValueKind == JsonValueKind.String ? field.GetString() ?? "" : "";
            // Do not copy untrusted oversized text or secret-like content into the marker.
            line = JsonSerializer.Serialize(new { timestamp = DateTimeOffset.UtcNow,
                event_type = "log.record_omitted", reason = sanitized.UnsafeContentDetected ? "unsafe_content" : "record_limit",
                original_event_type = eventType.Length <= 64 ? NightOwlSanitizer.SanitizeText(eventType).Value : "" });
            if (Encoding.UTF8.GetByteCount(line) + 1 > policy.MaxRecordBytes)
                line = "{\"event_type\":\"log.record_omitted\"}";
        }
        byte[] bytes = Encoding.UTF8.GetBytes(line + "\n");
        path = Path.GetFullPath(path);
        string directory = Path.GetDirectoryName(path)!;
        RejectReparseAncestors(directory);
        Directory.CreateDirectory(directory);
        RejectReparse(path);
        RejectReparse(path + ".lock");

        // A zero-byte sidecar stays in place: deleting it creates competing lock inodes.
        // File sharing coordinates all instances/processes/sessions without new ACL grants.
        observer?.Invoke("wait_start");
        using FileStream lease = Acquire(path + ".lock", LockTimeout);
        observer?.Invoke("acquired");
        RejectReparse(path);
        for (int i = 1; i <= policy.ArchiveCount; i++) RejectReparse(path + "." + i);
        for (int i = 1; i <= policy.ArchiveCount; i++)
        {
            string archive = path + "." + i;
            if (File.Exists(archive) && File.GetLastWriteTimeUtc(archive) < DateTime.UtcNow.AddDays(-policy.ArchiveDays))
                File.Delete(archive);
        }

        long length = 0;
        bool separatePartialRecord = false;
        if (File.Exists(path))
        {
            using FileStream tail = new(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            length = tail.Length;
            if (length > 0) { tail.Position = length - 1; separatePartialRecord = tail.ReadByte() != '\n'; }
        }
        if (length > 0 && length > policy.MaxFileBytes - bytes.Length - (separatePartialRecord ? 1 : 0))
        {
            observer?.Invoke("rotation_start");
            // No truncate-on-error fallback. A denied rename stops this write.
            File.Delete(path + "." + policy.ArchiveCount);
            for (int i = policy.ArchiveCount - 1; i >= 1; i--)
                if (File.Exists(path + "." + i)) File.Move(path + "." + i, path + "." + (i + 1), true);
            File.Move(path, path + ".1");
            separatePartialRecord = false;
            observer?.Invoke("rotation_end");
        }
        using FileStream output = new(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete, bufferSize: 1);
        if (separatePartialRecord) output.WriteByte((byte)'\n');
        if (writer is null) output.Write(bytes);
        else writer(output, bytes);
        observer?.Invoke("write_complete");
    }

    private static FileStream Acquire(string path, TimeSpan timeout)
    {
        if (timeout <= TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(timeout));
        Stopwatch elapsed = Stopwatch.StartNew();
        IOException? lastContention = null;
        while (true)
        {
            if (lastContention is not null && elapsed.Elapsed >= timeout)
                throw new IOException("Timed out acquiring the log file lock.", lastContention);
            try { return new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
            catch (IOException ex) when (IsSharingViolation(ex))
            {
                lastContention = ex;
                TimeSpan remaining = timeout - elapsed.Elapsed;
                if (remaining <= TimeSpan.Zero)
                    throw new IOException("Timed out acquiring the log file lock.", ex);
                Thread.Sleep(remaining < TimeSpan.FromMilliseconds(10) ? remaining : TimeSpan.FromMilliseconds(10));
            }
        }
    }

    private static bool IsSharingViolation(IOException error)
        => OperatingSystem.IsWindows() ? (error.HResult & 0xffff) is 32 or 33
            : (error.HResult & 0xffff) == 11;

    private static void RejectReparseAncestors(string directory)
    {
        for (DirectoryInfo? current = new(directory); current is not null; current = current.Parent)
            RejectReparse(current.FullName);
    }

    private static void RejectReparse(string path)
    {
        if ((File.Exists(path) || Directory.Exists(path)) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("Log path must not traverse a reparse point.");
    }
}
