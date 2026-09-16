using System.Text;

namespace NightOwl.Agent.Shared;

public static class NightOwlFileStore
{
    private static readonly UTF8Encoding Utf8NoBom = new(encoderShouldEmitUTF8Identifier: false);
    private const int DefaultMaxAttempts = 4;
    private static readonly TimeSpan DefaultInitialRetryDelay = TimeSpan.FromMilliseconds(100);

    public static void WriteAllText(string path, string content, Encoding? encoding = null)
    {
        WriteWithRetry(path, tempPath => File.WriteAllText(tempPath, content, encoding ?? Utf8NoBom));
    }

    public static async Task WriteAllTextAsync(string path, string content, Encoding? encoding = null, CancellationToken ct = default)
    {
        await WriteWithRetryAsync(path, tempPath => File.WriteAllTextAsync(tempPath, content, encoding ?? Utf8NoBom, ct), ct);
    }

    public static void WriteAllBytes(string path, byte[] bytes)
    {
        WriteWithRetry(path, tempPath => File.WriteAllBytes(tempPath, bytes));
    }

    public static async Task WriteAllBytesAsync(string path, byte[] bytes, CancellationToken ct = default)
    {
        await WriteWithRetryAsync(path, tempPath => File.WriteAllBytesAsync(tempPath, bytes, ct), ct);
    }

    internal static void WriteAllText(string path, string content, Encoding? encoding, NightOwlFileStoreTestHooks hooks)
    {
        WriteWithRetry(path, tempPath => File.WriteAllText(tempPath, content, encoding ?? Utf8NoBom), hooks);
    }

    internal static Task WriteAllTextAsync(string path, string content, Encoding? encoding, CancellationToken ct, NightOwlFileStoreTestHooks hooks)
    {
        return WriteWithRetryAsync(path, tempPath => File.WriteAllTextAsync(tempPath, content, encoding ?? Utf8NoBom, ct), ct, hooks);
    }

    private static void WriteWithRetry(string path, Action<string> writeTemp, NightOwlFileStoreTestHooks? hooks = null)
    {
        int maxAttempts = hooks?.MaxAttempts ?? DefaultMaxAttempts;
        TimeSpan delay = hooks?.InitialRetryDelay ?? DefaultInitialRetryDelay;
        for (int attempt = 1; ; attempt++)
        {
            string tempPath = CreateTempPath(path);
            try
            {
                writeTemp(tempPath);
                hooks?.BeforeMove?.Invoke(tempPath, path, attempt);
                File.Move(tempPath, path, overwrite: true);
                return;
            }
            catch (Exception ex) when (IsTransientFileStoreException(ex) && attempt < maxAttempts)
            {
                DeleteTempBestEffort(tempPath);
                hooks?.OnRetry?.Invoke(attempt, ex, delay);
                if (hooks?.Delay is not null)
                {
                    hooks.Delay(delay);
                }
                else
                {
                    Thread.Sleep(delay);
                }
                delay = NextDelay(delay);
            }
            finally
            {
                DeleteTempBestEffort(tempPath);
            }
        }
    }

    private static async Task WriteWithRetryAsync(string path, Func<string, Task> writeTemp, CancellationToken ct, NightOwlFileStoreTestHooks? hooks = null)
    {
        int maxAttempts = hooks?.MaxAttempts ?? DefaultMaxAttempts;
        TimeSpan delay = hooks?.InitialRetryDelay ?? DefaultInitialRetryDelay;
        for (int attempt = 1; ; attempt++)
        {
            ct.ThrowIfCancellationRequested();
            string tempPath = CreateTempPath(path);
            try
            {
                await writeTemp(tempPath);
                hooks?.BeforeMove?.Invoke(tempPath, path, attempt);
                File.Move(tempPath, path, overwrite: true);
                return;
            }
            catch (Exception ex) when (IsTransientFileStoreException(ex) && attempt < maxAttempts)
            {
                DeleteTempBestEffort(tempPath);
                hooks?.OnRetry?.Invoke(attempt, ex, delay);
                if (hooks?.DelayAsync is not null)
                {
                    await hooks.DelayAsync(delay, ct);
                }
                else
                {
                    await Task.Delay(delay, ct);
                }
                delay = NextDelay(delay);
            }
            finally
            {
                DeleteTempBestEffort(tempPath);
            }
        }
    }

    private static bool IsTransientFileStoreException(Exception ex)
    {
        return ex is IOException or UnauthorizedAccessException;
    }

    private static TimeSpan NextDelay(TimeSpan current)
    {
        double nextMilliseconds = Math.Min(current.TotalMilliseconds * 2, 1000);
        return TimeSpan.FromMilliseconds(nextMilliseconds);
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

internal sealed class NightOwlFileStoreTestHooks
{
    public int MaxAttempts { get; init; } = 4;
    public TimeSpan InitialRetryDelay { get; init; } = TimeSpan.Zero;
    public Action<string, string, int>? BeforeMove { get; init; }
    public Action<int, Exception, TimeSpan>? OnRetry { get; init; }
    public Action<TimeSpan>? Delay { get; init; }
    public Func<TimeSpan, CancellationToken, Task>? DelayAsync { get; init; }
}
