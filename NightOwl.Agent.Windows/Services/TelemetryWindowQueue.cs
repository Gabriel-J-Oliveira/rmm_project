using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

internal sealed class TelemetryWindowQueue
{
    private readonly string _directory;
    private readonly long _maxBytes;
    private readonly TimeSpan _maxAge;
    private readonly object _gate = new();
    internal int LostWindows { get; private set; }
    internal int CorruptWindows { get; private set; }
    internal TelemetryWindowQueue(string directory, long maxBytes = 128L * 1024 * 1024, TimeSpan? maxAge = null)
    {
        _directory = directory;
        _maxBytes = Math.Max(1024, maxBytes);
        _maxAge = maxAge ?? TimeSpan.FromDays(8);
        Directory.CreateDirectory(directory);
        Trim(DateTimeOffset.UtcNow);
    }
    private string FilePath(TelemetryWindow window) => Path.Combine(_directory, $"{window.StartAt:yyyyMMddTHHmmssZ}-{window.WindowId:N}.json");
    internal void Enqueue(TelemetryWindow window)
    {
        lock (_gate)
        {
            Validate(window);
            string path = FilePath(window);
            string json = JsonSerializer.Serialize(window);
            if (new System.Text.UTF8Encoding(false).GetByteCount(json) > 200 * 1024) { LostWindows++; return; }
            if (File.Exists(path))
            {
                if (File.ReadAllText(path) != json) throw new InvalidDataException("Conflicting telemetry window identity.");
                return;
            }
            NightOwlFileStore.WriteAllText(path, json);
            Trim(DateTimeOffset.UtcNow);
        }
    }
    private void Trim(DateTimeOffset now)
    {
        FileInfo[] files = new DirectoryInfo(_directory).GetFiles("*.json").OrderBy(file => file.Name, StringComparer.Ordinal).ToArray();
        long bytes = files.Sum(file => file.Length);
        int count = files.Length;
        foreach (FileInfo file in files)
        {
            bool expired = file.Name.Length >= 16 && DateTimeOffset.TryParseExact(file.Name[..16], "yyyyMMdd'T'HHmmss'Z'", System.Globalization.CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.AssumeUniversal, out DateTimeOffset at) && at < now - _maxAge;
            if (expired || bytes > _maxBytes || count > 11520)
            {
                bytes -= file.Length; count--; file.Delete(); LostWindows++;
            }
        }
    }
    internal IReadOnlyList<TelemetryWindow> Snapshot(DateTimeOffset now)
    {
        lock (_gate)
        {
            Trim(now);
            List<TelemetryWindow> result = new();
            long bytes = 0;
            foreach (string path in Directory.EnumerateFiles(_directory, "*.json").Order(StringComparer.Ordinal).Take(100))
            {
                try
                {
                    FileInfo file = new(path);
                    if (file.Length > 200 * 1024) throw new InvalidDataException();
                    TelemetryWindow window = JsonSerializer.Deserialize<TelemetryWindow>(File.ReadAllText(path)) ?? throw new InvalidDataException();
                    Validate(window);
                    if (window.WindowId == Guid.Empty || window.EndAt - window.StartAt != TimeSpan.FromMinutes(1) || FilePath(window) != path)
                        throw new InvalidDataException();
                    if (bytes + file.Length > 200 * 1024 || result.Count == 24) break;
                    bytes += file.Length; result.Add(window);
                }
                catch (Exception error) when (error is JsonException or InvalidDataException)
                {
                    string quarantine = Path.Combine(_directory, "quarantine");
                    Directory.CreateDirectory(quarantine);
                    File.Move(path, Path.Combine(quarantine, Path.GetFileName(path) + "." + Guid.NewGuid().ToString("N")));
                    CorruptWindows++;
                    FileInfo[] corrupt = new DirectoryInfo(quarantine).GetFiles().OrderByDescending(file => file.LastWriteTimeUtc).ToArray();
                    long retainedBytes = 0;
                    for (int index = 0; index < corrupt.Length; index++)
                    {
                        retainedBytes += corrupt[index].Length;
                        if (index >= 128 || retainedBytes > 16 * 1024 * 1024) corrupt[index].Delete();
                    }
                }
            }
            return result;
        }
    }
    internal void Acknowledge(IEnumerable<TelemetryWindow> sent, IEnumerable<Guid> accepted)
    {
        HashSet<Guid> ids = accepted.ToHashSet();
        lock (_gate)
            foreach (TelemetryWindow window in sent.Where(window => ids.Contains(window.WindowId)))
                File.Delete(FilePath(window));
    }

    private static void Validate(TelemetryWindow window)
    {
        if (window.WindowId == Guid.Empty || window.ResolutionSeconds != 60 || window.ExpectedSamples != 6 ||
            window.ReceivedSamples is < 0 or > 6 || window.EndAt-window.StartAt != TimeSpan.FromMinutes(1) ||
            window.StartAt.Second != 0 || window.StartAt.Millisecond != 0 || window.Metrics is null || window.Quality is null || window.Metrics.Count > 256)
            throw new InvalidDataException("Invalid persisted minute window.");
        foreach ((string name, MetricWindow metric) in window.Metrics)
        {
            if (name.Length > 220 || metric is null || metric.Count < 1 || metric.Count > window.ReceivedSamples ||
                metric.Min < 0 || metric.Min > metric.Max || metric.Weight <= 0 || metric.Distribution is null ||
                new[] { metric.Sum, metric.Min, metric.Max, metric.Weight, metric.WeightedSum }.Any(value => !double.IsFinite(value)) ||
                metric.Sum < metric.Min*metric.Count-.001 || metric.Sum > metric.Max*metric.Count+.001 ||
                metric.WeightedSum < metric.Min*metric.Weight-.001 || metric.WeightedSum > metric.Max*metric.Weight+.001)
                throw new InvalidDataException("Invalid persisted metric.");
            if (metric.Distribution.Count > 101 || metric.Distribution.Any(bin =>
                    !int.TryParse(bin.Key, out int bucket) || bucket < 0 || bucket > 100 ||
                    bucket < Math.Floor(metric.Min) || bucket > Math.Floor(metric.Max) ||
                    !double.IsFinite(bin.Value) || bin.Value <= 0) ||
                (metric.Distribution.Count > 0 && Math.Abs(metric.Distribution.Values.Sum()-metric.Weight) > Math.Max(.000001, metric.Weight*1e-8)))
                throw new InvalidDataException("Invalid persisted distribution.");
            if (name is "cpu.usage_percent" or "memory.used_percent" or "memory.available_percent" or "memory.committed_percent")
                if (metric.Max > 100 || metric.Distribution.Count == 0)
                    throw new InvalidDataException("Invalid persisted percentage.");
        }
    }
}
