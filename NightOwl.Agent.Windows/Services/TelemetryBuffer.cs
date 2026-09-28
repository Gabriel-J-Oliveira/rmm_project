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
    private int _count;

    internal TelemetryBuffer(string path, int maxSamples, TimeSpan maxAge, Func<DateTimeOffset>? now = null)
    {
        _path = path;
        _maxSamples = Math.Clamp(maxSamples, 6, 2304);
        _maxAge = maxAge;
        _now = now ?? (() => DateTimeOffset.UtcNow);
        _count = ReadSamples().Count;
    }

    internal int Count { get { lock (_gate) return _count; } }
    internal int MaxSamples => _maxSamples;
    internal TimeSpan MaxAge => _maxAge;

    internal int Add(TelemetrySample sample)
    {
        lock (_gate)
        {
            List<TelemetrySample> next = ReadSamples()
                .Where(item => item.CollectedAt >= _now() - _maxAge && item.SampleId != sample.SampleId)
                .Append(sample)
                .OrderBy(item => item.CollectedAt)
                .ToList();
            int discarded = Math.Max(0, next.Count - _maxSamples);
            if (discarded > 0) next.RemoveRange(0, discarded);
            Persist(next);
            _count = next.Count;
            return discarded;
        }
    }

    internal IReadOnlyList<TelemetrySample> Snapshot(int limit)
    {
        lock (_gate) return ReadSamples().Take(Math.Clamp(limit, 1, 24)).ToList();
    }

    internal void Acknowledge(IEnumerable<Guid> sampleIds)
    {
        lock (_gate)
        {
            HashSet<Guid> sent = sampleIds.ToHashSet();
            List<TelemetrySample> next = ReadSamples().Where(item => !sent.Contains(item.SampleId)).ToList();
            Persist(next);
            _count = next.Count;
        }
    }

    private void Persist(List<TelemetrySample> samples)
    {
        NightOwlFileStore.WriteAllText(_path, JsonSerializer.Serialize(samples, JsonOptions));
    }

    private List<TelemetrySample> ReadSamples()
    {
        if (!File.Exists(_path))
        {
            if (_count > 0) throw new IOException("Telemetry buffer disappeared while samples were pending.");
            return new();
        }
        if (new FileInfo(_path).Length > 32 * 1024 * 1024)
            throw new InvalidDataException("Telemetry buffer exceeds its file size limit.");
        List<TelemetrySample> samples = JsonSerializer.Deserialize<List<TelemetrySample>>(File.ReadAllText(_path), JsonOptions)
            ?? throw new JsonException("Telemetry buffer must be an array.");
        if (samples.Any(sample => sample is null || sample.SampleId == Guid.Empty || sample.CollectedAt == default))
            throw new JsonException("Telemetry buffer contains an invalid sample.");
        return samples;
    }
}
