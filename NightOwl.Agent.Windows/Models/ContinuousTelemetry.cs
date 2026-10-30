using System.Text.Json.Serialization;

namespace NightOwl.Agent.Windows.Models;

internal sealed class NativeReading
{
    [JsonPropertyName("sample_id")] public Guid SampleId { get; init; } = Guid.NewGuid();
    [JsonPropertyName("collected_at")] public DateTimeOffset CollectedAt { get; init; } = DateTimeOffset.UtcNow;
    [JsonPropertyName("metrics")] public Dictionary<string, double> Metrics { get; init; } = new();
    [JsonPropertyName("quality")] public Dictionary<string, object?> Quality { get; init; } = new();
    [JsonIgnore] public Dictionary<string, double> Weights { get; init; } = new();
    [JsonIgnore] public double IntervalSeconds { get; set; }
}

internal sealed class MetricWindow
{
    [JsonPropertyName("count")] public int Count { get; set; }
    [JsonPropertyName("sum")] public double Sum { get; set; }
    [JsonPropertyName("min")] public double Min { get; set; } = double.MaxValue;
    [JsonPropertyName("max")] public double Max { get; set; }
    [JsonPropertyName("weight")] public double Weight { get; set; }
    [JsonPropertyName("weighted_sum")] public double WeightedSum { get; set; }
    [JsonPropertyName("distribution")] public Dictionary<string, double> Distribution { get; set; } = new();
    internal void Add(double value, double weight, bool percent)
    {
        if (!double.IsFinite(value) || value < 0 || !double.IsFinite(weight) || weight <= 0) return;
        if (!double.IsFinite(Sum + value) || !double.IsFinite(Weight + weight) || !double.IsFinite(WeightedSum + value * weight)) return;
        Count++; Sum += value; Min = Math.Min(Min, value); Max = Math.Max(Max, value);
        Weight += weight; WeightedSum += value * weight;
        if (percent && value <= 100)
        {
            string bucket = ((int)Math.Floor(value)).ToString(System.Globalization.CultureInfo.InvariantCulture);
            Distribution[bucket] = Distribution.GetValueOrDefault(bucket) + weight;
        }
        else if (percent) Distribution.Clear();
    }
}

internal sealed class TelemetryWindow
{
    [JsonPropertyName("window_id")] public Guid WindowId { get; set; }
    [JsonPropertyName("start_at")] public DateTimeOffset StartAt { get; set; }
    [JsonPropertyName("end_at")] public DateTimeOffset EndAt { get; set; }
    [JsonPropertyName("resolution_seconds")] public int ResolutionSeconds { get; set; } = 60;
    [JsonPropertyName("expected_samples")] public int ExpectedSamples { get; set; } = 6;
    [JsonPropertyName("received_samples")] public int ReceivedSamples { get; set; }
    [JsonPropertyName("metrics")] public Dictionary<string, MetricWindow> Metrics { get; set; } = new();
    [JsonPropertyName("quality")] public Dictionary<string, object?> Quality { get; set; } = new();
}
