using System.Text;

namespace NightOwl.Agent.Shared;

public static class NightOwlFileStore
{
    private static readonly UTF8Encoding Utf8NoBom = new(encoderShouldEmitUTF8Identifier: false);

    public static void WriteAllText(string path, string content, Encoding? encoding = null)
    {
        string tempPath = CreateTempPath(path);
        try
        {
            File.WriteAllText(tempPath, content, encoding ?? Utf8NoBom);
            File.Move(tempPath, path, overwrite: true);
        }
        finally
        {
            DeleteTempBestEffort(tempPath);
        }
    }

    public static void WriteAllBytes(string path, byte[] bytes)
    {
        string tempPath = CreateTempPath(path);
        try
        {
            File.WriteAllBytes(tempPath, bytes);
            File.Move(tempPath, path, overwrite: true);
        }
        finally
        {
            DeleteTempBestEffort(tempPath);
        }
    }

    public static async Task WriteAllBytesAsync(string path, byte[] bytes, CancellationToken ct = default)
    {
        string tempPath = CreateTempPath(path);
        try
        {
            await File.WriteAllBytesAsync(tempPath, bytes, ct);
            File.Move(tempPath, path, overwrite: true);
        }
        finally
        {
            DeleteTempBestEffort(tempPath);
        }
    }

    private static string CreateTempPath(string path)
    {
        string? directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        string fileName = Path.GetFileName(path);
        return Path.Combine(directory ?? ".", $".{fileName}.{Guid.NewGuid():N}.tmp");
    }

    private static void DeleteTempBestEffort(string tempPath)
    {
        try
        {
            if (File.Exists(tempPath))
            {
                File.Delete(tempPath);
            }
        }
        catch
        {
            // Cleanup is best effort; preserve the original persistence exception.
        }
    }
}
