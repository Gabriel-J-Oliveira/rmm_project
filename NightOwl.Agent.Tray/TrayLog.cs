using System.Text.Json;

namespace NightOwl.Agent.Tray;

internal static class TrayLog
{
    private const string LogPath = @"C:\ProgramData\NightOwl\Logs\agent-tray.jsonl";

    public static void Write(string eventType, string message, object? metadata = null)
    {
        try
        {
            string? dir = Path.GetDirectoryName(LogPath);
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
            File.AppendAllText(LogPath, JsonSerializer.Serialize(entry) + Environment.NewLine);
        }
        catch
        {
            // Tray logging must never prevent the UI from starting.
        }
    }
}
