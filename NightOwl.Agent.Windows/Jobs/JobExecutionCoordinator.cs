using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;
using System.Text.Json;

namespace NightOwl.Agent.Windows.Jobs;

public sealed class JobExecutionCoordinator
{
    private readonly JobExecutionPolicy _policy;
    private readonly JsonlLogger _logger;
    private readonly object _sync = new();
    private readonly Dictionary<string, ActiveJob> _active = new(StringComparer.OrdinalIgnoreCase);

    public JobExecutionCoordinator(JobExecutionPolicy policy, JsonlLogger logger)
    {
        _policy = policy;
        _logger = logger;
    }

    public async Task RecoverInterruptedJobsAsync(AgentConfig config, PendingResultQueue resultQueue, CancellationToken ct)
    {
        HashSet<string> pendingResultJobIds = new(StringComparer.OrdinalIgnoreCase);
        foreach (PendingResultRecord pendingResult in resultQueue.LoadAll())
        {
            if (string.IsNullOrWhiteSpace(pendingResult.JobId))
            {
                continue;
            }

            pendingResultJobIds.Add(pendingResult.JobId);
            if (TryBuildFinalResult(config, pendingResult, out RemoteJobResult? finalResult))
            {
                RemoteJobResult recovered = finalResult!;
                _policy.Store.MarkFinal(recovered);
                await _logger.LogAsync("job.final_state.recovered", "Local job state finalized from pending result.", new
                {
                    recovered.JobId,
                    recovered.JobType,
                    recovered.Status,
                    result_id = pendingResult.ResultId
                }, ct);
            }
        }

        foreach (JobStateRecord record in _policy.Store.LoadAll().Where(record => record.Status.Equals("running", StringComparison.OrdinalIgnoreCase)))
        {
            if (pendingResultJobIds.Contains(record.JobId))
            {
                await _logger.LogAsync("job.interrupted.recovery_skipped", "Running job has a pending final result; interrupted recovery skipped.", new
                {
                    record.JobId,
                    record.JobType
                }, ct);
                continue;
            }

            DateTimeOffset now = DateTimeOffset.UtcNow;
            if (ShouldSkipInterruptedRecoveryForExternalRunner(record, now))
            {
                await _logger.LogAsync("job.interrupted.recovery_skipped_external_runner", "Running lifecycle job is owned by an external runner; interrupted recovery skipped.", new
                {
                    record.JobId,
                    record.JobType,
                    record.ExternalRunnerStartedAt,
                    record.ExternalRunnerTimeoutSeconds,
                    record.ExternalRunnerPath
                }, ct);
                continue;
            }

            JobExecutionResult result = new()
            {
                JobId = record.JobId,
                Status = JobFinalStatuses.Failed,
                StartedAt = record.UpdatedAt,
                FinishedAt = now,
                DurationSeconds = Math.Round(Math.Max(0, (now - record.UpdatedAt).TotalSeconds), 3),
                ExitCode = 1,
                Stdout = "",
                Stderr = "Job was interrupted by agent restart.",
                ErrorMessage = "Job was interrupted by agent restart.",
                Result = new
                {
                    type = record.JobType,
                    error_code = JobErrorCodes.JobInterrupted,
                    message = "Job was interrupted by agent restart.",
                    agent_version = config.AgentVersion,
                    machine_id = config.MachineId
                }
            };

            _policy.Store.MarkFinal(new RemoteJobResult
            {
                JobId = record.JobId,
                JobType = record.JobType,
                Status = result.Status,
                StartedAt = result.StartedAt,
                CompletedAt = result.FinishedAt,
                DurationMs = (long)Math.Round(result.DurationSeconds * 1000),
                Attempt = Math.Max(record.Attempt, 1),
                ErrorCode = JobErrorCodes.JobInterrupted,
                ErrorMessage = result.ErrorMessage,
                Output = result.Result,
                AgentVersion = config.AgentVersion,
                MachineId = config.MachineId
            }, record.CorrelationId);

            PendingResultRecord pending = resultQueue.Enqueue(record.JobType, result, IsCritical(record.JobType));
            await _logger.LogAsync("job.interrupted.recovered", "Running job from previous process was marked interrupted.", new
            {
                record.JobId,
                record.JobType,
                result_id = pending.ResultId,
                error_code = JobErrorCodes.JobInterrupted
            }, ct, "warning");
        }
    }

    private static bool TryBuildFinalResult(AgentConfig config, PendingResultRecord pendingResult, out RemoteJobResult? finalResult)
    {
        finalResult = null;
        try
        {
            JobExecutionResult result = pendingResult.Payload.Deserialize<JobExecutionResult>(new JsonSerializerOptions(JsonSerializerDefaults.Web))
                ?? throw new InvalidOperationException(JobErrorCodes.ResultPayloadInvalid);
            if (!JobFinalStatuses.All.Contains(result.Status))
            {
                return false;
            }

            DateTimeOffset completedAt = result.FinishedAt == default ? DateTimeOffset.UtcNow : result.FinishedAt;
            DateTimeOffset startedAt = result.StartedAt == default ? completedAt : result.StartedAt;
            string errorCode = ExtractErrorCode(result.Result);
            finalResult = new RemoteJobResult
            {
                JobId = result.JobId,
                JobType = pendingResult.JobType,
                Status = result.Status,
                StartedAt = startedAt,
                CompletedAt = completedAt,
                DurationMs = (long)Math.Round(Math.Max(0, result.DurationSeconds) * 1000),
                Attempt = 1,
                ErrorCode = errorCode,
                ErrorMessage = result.ErrorMessage,
                Output = result.Result,
                AgentVersion = config.AgentVersion,
                MachineId = config.MachineId
            };
            return !string.IsNullOrWhiteSpace(finalResult.JobId) && !string.IsNullOrWhiteSpace(finalResult.JobType);
        }
        catch
        {
            return false;
        }
    }

    private static string ExtractErrorCode(object? output)
    {
        if (output is null)
        {
            return "";
        }

        try
        {
            if (output is JsonElement element
                && element.ValueKind == JsonValueKind.Object
                && element.TryGetProperty("error_code", out JsonElement errorCode)
                && errorCode.ValueKind == JsonValueKind.String)
            {
                return errorCode.GetString() ?? "";
            }
        }
        catch
        {
            return "";
        }

        return "";
    }

    public static bool ShouldSkipInterruptedRecoveryForExternalRunner(JobStateRecord record, DateTimeOffset now)
    {
        if (!record.ExternalRunnerActive || record.ExternalRunnerStartedAt is null)
        {
            return false;
        }
        int timeoutSeconds = Math.Clamp(record.ExternalRunnerTimeoutSeconds <= 0 ? 900 : record.ExternalRunnerTimeoutSeconds, 60, 3600);
        DateTimeOffset expiresAt = record.ExternalRunnerStartedAt.Value.AddSeconds(timeoutSeconds);
        return now <= expiresAt;
    }

    public async Task<JobStartDecision> TryStartAsync(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        string category = GetCategory(job.Type);
        string jobId = job.Id;
        string jobType = job.Type;
        string? errorCode = null;
        string? message = null;
        int activeCount;
        int heavyCount;

        lock (_sync)
        {
            activeCount = _active.Count;
            heavyCount = _active.Values.Count(active => active.Category.Equals(JobCategories.Heavy, StringComparison.OrdinalIgnoreCase));
            bool activeExclusive = _active.Values.Any(active => active.Category.Equals(JobCategories.Exclusive, StringComparison.OrdinalIgnoreCase));
            bool activeLifecycle = _active.Values.Any(active => IsLifecycle(active.JobType));

            if (activeLifecycle)
            {
                errorCode = JobErrorCodes.JobExclusiveConflict;
                message = "An agent lifecycle job is active; no other jobs can start.";
            }
            else if (category.Equals(JobCategories.Exclusive, StringComparison.OrdinalIgnoreCase) && _active.Count > 0)
            {
                errorCode = JobErrorCodes.JobExclusiveConflict;
                message = $"{jobType} requires exclusive execution.";
            }
            else if (!category.Equals(JobCategories.Exclusive, StringComparison.OrdinalIgnoreCase) && activeExclusive)
            {
                errorCode = JobErrorCodes.JobExclusiveConflict;
                message = "An exclusive job is active.";
            }
            else if (_active.Count >= 2)
            {
                errorCode = JobErrorCodes.JobConcurrencyLimit;
                message = "Maximum simultaneous job limit reached.";
            }
            else if (category.Equals(JobCategories.Heavy, StringComparison.OrdinalIgnoreCase) && heavyCount >= 1)
            {
                errorCode = JobErrorCodes.JobConcurrencyLimit;
                message = "Maximum heavy job limit reached.";
            }
            else
            {
                _active[jobId] = new ActiveJob(jobId, jobType, category, DateTimeOffset.UtcNow);
            }
        }

        if (errorCode is not null)
        {
            await _logger.LogAsync("job.start.blocked", message ?? "Job could not start.", new
            {
                job_id = jobId,
                job_type = jobType,
                category,
                active_jobs = activeCount,
                active_heavy_jobs = heavyCount,
                error_code = errorCode
            }, ct, "warning");

            DateTimeOffset now = DateTimeOffset.UtcNow;
            JobExecutionResult result = new()
            {
                JobId = jobId,
                Status = JobFinalStatuses.Failed,
                StartedAt = now,
                FinishedAt = now,
                DurationSeconds = 0,
                ExitCode = 1,
                Stdout = "",
                Stderr = message ?? "",
                ErrorMessage = message ?? "",
                Result = new
                {
                    type = jobType,
                    error_code = errorCode,
                    message,
                    category,
                    active_jobs = activeCount,
                    agent_version = config.AgentVersion,
                    machine_id = config.MachineId
                }
            };
            _policy.MarkFinal(config, job, result, errorCode);
            return JobStartDecision.Blocked(result, errorCode);
        }

        await _logger.LogAsync("job.started", "Job execution slot acquired.", new
        {
            job_id = jobId,
            job_type = jobType,
            category,
            active_jobs = activeCount + 1
        }, ct);
        return JobStartDecision.Started(category);
    }

    public async Task CompleteAsync(AgentConfig config, AgentJobRequest job, JobExecutionResult result, string errorCode, CancellationToken ct)
    {
        string category;
        int activeJobs;
        lock (_sync)
        {
            category = _active.TryGetValue(job.Id, out ActiveJob? active) ? active.Category : GetCategory(job.Type);
            _active.Remove(job.Id);
            activeJobs = _active.Count;
        }
        _policy.MarkFinal(config, job, result, errorCode);
        await _logger.LogAsync(result.Status == JobFinalStatuses.Completed ? "job.completed" : "job.failed", "Job execution slot released.", new
        {
            job_id = job.Id,
            job_type = job.Type,
            category,
            result.Status,
            error_code = errorCode,
            active_jobs = activeJobs
        }, ct, result.Status == JobFinalStatuses.Completed ? "info" : "warning");
    }

    public void Release(AgentJobRequest job)
    {
        lock (_sync)
        {
            _active.Remove(job.Id);
        }
    }

    public static string GetCategory(string jobType)
    {
        if (jobType.Equals("ping", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("collect_disks", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("collect_security", StringComparison.OrdinalIgnoreCase))
        {
            return JobCategories.Light;
        }
        if (jobType.Equals("restart_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("update_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("repair_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("uninstall_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("update_trusted_release_keys", StringComparison.OrdinalIgnoreCase))
        {
            return JobCategories.Exclusive;
        }
        return JobCategories.Heavy;
    }

    public static bool IsCritical(string jobType)
    {
        return jobType.Equals("restart_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("update_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("repair_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("uninstall_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("update_trusted_release_keys", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsLifecycle(string jobType)
    {
        return jobType.Equals("update_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("repair_agent", StringComparison.OrdinalIgnoreCase)
            || jobType.Equals("uninstall_agent", StringComparison.OrdinalIgnoreCase);
    }

    private sealed record ActiveJob(string JobId, string JobType, string Category, DateTimeOffset StartedAt);
}

public static class JobCategories
{
    public const string Light = "light";
    public const string Heavy = "heavy";
    public const string Exclusive = "exclusive";
}

public sealed record JobStartDecision(bool CanStart, string Category, JobExecutionResult? Result, string ErrorCode)
{
    public static JobStartDecision Started(string category) => new(true, category, null, "");
    public static JobStartDecision Blocked(JobExecutionResult result, string errorCode) => new(false, "", result, errorCode);
}
