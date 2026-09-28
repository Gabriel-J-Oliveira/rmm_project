using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

internal sealed class TelemetryBuffer
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly object _gate = new();
    private readonly string _path;
    private readonly int _maxSamples;
    private readonly TimeSpan _maxAge;
    private readonly Func<DateTimeOffset> _now;
    private List<TelemetrySample> _samples;

    internal TelemetryBuffer(string path, int maxSamples, TimeSpan maxAge, Func<DateTimeOffset>? now = null)
    {
        _path = path;
        _maxSamples = Math.Clamp(maxSamples, 6, 2016);
        _maxAge = maxAge;
        _now = now ?? (() => DateTimeOffset.UtcNow);
        if (File.Exists(path) && new FileInfo(path).Length > 8 * 1024 * 1024)
            throw new InvalidDataException("Telemetry buffer exceeds its file size limit.");
        _samples = File.Exists(path)
            ? JsonSerializer.Deserialize<List<TelemetrySample>>(File.ReadAllText(path), JsonOptions) ?? new()
            : new();
    }

    internal int Count { get { lock (_gate) return _samples.Count; } }

    internal int Add(TelemetrySample sample)
    {
        lock (_gate)
        {
            List<TelemetrySample> next = _samples
                .Where(item => item.CollectedAt >= _now() - _maxAge && item.SampleId != sample.SampleId)
                .Append(sample)
                .OrderBy(item => item.CollectedAt)
                .ToList();
            int discarded = Math.Max(0, next.Count - _maxSamples);
            if (discarded > 0) next.RemoveRange(0, discarded);
            Persist(next);
            _samples = next;
            return discarded;
        }
    }

    internal IReadOnlyList<TelemetrySample> Snapshot(int limit)
    {
        lock (_gate) return _samples.Take(Math.Clamp(limit, 1, 24)).ToList();
    }

    internal void Acknowledge(IEnumerable<Guid> sampleIds)
    {
        lock (_gate)
        {
            HashSet<Guid> sent = sampleIds.ToHashSet();
            List<TelemetrySample> next = _samples.Where(item => !sent.Contains(item.SampleId)).ToList();
            Persist(next);
            _samples = next;
        }
    }

    private void Persist(List<TelemetrySample> samples)
    {
        NightOwlFileStore.WriteAllText(_path, JsonSerializer.Serialize(samples, JsonOptions));
    }
}
