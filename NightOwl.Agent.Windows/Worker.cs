using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;
using NightOwl.Agent.Windows.Jobs;
using System.Text.Json;

namespace NightOwl.Agent.Windows;

public sealed class Worker : BackgroundService
{
    private readonly ConfigService _configService;
    private readonly StateService _stateService;
    private readonly JsonlLogger _logger;
    private readonly AgentApiClient _api;
    private readonly WindowsInventoryCollector _collector;
    private readonly JobExecutor _jobExecutor;

    public Worker(
        ConfigService configService,
        StateService stateService,
        JsonlLogger logger,
        AgentApiClient api,
        WindowsInventoryCollector collector,
        JobExecutor jobExecutor)
    {
        _configService = configService;
        _stateService = stateService;
        _logger = logger;
        _api = api;
        _collector = collector;
        _jobExecutor = jobExecutor;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        AgentConfig config = _configService.Load();
        AgentState state = _stateService.Load(config);
        await _logger.LogAsync("config.loaded", "Agent config loaded.", new { config.AgentVersion, config.ServerBaseUrl }, stoppingToken);
        await _logger.LogAsync("config.normalized", "Agent URLs normalized.", new
        {
            config.HeartbeatUrl,
            config.CollectUrl,
            config.JobsPullUrl,
            config.JobsResultUrl,
            config.InstallPath
        }, stoppingToken);
        await _logger.LogAsync("machine_id.resolved", "Machine ID resolved.", new
        {
            machine_id = config.MachineId,
            source = config.MachineIdSource
        }, stoppingToken);
        await _logger.LogAsync("service.starting", "NightOwl .NET agent starting.", new { config.AgentVersion }, stoppingToken);

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                if (!config.HasValidToken)
                {
                    await _logger.LogAsync(
                        "config.invalid_missing_token",
                        "Agent token is missing or still contains a placeholder. API calls are paused.",
                        new { config.ServerBaseUrl, config.MachineId },
                        stoppingToken,
                        "error");
                    await Task.Delay(TimeSpan.FromSeconds(60), stoppingToken);
                    continue;
                }

                DateTimeOffset now = DateTimeOffset.UtcNow;

                await SendPendingUpdateResultAsync(config, stoppingToken);

                if (IsDue(state.LastHeartbeatAt, config.Intervals.HeartbeatSeconds, now))
                {
                    await SendHeartbeatAsync(config, state, now, stoppingToken);
                }

                if (IsDue(state.LastCollectionAt, config.Intervals.CollectSeconds, now))
                {
                    await SendCollectionAsync(config, state, now, stoppingToken);
                }

                if (IsDue(state.LastJobPullAt, config.Intervals.JobsSeconds, now))
                {
                    await PullAndRunJobsAsync(config, state, now, stoppingToken);
                }
        }
        catch (Exception ex)
        {
            await _logger.LogAsync("service.loop.failed", ex.Message, BuildErrorData(ex), stoppingToken, "error");
        }

            await _stateService.SaveAsync(config, state, stoppingToken);
            await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
        }

        await _logger.LogAsync("service.stopping", "NightOwl .NET agent stopping.", null, CancellationToken.None);
    }

    private async Task SendPendingUpdateResultAsync(AgentConfig config, CancellationToken ct)
    {
        string pendingPath = Path.Combine(config.JobsPath, "pending-update-result.json");
        if (!File.Exists(pendingPath))
        {
            return;
        }

        await _logger.LogAsync("update.result.pending_found", "Pending update result found.", new { pendingPath }, ct);
        try
        {
            string json = await File.ReadAllTextAsync(pendingPath, ct);
            JobExecutionResult result = JsonSerializer.Deserialize<JobExecutionResult>(json, new JsonSerializerOptions(JsonSerializerDefaults.Web))
                ?? throw new InvalidOperationException("Pending update result is invalid.");
            await _api.SendJobResultAsync(config, result, ct);
            string completedDir = Path.Combine(config.JobsPath, "completed");
            Directory.CreateDirectory(completedDir);
            string completedPath = Path.Combine(completedDir, $"pending-update-result-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}.json");
            File.Move(pendingPath, completedPath, overwrite: true);
            await _logger.LogAsync("update.result.sent", "Pending update result sent.", new { result.JobId, completedPath }, ct);
        }
        catch (Exception ex)
        {
            await _logger.LogAsync("update.result.send_failed", ex.Message, BuildErrorData(ex, new { pendingPath }), ct, "error");
        }
    }

    private static bool IsDue(DateTimeOffset? previous, int intervalSeconds, DateTimeOffset now)
    {
        if (previous is null)
        {
            return true;
        }

        int interval = Math.Max(intervalSeconds, 5);
        return now - previous.Value >= TimeSpan.FromSeconds(interval);
    }

    private async Task SendHeartbeatAsync(AgentConfig config, AgentState state, DateTimeOffset now, CancellationToken ct)
    {
        AgentHeartbeatPayload payload = _collector.BuildHeartbeat(config);
        try
        {
            await _api.PostHeartbeatAsync(config, payload, ct);
            state.LastHeartbeatAt = now;
            await _logger.LogAsync("heartbeat.sent", "Heartbeat sent.", new { payload.Hostname, payload.MachineId }, ct);
        }
        catch (Exception ex)
        {
            await _logger.LogAsync("heartbeat.failed", ex.Message, BuildErrorData(ex), ct, "error");
        }
    }

    private async Task SendCollectionAsync(AgentConfig config, AgentState state, DateTimeOffset now, CancellationToken ct)
    {
        await _logger.LogAsync("collection.started", "Aggregated collection started.", null, ct);
        try
        {
            AgentCollectPayload payload = _collector.BuildCollectPayload(config);
            await _api.PostCollectionAsync(config, payload, ct);
            state.LastCollectionAt = now;
            await _logger.LogAsync("collection.sent", "Aggregated collection sent.", new
            {
                disks = payload.Disks.Count,
                software = payload.Software.Count
            }, ct);
        }
        catch (Exception ex)
        {
            await _logger.LogAsync("collection.failed", ex.Message, BuildErrorData(ex), ct, "error");
        }
    }

    private async Task PullAndRunJobsAsync(AgentConfig config, AgentState state, DateTimeOffset now, CancellationToken ct)
    {
        state.LastJobPullAt = now;
        await _logger.LogAsync("job.pull.started", "Pulling jobs.", null, ct);

        AgentJobsPullResponse response;
        try
        {
            response = await _api.PullJobsAsync(config, ct);
            await _logger.LogAsync("job.pull.response", "Job pull completed.", new { count = response.Jobs.Count }, ct);
        }
        catch (Exception ex)
        {
            await _logger.LogAsync("job.pull.failed", ex.Message, BuildErrorData(ex), ct, "error");
            return;
        }

        foreach (AgentJobRequest job in response.Jobs)
        {
            if (state.RecentJobIds.Contains(job.Id))
            {
                await _logger.LogAsync("job.skipped_duplicate", "Job already executed locally.", new { job.Id, job.Type }, ct, "warning");
                continue;
            }

            await _logger.LogAsync("job.received", "Job received.", new { job.Id, job.Type }, ct);
            JobExecutionResult result = await _jobExecutor.ExecuteAsync(config, job, ct);

            try
            {
                await _api.SendJobResultAsync(config, result, ct);
                await _logger.LogAsync("job.result.sent", "Job result sent.", new { job.Id, result.Status }, ct);
                state.RememberJob(job.Id);
            }
            catch (Exception ex)
            {
                await _logger.LogAsync("job.result.failed", ex.Message, BuildErrorData(ex, new { job.Id }), ct, "error");
            }
        }
    }

    private static object BuildErrorData(Exception ex, object? context = null)
    {
        if (ex is AgentApiException api)
        {
            return new
            {
                status_code = api.StatusCodeValue,
                reason = api.ReasonPhrase,
                response_body = api.ResponseBody,
                url = api.Url,
                method = api.Method,
                operation = api.Operation,
                context,
                exception = api.ToString()
            };
        }

        return new
        {
            context,
            exception = ex.ToString()
        };
    }
}
