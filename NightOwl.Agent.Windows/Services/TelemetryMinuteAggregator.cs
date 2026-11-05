using System.Security.Cryptography;
using System.Text;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

internal sealed class TelemetryMinuteAggregator
{
    private readonly string _machineId;
    private TelemetryWindow? _current;
    private long? _lastSlot;
    internal int ClockDiscardedWindows { get; private set; }
    internal TelemetryMinuteAggregator(string machineId) => _machineId = machineId;
    internal static DateTimeOffset Minute(DateTimeOffset at) => DateTimeOffset.FromUnixTimeSeconds(at.ToUnixTimeSeconds() / 60 * 60);
    internal TelemetryWindow? Advance(DateTimeOffset now)
    {
        if (_current is not null && now < _current.StartAt)
        {
            _current = null; _lastSlot = null; ClockDiscardedWindows++;
            return null;
        }
        if (_current is null || now < _current.EndAt) return null;
        TelemetryWindow complete = _current;
        _current = null; _lastSlot = null;
        complete.Quality["missing_samples"] = complete.ExpectedSamples - complete.ReceivedSamples;
        return complete;
    }

    internal void Add(NativeReading reading)
    {
        DateTimeOffset start = Minute(reading.CollectedAt);
        if (_current is not null && _current.StartAt != start) throw new InvalidOperationException("Advance window before adding a different minute.");
        _current ??= new()
        {
            WindowId = new Guid(SHA256.HashData(Encoding.UTF8.GetBytes(_machineId + ":" + start.ToUnixTimeSeconds()))[..16]),
            StartAt = start, EndAt = start.AddMinutes(1),
        };
        long slot = reading.CollectedAt.ToUnixTimeSeconds() / 10;
        if (_lastSlot == slot || _current.ReceivedSamples >= 6) return;
        _lastSlot = slot;
        _current.ReceivedSamples++;
        foreach ((string key, double value) in reading.Metrics)
        {
            if (!double.IsFinite(value) || value < 0) continue;
            double weight = reading.Weights.GetValueOrDefault(key, reading.IntervalSeconds is > 0 and <= 30 ? reading.IntervalSeconds : 0);
            if (weight <= 0 && !key.StartsWith("cpu.", StringComparison.Ordinal) && !key.StartsWith("network.", StringComparison.Ordinal) && !key.StartsWith("disk.", StringComparison.Ordinal)) weight = 10;
            if (weight <= 0) continue;
            if (!_current.Metrics.TryGetValue(key, out MetricWindow? summary)) _current.Metrics[key] = summary = new();
            summary.Add(value, weight, key.EndsWith("_percent", StringComparison.Ordinal));
            if (summary.Count == 0) _current.Metrics.Remove(key);
        }
        // Device IDs, availability and source metadata, never credentials or command lines.
        foreach ((string key, object? value) in reading.Quality) _current.Quality[key] = value;
        _current.Quality["aggregation_basis"] = "UTC_capture_bins; elapsed_time_weighted_rates; operation_weighted_latency; instantaneous_memory_step_estimate";
        if (!_current.Quality.TryGetValue("group_status_counts", out object? counts) || counts is not Dictionary<string, Dictionary<string, int>> groups)
        {
            groups = new Dictionary<string, Dictionary<string, int>>();
            _current.Quality["group_status_counts"] = groups;
        }
        foreach (string group in new[] { "cpu", "memory", "commit", "disk", "network" })
        {
            if (!groups.TryGetValue(group, out var statuses)) groups[group] = statuses = new();
            string status = reading.Quality.GetValueOrDefault(group)?.ToString() ?? "unknown";
            statuses[status] = statuses.GetValueOrDefault(status) + 1;
        }
    }
}
