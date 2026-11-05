using System.Text;

namespace NightOwl.Agent.Shared;

public sealed record LogTail(IReadOnlyList<string> Lines, int BytesRead, bool Truncated);

public static class LogTailReader
{
    public static LogTail Read(string path, int maxLines = 400, int maxBytes = 1024 * 1024)
    {
        if (maxLines is < 1 or > 10000)
            throw new ArgumentOutOfRangeException(nameof(maxLines));
        if (maxBytes is < 1 or > 16 * 1024 * 1024)
            throw new ArgumentOutOfRangeException(nameof(maxBytes));
        using FileStream stream = new(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        long length = stream.Length;
        int count = (int)Math.Min(length, maxBytes);
        long offset = length - count;
        bool skipPartial = false;
        int probe = 0;
        if (offset > 0)
        {
            stream.Position = offset - 1;
            int previous = stream.ReadByte();
            probe = previous < 0 ? 0 : 1;
            skipPartial = previous != '\n';
        }
        stream.Position = offset;
        byte[] buffer = new byte[count];
        int read = 0;
        while (read < count)
        {
            int received = stream.Read(buffer, read, count - read);
            if (received == 0) break;
            read += received;
        }
        // Fixed snapshot bounds prevent an appending writer from extending this read forever.
        string text = Encoding.UTF8.GetString(buffer, 0, read).TrimStart('\uFEFF');
        using StringReader reader = new(text);
        if (skipPartial) reader.ReadLine();
        Queue<string> lines = new();
        bool truncated = offset > 0;
        while (reader.ReadLine() is { } line)
        {
            lines.Enqueue(line);
            if (lines.Count > maxLines) { lines.Dequeue(); truncated = true; }
        }
        return new(lines.ToArray(), read + probe, truncated);
    }
}
