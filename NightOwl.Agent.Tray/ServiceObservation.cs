namespace NightOwl.Agent.Tray;

internal sealed class ServiceObservation
{
    private string? _previous;
    private TimeSpan? _unavailableSince;
    internal static readonly TimeSpan Grace = TimeSpan.FromMinutes(2);

    internal (bool Exit, bool Changed) Observe(string? status, TimeSpan now)
    {
        bool changed = !string.Equals(_previous, status, StringComparison.Ordinal);
        _previous = status;
        if (status is null) return (true, changed);
        if (status == "Running") { _unavailableSince = null; return (false, changed); }
        _unavailableSince ??= now;
        return (now - _unavailableSince.Value >= Grace, changed);
    }
}
