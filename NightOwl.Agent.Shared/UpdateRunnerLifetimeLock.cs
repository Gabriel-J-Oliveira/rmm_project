using System.Security.Cryptography;
using System.Text;

namespace NightOwl.Agent.Shared;

public sealed class UpdateRunnerLifetimeLock : IDisposable
{
    private readonly Mutex _mutex;
    private bool _acquired;

    private UpdateRunnerLifetimeLock(Mutex mutex, bool acquired)
    {
        _mutex = mutex;
        _acquired = acquired;
    }

    public bool Acquired => _acquired;

    public static UpdateRunnerLifetimeLock TryAcquire(string updateId)
    {
        if (string.IsNullOrWhiteSpace(updateId))
        {
            throw new ArgumentException("Runner requires update_id.", nameof(updateId));
        }

        string hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(updateId.ToUpperInvariant())));
        string name = "NightOwl.Agent.UpdateRunner." + hash;
        Mutex mutex = new(false, OperatingSystem.IsWindows() ? @"Global\" + name : name);

        bool acquired;
        try
        {
            acquired = mutex.WaitOne(TimeSpan.Zero);
        }
        catch (AbandonedMutexException)
        {
            acquired = true;
        }
        return new UpdateRunnerLifetimeLock(mutex, acquired);
    }

    public void Dispose()
    {
        if (_acquired)
        {
            _mutex.ReleaseMutex();
            _acquired = false;
        }
        _mutex.Dispose();
    }
}
