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
    private static readonly TimeSpan DefaultLoopDelay = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan InitialStateSaveBackoff = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan MaxStateSaveBackoff = TimeSpan.FromSeconds(60);
    private readonly StateService _stateService;
    private readonly JsonlLogger _logger;
    private readonly AgentApiClient _api;
    private readonly WindowsInventoryCollector _collector;
    private readonly JobExecutor _jobExecutor;
    private readonly JobExecutionCoordinator _jobCoordinator;
    private readonly PendingResultQueue _resultQueue;
    private readonly TelemetryPipeline _telemetry;
    private readonly TelemetryRuntime _telemetryRuntime;
    private readonly TelemetryConfigurationService? _telemetryConfiguration;
    private readonly Func<AgentConfig> _loadConfig;
    private readonly string _updateStatePath;
    private long _latestHeartbeatTicks;
    private long _latestJobPullTicks;
    private readonly SemaphoreSlim _heavyWork = new(1, 1);

    public Worker(
        ConfigService configService,
        StateService stateService,
        JsonlLogger logger,
        AgentApiClient api,
        WindowsInventoryCollector collector,
        JobExecutor jobExecutor,
        JobExecutionCoordinator jobCoordinator,
        PendingResultQueue resultQueue,
        TelemetryPipeline telemetry,
        TelemetryRuntime? telemetryRuntime = null,
        TelemetryConfigurationService? telemetryConfiguration = null)
    {
        _stateService = stateService;
        _logger = logger;
        _api = api;
        _collector = collector;
        _jobExecutor = jobExecutor;
        _jobCoordinator = jobCoordinator;
        _resultQueue = resultQueue;
        _telemetry = telemetry;
        _telemetryRuntime = telemetryRuntime ?? new TelemetryRuntime(telemetry);
        _telemetryConfiguration = telemetryConfiguration;
        _loadConfig = configService.Load;
        _updateStatePath = NightOwlPaths.Current.UpdateStatePath;
    }

    internal Worker(ConfigService configService, StateService stateService, JsonlLogger logger,
        AgentApiClient api, WindowsInventoryCollector collector, JobExecutor jobExecutor,
        JobExecutionCoordinator jobCoordinator, PendingResultQueue resultQueue, TelemetryPipeline telemetry,
        Func<AgentConfig> loadConfig, string updateStatePath)
        : this(configService, stateService, logger, api, collector, jobExecutor, jobCoordinator, resultQueue, telemetry)
    {
        _loadConfig = loadConfig;
        _updateStatePath = updateStatePath;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await Task.Yield();
        AgentConfig config = _loadConfig();
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
        await MigrateLegacyPendingResultsAsync(config, stoppingToken);
        using CancellationTokenSource handshakeCancellation = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        Task updateHandshakeTask = RunPendingUpdateHandshakeAsync(config, handshakeCancellation.Token);
        DateTimeOffset? initialHeartbeat = state.LastHeartbeatAt;
        Task heartbeatTask = Task.Run(() => RunHeartbeatLoopAsync(config, initialHeartbeat,
            handshakeCancellation.Token), CancellationToken.None);
        Task jobsTask = Task.CompletedTask;
        try
        {
            try
            {
                await _telemetryRuntime.StartAsync(config, stoppingToken);
                if (_telemetryConfiguration is not null)
                    await _telemetryConfiguration.RecoverAsync(config, stoppingToken);
            }
            catch (Exception) when (!stoppingToken.IsCancellationRequested)
            {
                await _logger.LogAsync("telemetry.startup.not_ready", "Telemetry initialization/recovery pending.", null, stoppingToken, "warning");
            }
            await _jobCoordinator.RecoverInterruptedJobsAsync(config, _resultQueue, stoppingToken, _updateStatePath);
            JobRuntime jobs = new(_api, _jobCoordinator, _jobExecutor.ExecuteAsync, _resultQueue, _logger, _heavyWork,
                token => MigrateLegacyPendingResultsAsync(config, token));
            jobs.PollConfirmed = at => Interlocked.Exchange(ref _latestJobPullTicks,at.UtcTicks);
            jobsTask = Task.Run(() => jobs.RunAsync(config, handshakeCancellation.Token), CancellationToken.None);
            TimeSpan stateSaveBackoff = InitialStateSaveBackoff;

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

                    if (_telemetryConfiguration?.RecoveryPending == true)
                    {
                        try
                        {
                            await _telemetryConfiguration.RecoverAsync(config, stoppingToken);
                            await _jobCoordinator.RecoverInterruptedJobsAsync(config, _resultQueue, stoppingToken, _updateStatePath);
                        }
                        catch (Exception) when (!stoppingToken.IsCancellationRequested)
                        {
                            await _logger.LogAsync("telemetry.recovery.pending", "Telemetry recovery not yet confirmed.", null, stoppingToken, "warning");
                        }
                    }

                    if (InventoryPlan.Due(state, now, config.Intervals.CollectSeconds).Any &&
                        IsDue(state.LastCollectionAt, Math.Min(300, Math.Max(5, config.Intervals.CollectSeconds)), now) &&
                        !_jobCoordinator.HasExternalOwner && await _heavyWork.WaitAsync(0, stoppingToken))
                    {
                        bool ownsCollection = true;
                        try { await SendCollectionAsync(config, state, now, stoppingToken,
                            () => { _heavyWork.Release(); ownsCollection = false; }); }
                        finally { if (ownsCollection) _heavyWork.Release(); }
                    }
                }
                catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
                catch (Exception ex)
                {
                    await _logger.LogAsync("service.loop.failed", ex.Message, BuildErrorData(ex), stoppingToken, "error");
                }

                CopyHeartbeatCheckpoint(state);
                StateSaveOutcome saveOutcome = await SaveStateWithBoundaryAsync(
                    token => _stateService.SaveAsync(config, state, token),
                    _logger,
                    stateSaveBackoff,
                    stoppingToken);
                stateSaveBackoff = saveOutcome.NextBackoff;
                await Task.Delay(saveOutcome.LoopDelay, stoppingToken);
            }
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
        finally
        {
            handshakeCancellation.Cancel();
            await heartbeatTask;
            try { await jobsTask; } catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
            CopyHeartbeatCheckpoint(state);
            await updateHandshakeTask;
            try { await _telemetryRuntime.StopAsync(); }
            catch (TimeoutException)
            {
                try
                {
                    await _logger.LogAsync("telemetry.shutdown.deferred", "Telemetry did not finish within shutdown grace.",
                        new { grace_seconds = 3 }, CancellationToken.None, "warning");
                }
                catch (Exception) { /* Shutdown must not be blocked by diagnostics. */ }
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
            try { await _logger.LogAsync("service.stopping", "NightOwl .NET agent stopping.", null, CancellationToken.None); }
            catch (Exception) { /* Shutdown must not be blocked by diagnostics. */ }
        }
    }

    private void CopyHeartbeatCheckpoint(AgentState state)
    {
        long ticks = Interlocked.Read(ref _latestHeartbeatTicks);
        if (ticks > 0) state.LastHeartbeatAt = new DateTimeOffset(ticks, TimeSpan.Zero);
        long jobTicks = Interlocked.Read(ref _latestJobPullTicks);
        if (jobTicks > 0) state.LastJobPullAt = new DateTimeOffset(jobTicks,TimeSpan.Zero);
    }

    internal async Task RunHeartbeatLoopAsync(AgentConfig config, DateTimeOffset? lastHeartbeat, CancellationToken ct)
    {
        try
        {
            while (!ct.IsCancellationRequested)
            {
                DateTimeOffset now = DateTimeOffset.UtcNow;
                if (config.HasValidToken && IsDue(lastHeartbeat, config.Intervals.HeartbeatSeconds, now))
                {
                    using CancellationTokenSource request = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    request.CancelAfter(TimeSpan.FromSeconds(30));
                    try
                    {
                        if (await SendHeartbeatAsync(config, request.Token))
                        {
                            lastHeartbeat = now;
                            Interlocked.Exchange(ref _latestHeartbeatTicks, now.UtcTicks);
                        }
                    }
                    catch (Exception) when (!ct.IsCancellationRequested)
                    {
                        // Collection/logging failures cannot terminate the heartbeat scheduler.
                    }
                }
                await Task.Delay(DefaultLoopDelay, ct);
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { }
    }

    private async Task RunPendingUpdateHandshakeAsync(AgentConfig config, CancellationToken ct)
    {
        try
        {
            (string JobId, string UpdateId)? target = await ConfirmPendingUpdateAsync(config, ct);
            if (target is { } identity && !string.IsNullOrWhiteSpace(identity.JobId)
                && !string.IsNullOrWhiteSpace(identity.UpdateId))
            {
                await MigrateLegacyPendingResultsAsync(config, ct, identity.JobId, identity.UpdateId);
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { }
        catch (Exception ex)
        {
            await _logger.LogAsync("update.healthcheck.skipped", "Pending update confirmation could not finish.",
                new { reason = "unexpected_error", exception_type = ex.GetType().Name }, CancellationToken.None, "error");
        }
    }

    private async Task<(string JobId, string UpdateId)?> ConfirmPendingUpdateAsync(AgentConfig config, CancellationToken ct)
    {
        UpdateStateStore store = new(_updateStatePath);
        (bool readable, UpdateState? state) = await UpdateHealthCheckHandshake.ReadInitialAsync(
            () => (store.TryLoad(out UpdateState? loaded, out _), loaded), Task.Delay, ct);
        if (!readable)
        {
            await _logger.LogAsync("update.state.invalid", "Update state could not be read after bounded retries.",
                new { error_code = UpdateErrorCodes.UpdateStateInvalid }, ct, "error");
            return null;
        }

        if (state is null)
        {
            return null;
        }

        await _logger.LogAsync("update.healthcheck.pending_found", "Pending update state found at agent startup.",
            new { update_id = state.UpdateId, job_id = state.JobId, stage = state.CurrentStage }, ct);
        if (!state.IsActive)
        {
            await _logger.LogAsync("update.healthcheck.skipped", "Update state is already terminal.",
                new { update_id = state.UpdateId, job_id = state.JobId, reason = "terminal_state" }, ct);
            return null;
        }

        UpdateState initial = state;
        bool isRollbackHealthCheck = state.CurrentStage.Equals(UpdateStages.RollbackWaitingHealthCheck, StringComparison.OrdinalIgnoreCase);
        if (state.CurrentStage.Equals(UpdateStages.StartingService, StringComparison.OrdinalIgnoreCase)
            || state.CurrentStage.Equals(UpdateStages.ServiceStarted, StringComparison.OrdinalIgnoreCase))
        {
            await _logger.LogAsync("update.healthcheck.waiting_handshake", "Waiting for updater healthcheck stage.",
                new { update_id = state.UpdateId, job_id = state.JobId, stage = state.CurrentStage }, ct);
        }
        HandshakeResult handshake = await UpdateHealthCheckHandshake.WaitAsync(initial,
            () => (store.TryLoad(out UpdateState? loaded, out _), loaded), Task.Delay, ct);
        if (handshake.Status != HandshakeStatus.Ready)
        {
            await _logger.LogAsync("update.healthcheck.skipped",
                "Update healthcheck confirmation was not attempted.",
                new { update_id = initial.UpdateId, job_id = initial.JobId, reason = handshake.Reason,
                    read_retries = handshake.ReadRetries }, ct,
                handshake.Status == HandshakeStatus.ReadFailed ? "warning" : "info");
            return null;
        }
        state = handshake.State!;
        if (handshake.Waited)
        {
            await _logger.LogAsync("update.healthcheck.waiting_observed", "Updater healthcheck stage observed.",
                new { update_id = state.UpdateId, job_id = state.JobId, stage = state.CurrentStage,
                    read_retries = handshake.ReadRetries }, ct);
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
            bool persisted = store.TryTransitionNonTerminal(state.UpdateId, current =>
            {
                if (!UpdateHealthCheckHandshake.CanConfirm(current, initial, isRollbackHealthCheck)) return false;
                if (isRollbackHealthCheck)
                    current.MarkRollbackFailed(UpdateErrorCodes.RollbackVersionMismatch, $"Running version {runningVersion} does not match rollback target {current.FromVersion}.");
                else
                    current.MarkRollbackRequired(UpdateStages.WaitingHealthCheck, UpdateErrorCodes.UpdateHealthcheckVersionMismatch, $"Running version {runningVersion} does not match target {current.TargetVersion}.");
                return true;
            }, out UpdateState? currentState);
            if (!persisted)
            {
                return null;
            }
            state = currentState!;
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
                return (state.JobId, state.UpdateId);
            }
            return null;
        }

        if (string.IsNullOrWhiteSpace(config.MachineId))
        {
            bool persisted = store.TryTransitionNonTerminal(state.UpdateId, current =>
            {
                if (!UpdateHealthCheckHandshake.CanConfirm(current, initial, isRollbackHealthCheck)) return false;
                current.MarkFailed(UpdateErrorCodes.UpdateStateInvalid, "Machine ID is empty after update.");
                return true;
            }, out UpdateState? currentState);
            if (!persisted) return null;
            state = currentState!;
            await _logger.LogAsync("update.healthcheck.failed", "Machine ID was not available after update.", new { update_id = state.UpdateId, job_id = state.JobId, error_code = state.ErrorCode }, ct, "error");
            WritePendingUpdateResult(config, state, "failed", 20, runningVersion, state.FromVersion, state.ErrorMessage);
            return (state.JobId, state.UpdateId);
        }

        bool confirmed = UpdateHealthCheckHandshake.TryConfirm(store, initial, isRollbackHealthCheck,
            out UpdateState? confirmedState);
        if (!confirmed)
        {
            await _logger.LogAsync("update.healthcheck.skipped", "Update state changed before confirmation.",
                new { update_id = initial.UpdateId, job_id = initial.JobId, reason = "state_changed_before_persist" }, ct);
            return null;
        }
        state = confirmedState!;
        await _logger.LogAsync("update.healthcheck.persisted", "Healthcheck terminal state persisted.",
            new { update_id = state.UpdateId, job_id = state.JobId, stage = state.CurrentStage }, ct);
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
        return (state.JobId, state.UpdateId);
    }

    private static void WritePendingUpdateResult(AgentConfig config, UpdateState state, string status, int exitCode, string installedVersion, string previousVersion, string message)
    {
        if (string.IsNullOrWhiteSpace(state.JobId))
        {
            return;
        }

        string originalErrorMessage = SanitizeUpdateResultMessage(state.ErrorMessage);
        string rollbackErrorMessage = SanitizeUpdateResultMessage(state.RollbackErrorMessage);
        string effectiveErrorMessage = status == "failed"
            ? SanitizeUpdateResultMessage(message)
            : status == "rolled_back"
                ? originalErrorMessage
                : "";

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
            ErrorMessage = effectiveErrorMessage,
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
                updated = status == "completed" && exitCode == 0,
                rollback_performed = status == "rolled_back" || state.RollbackAttempt > 0,
                health_check = new
                {
                    confirmed = state.HealthCheckConfirmed,
                    service_started = state.ServiceStarted,
                    stage = state.CurrentStage,
                    machine_id_preserved = !string.IsNullOrWhiteSpace(config.MachineId)
                },
                exit_code = exitCode,
                failure_stage = state.RollbackReason,
                rollback_duration = state.RollbackStartedAt is null ? null : (double?)Math.Round((DateTimeOffset.UtcNow - state.RollbackStartedAt.Value).TotalSeconds, 3),
                rollback_confirmed = status == "rolled_back",
                error_code = state.ErrorCode,
                error_message = effectiveErrorMessage,
                original_error_code = state.ErrorCode,
                original_error_message = originalErrorMessage,
                rollback_error_code = state.RollbackErrorCode,
                rollback_error_message = rollbackErrorMessage,
                message,
                completed_at = DateTimeOffset.UtcNow
            }
        };
        string path = Path.Combine(pendingDir, $"job-result-{state.JobId}.json");
        File.WriteAllText(path, JsonSerializer.Serialize(payload, new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true }));
    }

    private static string SanitizeUpdateResultMessage(string value)
    {
        string sanitized = (value ?? "").Replace("\r", " ").Replace("\n", " ");
        return sanitized.Length <= 1000 ? sanitized : sanitized[..1000];
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

    private async Task MigrateLegacyPendingResultsAsync(AgentConfig config, CancellationToken ct,
        string? finalizeUpdateJobId = null, string? finalizeUpdateId = null)
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
                if (!string.IsNullOrWhiteSpace(finalizeUpdateJobId)
                    && jobType.Equals("update_agent", StringComparison.OrdinalIgnoreCase)
                    && result.JobId.Equals(finalizeUpdateJobId, StringComparison.OrdinalIgnoreCase)
                    && !JobExecutionCoordinator.MatchesTargetedUpdate(result, finalizeUpdateJobId, finalizeUpdateId))
                {
                    string rejectedDir = Path.Combine(pendingDir, "migrated");
                    Directory.CreateDirectory(rejectedDir);
                    File.Move(pendingPath, Path.Combine(rejectedDir,
                        $"{Path.GetFileNameWithoutExtension(pendingPath)}-{Guid.NewGuid():N}.json"));
                    await _logger.LogAsync("update.result.stale_ignored", "Pending update result does not match the confirmed update.",
                        new { job_id = result.JobId }, ct, "warning");
                    continue;
                }
                string? resultId = Path.GetFileName(pendingPath).Equals("pending-update-result.json", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(result.JobId)
                    ? $"update-{result.JobId}"
                    : null;
                PendingResultRecord queued = _resultQueue.Enqueue(jobType, result, JobExecutionCoordinator.IsCritical(jobType), resultId);
                if (!string.IsNullOrWhiteSpace(finalizeUpdateJobId)
                    && jobType.Equals("update_agent", StringComparison.OrdinalIgnoreCase)
                    && result.JobId.Equals(finalizeUpdateJobId, StringComparison.OrdinalIgnoreCase))
                {
                    await _jobCoordinator.FinalizePendingUpdateJobAsync(config, queued, finalizeUpdateJobId, finalizeUpdateId!, ct);
                }
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

    private static bool IsDue(DateTimeOffset? previous, int intervalSeconds, DateTimeOffset now)
    {
        if (previous is null)
        {
            return true;
        }

        int interval = Math.Max(intervalSeconds, 5);
        return now - previous.Value >= TimeSpan.FromSeconds(interval);
    }

    private async Task<bool> SendHeartbeatAsync(AgentConfig config, CancellationToken ct)
    {
        AgentHeartbeatPayload payload = _collector.BuildHeartbeat(config);
        // Keep the historical spelling for mixed-version capability consumers.
        payload.Agent["capabilities"] = config.AllowedJobTypes
            .Where(type => type is "configure_telemetry" or "collect_services" || type == "install_software" && config.SoftwareInstallEnabled)
            .Concat(OperationalActions.Capabilities(config))
            .Append("jobs_protocol_v2").Append("job_protocol_v2").ToArray();
        TelemetrySettings? effectiveTelemetry = _telemetryRuntime.Effective;
        payload.Agent["telemetry_mode"] = config.TelemetryMode;
        if (config.TelemetryMode == "continuous")
            payload.Agent["continuous_telemetry"] = new { schema_version = 2, sample_seconds = 10, summary_seconds = 60, upload_seconds = 60, raw_retention_minutes = 30 };
        if (effectiveTelemetry is not null)
            payload.Agent["telemetry_configuration"] = effectiveTelemetry.Report();
        try
        {
            await _api.PostHeartbeatAsync(config, payload, ct);
            try { await _logger.LogAsync("heartbeat.sent", "Heartbeat sent.", new { payload.Hostname, payload.MachineId }, ct); }
            catch (Exception) { /* The server already acknowledged this heartbeat. */ }
            return true;
        }
        catch (Exception ex)
        {
            try { await _logger.LogAsync("heartbeat.failed", ex.Message, BuildErrorData(ex), ct, "error"); }
            catch (Exception) { /* Logging must not stop the scheduler. */ }
            return false;
        }
    }

    private async Task SendCollectionAsync(AgentConfig config, AgentState state, DateTimeOffset now, CancellationToken ct, Action? collected = null)
    {
        await _logger.LogAsync("collection.started", "Aggregated collection started.", null, ct);
        try
        {
            InventoryPlan plan = InventoryPlan.Due(state, now, config.Intervals.CollectSeconds);
            using var scope = WindowsInventoryCollector.WithJobCancellation(ct);
            AgentCollectPayload payload = _collector.BuildScheduledPayload(config, plan);
            collected?.Invoke();
            await _api.PostCollectionAsync(config, payload, ct);
            RecordInventoryCheckpoint(state, plan, payload, now);
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

    internal static void RecordInventoryCheckpoint(AgentState state, InventoryPlan plan, AgentCollectPayload payload, DateTimeOffset now)
    {
        state.LastCollectionAt = now;
        if (plan.Basic && payload.System.Count > 0) state.LastBasicInventoryAt = now;
        if (plan.Detailed && payload.Hardware.GetValueOrDefault("detailed_collected_at") is not null)
            state.LastDetailedHardwareAt = now;
        if (plan.Volumes && payload.SectionStates.GetValueOrDefault("disk") as string == "available")
            state.LastVolumesAt = now;
        if (plan.Network && payload.Network.Count > 0) state.LastNetworkInventoryAt = now;
        if (plan.Ancillary && payload.Security.Count > 0 && payload.Patches.Count > 0)
            state.LastAncillaryInventoryAt = now;
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

    internal static async Task<StateSaveOutcome> SaveStateWithBoundaryAsync(
        Func<CancellationToken, Task> saveAsync,
        JsonlLogger logger,
        TimeSpan currentBackoff,
        CancellationToken ct,
        Func<TimeSpan, CancellationToken, Task>? delayAsync = null)
    {
        try
        {
            await saveAsync(ct);
            return new StateSaveOutcome(DefaultLoopDelay, InitialStateSaveBackoff, Saved: true);
        }
        catch (Exception ex) when (ex is UnauthorizedAccessException or IOException)
        {
            TimeSpan delay = currentBackoff <= TimeSpan.Zero ? InitialStateSaveBackoff : currentBackoff;
            string errorCode = ex switch
            {
                UnauthorizedAccessException => "STATE_SAVE_UNAUTHORIZED",
                IOException => "STATE_SAVE_IO_FAILED",
                _ => "STATE_SAVE_FAILED"
            };
            try
            {
                await logger.LogAsync("state.save.failed", "Agent state persistence failed; worker will retry.", new
                {
                    error_code = errorCode,
                    exception_type = ex.GetType().FullName,
                    error_message = NightOwlSanitizer.SanitizeText(ex.Message).Value,
                    retry_delay_seconds = Math.Round(delay.TotalSeconds, 3)
                }, ct, "error");
            }
            catch (Exception logEx) when (logEx is not OperationCanceledException)
            {
                // State persistence failures must not terminate the worker if diagnostics cannot be written.
            }

            if (delayAsync is not null)
            {
                await delayAsync(delay, ct);
            }

            TimeSpan next = TimeSpan.FromSeconds(Math.Min(delay.TotalSeconds * 2, MaxStateSaveBackoff.TotalSeconds));
            return new StateSaveOutcome(delayAsync is null ? delay : TimeSpan.Zero, next, Saved: false);
        }
    }
}

internal sealed record StateSaveOutcome(TimeSpan LoopDelay, TimeSpan NextBackoff, bool Saved);
