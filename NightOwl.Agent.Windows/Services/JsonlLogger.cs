using System.Text.Json;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class JsonlLogger
{
    private readonly ConfigService _configService;
    private readonly SemaphoreSlim _lock = new(1, 1);
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public JsonlLogger(ConfigService configService)
    {
        _configService = configService;
    }

    public async Task LogAsync(string eventType, string message, object? data = null, CancellationToken ct = default, string level = "info")
    {
        AgentConfig config = _configService.Current ?? _configService.Load();
        string? directory = Path.GetDirectoryName(config.LogPath);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var record = new
        {
            timestamp = DateTimeOffset.UtcNow,
            level,
            event_type = eventType,
            message,
            data
        };

        string line = JsonSerializer.Serialize(record, JsonOptions);
        await _lock.WaitAsync(ct);
        try
        {
            await File.AppendAllTextAsync(config.LogPath, line + Environment.NewLine, ct);
        }
        finally
        {
            _lock.Release();
        }
    }
}
