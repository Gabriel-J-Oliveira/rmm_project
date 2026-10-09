using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class TelemetryRuntime
{
    private readonly Func<AgentConfig, CancellationToken, Action, Task> _run;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private CancellationTokenSource? _stop;
    private Task _task = Task.CompletedTask;
    private CancellationToken _lifetime;
    private TelemetrySettings? _effective;
    public TelemetrySettings? Effective => _effective?.Enabled == true && _task.IsCompleted ? null : _effective;

    public TelemetryRuntime(TelemetryPipeline pipeline) : this(pipeline.RunAsync) { }
    internal TelemetryRuntime(Func<AgentConfig, CancellationToken, Action, Task> run) => _run = run;

    public async Task StartAsync(AgentConfig config, CancellationToken lifetime)
    {
        _lifetime = lifetime;
        await ReloadAsync(config, lifetime);
    }

    public async Task ReloadAsync(AgentConfig config, CancellationToken ct)
    {
        await _gate.WaitAsync(ct);
        try
        {
            _stop?.Cancel();
            // Never open a second buffer while the previous pipeline still owns it.
            try { await _task.WaitAsync(TimeSpan.FromSeconds(10), ct); }
            catch (Exception) when (_task.IsCompleted && !ct.IsCancellationRequested) { }
            _stop?.Dispose();
            _stop = null;
            _task = Task.CompletedTask;
            _effective = null;
            if (!config.TelemetryEnabled || !config.HasValidToken)
            {
                _effective = TelemetrySettings.FromConfig(config);
                return;
            }
            _stop = CancellationTokenSource.CreateLinkedTokenSource(_lifetime);
            TaskCompletionSource ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
            _task = _run(config, _stop.Token, () => ready.TrySetResult());
            Task winner = await Task.WhenAny(ready.Task, _task).WaitAsync(TimeSpan.FromSeconds(20), ct);
            if (winner == _task || _task.IsCompleted) throw new InvalidOperationException("Telemetry pipeline stopped before readiness.");
            await ready.Task;
            _effective = TelemetrySettings.FromConfig(config);
        }
        finally { _gate.Release(); }
    }

    public async Task StopAsync()
    {
        _stop?.Cancel();
        await _task.WaitAsync(TimeSpan.FromSeconds(3));
    }
}
