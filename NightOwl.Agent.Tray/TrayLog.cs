using System.Text.Json;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Tray;

internal static class TrayLog
{
    public static void Write(string eventType, string message, object? metadata = null)
    {
        try
        {
            string logPath = NightOwlPaths.Current.TrayLogPath;
            string? dir = Path.GetDirectoryName(logPath);
            if (!string.IsNullOrWhiteSpace(dir))
            {
                Directory.CreateDirectory(dir);
            }

            var entry = new
            {
                timestamp = DateTimeOffset.UtcNow.ToString("O"),
                event_type = eventType,
                message,
                metadata = metadata ?? new { }
            };
            File.AppendAllText(logPath, JsonSerializer.Serialize(entry) + Environment.NewLine);
        }
        catch
        {
            // Tray logging must never prevent the UI from starting.
        }
    }
}
