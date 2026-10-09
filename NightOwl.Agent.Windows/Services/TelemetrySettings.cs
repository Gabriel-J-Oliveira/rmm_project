using System.Text.Json;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed record TelemetrySettings(bool Enabled, int SampleSeconds, int FlushSeconds)
{
    public static TelemetrySettings FromConfig(AgentConfig config) =>
        new(config.TelemetryEnabled, config.TelemetrySampleSeconds, config.TelemetryFlushSeconds);

    public static TelemetrySettings Parse(Dictionary<string, object?> payload)
    {
        JsonElement value = JsonSerializer.SerializeToElement(payload);
        string[] fields = { "telemetryEnabled", "telemetrySampleSeconds", "telemetryFlushSeconds" };
        if (value.EnumerateObject().Count() != 3 || fields.Any(key => !value.TryGetProperty(key, out _))
            || value.GetProperty(fields[0]).ValueKind is not (JsonValueKind.True or JsonValueKind.False)
            || !value.GetProperty(fields[1]).TryGetInt32(out int sample)
            || !value.GetProperty(fields[2]).TryGetInt32(out int flush)
            || sample is < 60 or > 3600 || flush is < 60 or > 86400)
            throw new InvalidOperationException("Invalid telemetry parameters.");
        return new(value.GetProperty(fields[0]).GetBoolean(), sample, flush);
    }

    public void ApplyTo(AgentConfig config)
    {
        config.TelemetryEnabled = Enabled;
        config.TelemetrySampleSeconds = SampleSeconds;
        config.TelemetryFlushSeconds = FlushSeconds;
    }

    public object Report() => new { telemetryEnabled = Enabled, telemetrySampleSeconds = SampleSeconds,
        telemetryFlushSeconds = FlushSeconds };
}
