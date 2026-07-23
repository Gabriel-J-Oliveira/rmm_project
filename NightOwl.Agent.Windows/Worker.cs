using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;
using NightOwl.Agent.Windows.Jobs;
using System.Text.Json;
using System.Reflection;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Windows;

public sealed class Worker : BackgroundService
{
    private readonly ConfigService _configService;
    private readonly StateService _stateService;
    private readonly JsonlLogger _logger;
    private readonly AgentApiClient _api;
    private readonly WindowsInventoryCollector _collector;
    private readonly JobExecutor _jobExecutor;
    private readonly JobExecutionCoordinator _jobCoordinator;
    private readonly PendingResultQueue _resultQueue;

    public Worker(
        ConfigService configService,
        StateService stateService,
        JsonlLogger logger,
        AgentApiClient api,
        WindowsInventoryCollector collector,
        JobExecutor jobExecutor,
        JobExecutionCoordinator jobCoordinator,
        PendingResultQueue resultQueue)
    {
        _configService = configService;
        _stateService = stateService;
        _logger = logger;
        _api = api;
        _collector = collector;
        _jobExecutor = jobExecutor;
        _jobCoordinator = jobCoordinator;
        _resultQueue = resultQueue;
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
        await ConfirmPendingUpdateAsync(config, stoppingToken);
        await _jobCoordinator.RecoverInterruptedJobsAsync(config, _resultQueue, stoppingToken);
        await MigrateLegacyPendingResultsAsync(config, stoppingToken);

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

                await FlushPendingResultsAsync(config, stoppingToken);

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

    private async Task ConfirmPendingUpdateAsync(AgentConfig config, CancellationToken ct)
    {
        UpdateStateStore store = new(NightOwlPaths.Current.UpdateStatePath);
        if (!store.TryLoad(out UpdateState? state, out string error))
        {
            await _logger.LogAsync("update.state.invalid", "Update state file is invalid.", new { error_code = UpdateErrorCodes.UpdateStateInvalid, error }, ct, "error");
            return;
        }

        if (state is null || !state.IsActive)
        {
            return;
        }

        bool isRollbackHealthCheck = state.CurrentStage.Equals(UpdateStages.RollbackWaitingHealthCheck, StringComparison.OrdinalIgnoreCase);
        if (!state.CurrentStage.Equals(UpdateStages.WaitingHealthCheck, StringComparison.OrdinalIgnoreCase)
            && !state.CurrentStage.Equals(UpdateStages.ServiceStarted, StringComparison.OrdinalIgnoreCase)
            && !isRollbackHealthCheck)
        {
            return;
        }

        string runningVersion = GetRunningAgentVersion(config.AgentVersion);
        string expectedVersion = isRollbackHealthCheck ? state.FromVersion : state.TargetVersion;
        await _logger.LogAsync("update.healthcheck.started", "Checking pending update state after service start.", new
        {
            update_id = state.UpdateId,
            job_id = state.JobId,
            target_version = state.TargetVersion,
            expected_version = expectedVersion,
            rollback = isRollbackHealthCheck,
            running_version = runningVersion,
            machine_id = config.MachineId
        }, ct);

        if (!VersionsEqual(runningVersion, expectedVersion))
        {
            if (isRollbackHealthCheck)
            {
                state.MarkRollbackFailed(UpdateErrorCodes.RollbackVersionMismatch, $"Running version {runningVersion} does not match rollback target {state.FromVersion}.");
            }
            else
            {
                state.MarkRollbackRequired(UpdateStages.WaitingHealthCheck, UpdateErrorCodes.UpdateHealthcheckVersionMismatch, $"Running version {runningVersion} does not match target {state.TargetVersion}.");
            }
            store.Save(state);
            await _logger.LogAsync("update.healthcheck.failed", "Update target version mismatch.", new
            {
                update_id = state.UpdateId,
                job_id = state.JobId,
                error_code = state.ErrorCode,
                rollback_error_code = state.RollbackErrorCode,
                target_version = state.TargetVersion,
                expected_version = expectedVersion,
                running_version = runningVersion
            }, ct, "error");
            if (isRollbackHealthCheck)
            {
                WritePendingUpdateResult(config, state, "failed", 24, runningVersion, state.FromVersion, state.RollbackErrorMessage);
            }
            return;
        }

        if (string.IsNullOrWhiteSpace(config.MachineId))
        {
            state.MarkFailed(UpdateErrorCodes.UpdateStateInvalid, "Machine ID is empty after update.");
            store.Save(state);
            await _logger.LogAsync("update.healthcheck.failed", "Machine ID was not available after update.", new { update_id = state.UpdateId, job_id = state.JobId, error_code = state.ErrorCode }, ct, "error");
            WritePendingUpdateResult(config, state, "failed", 20, runningVersion, state.FromVersion, state.ErrorMessage);
            return;
        }

        state.ServiceStarted = true;
        if (isRollbackHealthCheck)
        {
            state.PreviousVersionConfirmed = true;
            state.HealthCheckConfirmed = false;
            state.MarkStage(UpdateStages.RolledBack, UpdateStatuses.Failed);
        }
        else
        {
            state.HealthCheckConfirmed = true;
            state.MarkStage(UpdateStages.Completed, UpdateStatuses.Completed);
        }
        store.Save(state);
        await _logger.LogAsync(isRollbackHealthCheck ? "rollback.healthcheck.confirmed" : "update.healthcheck.confirmed", isRollbackHealthCheck ? "Rollback completed after agent health check." : "Update completed after agent health check.", new
        {
            update_id = state.UpdateId,
            job_id = state.JobId,
            from_version = state.FromVersion,
            target_version = state.TargetVersion,
            running_version = runningVersion,
            machine_id = config.MachineId
        }, ct);
        WritePendingUpdateResult(config, state, isRollbackHealthCheck ? "rolled_back" : "completed", isRollbackHealthCheck ? 23 : 0, runningVersion, state.FromVersion, isRollbackHealthCheck ? "Agent rollback confirmed." : "Agent updated successfully.");
    }

    private static void WritePendingUpdateResult(AgentConfig config, UpdateState state, string status, int exitCode, string installedVersion, string previousVersion, string message)
    {
        if (string.IsNullOrWhiteSpace(state.JobId))
        {
            return;
        }

        string pendingDir = string.IsNullOrWhiteSpace(config.PendingResultsPath)
            ? NightOwlPaths.Current.PendingResultsDir
            : config.PendingResultsPath;
        Directory.CreateDirectory(pendingDir);
        JobExecutionResult payload = new()
        {
            JobId = state.JobId,
            Status = status,
            StartedAt = state.StartedAt,
            FinishedAt = DateTimeOffset.UtcNow,
            DurationSeconds = Math.Round((DateTimeOffset.UtcNow - state.StartedAt).TotalSeconds, 3),
            ExitCode = exitCode,
            Stdout = message,
            Stderr = "",
            ErrorMessage = status == "failed" ? message : "",
            Result = new
            {
                type = "update_agent",
                update_id = state.UpdateId,
                update_status = status == "rolled_back" ? "rolled_back" : status == "completed" ? "success" : "failed",
                installed_version = installedVersion,
                previous_version = previousVersion,
                from_version = state.FromVersion,
                attempted_version = state.TargetVersion,
                active_version = installedVersion,
                target_version = state.TargetVersion,
                failure_stage = state.RollbackReason,
                original_error_code = state.ErrorCode,
                rollback_duration = state.RollbackStartedAt is null ? null : (double?)Math.Round((DateTimeOffset.UtcNow - state.RollbackStartedAt.Value).TotalSeconds, 3),
                rollback_confirmed = status == "rolled_back",
                error_code = state.ErrorCode,
                message,
                completed_at = DateTimeOffset.UtcNow
            }
        };
        string path = Path.Combine(pendingDir, $"job-result-{state.JobId}.json");
        File.WriteAllText(path, JsonSerializer.Serialize(payload, new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true }));
    }

    private static string GetRunningAgentVersion(string fallback)
    {
        try
        {
            string? informational = typeof(Worker).Assembly
                .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?
                .InformationalVersion;
            string version = (informational ?? typeof(Worker).Assembly.GetName().Version?.ToString() ?? "").Split('+')[0];
            return string.IsNullOrWhiteSpace(version) ? fallback : version;
        }
        catch
        {
            return fallback;
        }
    }

    private static bool VersionsEqual(string left, string right)
    {
        return string.Equals((left ?? "").Trim(), (right ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
    }

    private async Task MigrateLegacyPendingResultsAsync(AgentConfig config, CancellationToken ct)
    {
        List<string> pendingFiles = new();
        string pendingDir = string.IsNullOrWhiteSpace(config.PendingResultsPath)
            ? NightOwlPaths.Current.PendingResultsDir
            : config.PendingResultsPath;
        if (Directory.Exists(pendingDir))
        {
            pendingFiles.AddRange(Directory.GetFiles(pendingDir, "*.json", SearchOption.TopDirectoryOnly)
                .Where(path => !Path.GetFileName(path).StartsWith(".", StringComparison.OrdinalIgnoreCase)));
        }

        string legacyPendingPath = Path.Combine(config.JobsPath, "pending-update-result.json");
        if (File.Exists(legacyPendingPath))
        {
            pendingFiles.Add(legacyPendingPath);
        }

        string legacyPendingDir = Path.Combine(config.JobsPath, "Pending");
        if (Directory.Exists(legacyPendingDir))
        {
            pendingFiles.AddRange(Directory.GetFiles(legacyPendingDir, "*.json", SearchOption.TopDirectoryOnly));
        }

        foreach (string pendingPath in pendingFiles.Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(path => path))
        {
            await _logger.LogAsync("job.result.legacy_pending_found", "Legacy pending job result found.", new { pendingPath }, ct);
            try
            {
                string json = await File.ReadAllTextAsync(pendingPath, ct);
                using JsonDocument document = JsonDocument.Parse(json);
                if (document.RootElement.ValueKind == JsonValueKind.Object
                    && document.RootElement.TryGetProperty("result_id", out _)
                    && document.RootElement.TryGetProperty("payload", out _))
                {
                    continue;
                }
                JobExecutionResult result = JsonSerializer.Deserialize<JobExecutionResult>(json, new JsonSerializerOptions(JsonSerializerDefaults.Web))
                    ?? throw new InvalidOperationException("Pending job result is invalid.");
                string jobType = InferJobType(result);
                PendingResultRecord queued = _resultQueue.Enqueue(jobType, result, JobExecutionCoordinator.IsCritical(jobType));
                string migratedDir = Path.Combine(pendingDir, "migrated");
                Directory.CreateDirectory(migratedDir);
                string migratedPath = Path.Combine(migratedDir, $"{Path.GetFileNameWithoutExtension(pendingPath)}-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}.json");
                File.Move(pendingPath, migratedPath, overwrite: true);
                await _logger.LogAsync("job.result.legacy_migrated", "Legacy pending job result migrated into persistent queue.", new { result.JobId, jobType, result_id = queued.ResultId, migratedPath }, ct);
                if (Path.GetFileName(pendingPath).Equals("pending-update-result.json", StringComparison.OrdinalIgnoreCase))
                {
                    await _logger.LogAsync("update.result.pending_migrated", "Legacy pending update result migrated into persistent queue.", new { result.JobId, result_id = queued.ResultId, migratedPath }, ct);
                }
            }
            catch (Exception ex)
            {
                await _logger.LogAsync("job.result.legacy_migration_failed", ex.Message, BuildErrorData(ex, new { pendingPath }), ct, "error");
            }
        }
    }

    private async Task FlushPendingResultsAsync(AgentConfig config, CancellationToken ct)
    {
        foreach (PendingResultRecord pending in _resultQueue.ListDue(DateTimeOffset.UtcNow))
        {
            try
            {
                JobExecutionResult result = pending.Payload.Deserialize<JobExecutionResult>(new JsonSerializerOptions(JsonSerializerDefaults.Web))
                    ?? throw new InvalidOperationException(JobErrorCodes.ResultPayloadInvalid);
                await _api.SendJobResultAsync(config, result, ct, pending.ResultId);
                _resultQueue.MarkSent(pending);
                await _logger.LogAsync("job.result.sent", "Queued job result sent.", new
                {
                    pending.JobId,
                    pending.JobType,
                    result_id = pending.ResultId,
                    pending.Status,
                    attempt_count = pending.AttemptCount,
                    idempotency_key = pending.ResultId
                }, ct);
            }
            catch (Exception ex)
            {
                _resultQueue.MarkAttemptFailed(pending, JobErrorCodes.ResultSendFailed, ex.Message);
                await _logger.LogAsync("job.result.send_failed", ex.Message, BuildErrorData(ex, new
                {
                    pending.JobId,
                    pending.JobType,
                    result_id = pending.ResultId,
                    attempt_count = pending.AttemptCount + 1,
                    next_attempt_at = pending.NextAttemptAt
                }), ct, "error");
            }
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

        IEnumerable<AgentJobRequest> orderedJobs = response.Jobs
            .OrderBy(job => JobExecutionCoordinator.GetCategory(job.Type).Equals(JobCategories.Exclusive, StringComparison.OrdinalIgnoreCase) ? 0 : 1)
            .ThenByDescending(job => job.Priority)
            .ThenBy(job => job.CreatedAt ?? DateTimeOffset.UtcNow);

        foreach (AgentJobRequest job in orderedJobs)
        {
            await _logger.LogAsync("job.received", "Job received.", new { job.Id, job.Type }, ct);
            JobExecutionResult result;
            bool started = false;
            JobStartDecision startDecision = await _jobCoordinator.TryStartAsync(config, job, ct);
            if (!startDecision.CanStart)
            {
                result = startDecision.Result!;
            }
            else
            {
                started = true;
                try
                {
                    result = await _jobExecutor.ExecuteAsync(config, job, ct);
                }
                finally
                {
                    _jobCoordinator.Release(job);
                }
            }

            try
            {
                string jobType = InferJobType(result, job.Type);
                PendingResultRecord queued = _resultQueue.Enqueue(jobType, result, JobExecutionCoordinator.IsCritical(jobType));
                await _logger.LogAsync("job.result.queued", "Job result persisted before send.", new
                {
                    job.Id,
                    job.Type,
                    result.Status,
                    result_id = queued.ResultId,
                    category = JobExecutionCoordinator.GetCategory(jobType),
                    critical = queued.Critical
                }, ct);
                await FlushPendingResultsAsync(config, ct);
                state.RememberJob(job.Id);
            }
            catch (Exception ex)
            {
                await _logger.LogAsync("job.result.queue_failed", ex.Message, BuildErrorData(ex, new { job.Id, job.Type, started }), ct, "error");
            }
        }
    }

    private static string InferJobType(JobExecutionResult result, string fallback = "")
    {
        if (result.Result is JsonElement element)
        {
            string? type = TryGetResultType(element);
            if (!string.IsNullOrWhiteSpace(type))
            {
                return type;
            }
        }
        else if (result.Result is not null)
        {
            JsonElement serialized = JsonSerializer.SerializeToElement(result.Result, new JsonSerializerOptions(JsonSerializerDefaults.Web));
            string? type = TryGetResultType(serialized);
            if (!string.IsNullOrWhiteSpace(type))
            {
                return type;
            }
        }
        return string.IsNullOrWhiteSpace(fallback) ? "unknown" : fallback;
    }

    private static string? TryGetResultType(JsonElement element)
    {
        return element.ValueKind == JsonValueKind.Object && element.TryGetProperty("type", out JsonElement typeElement)
            ? typeElement.GetString()
            : null;
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
