using System.Text.Json;
using System.Text.RegularExpressions;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Jobs;

public sealed class JobExecutionPolicy
{
    public const int MaxOutputBytes = 64 * 1024;
    private static readonly Regex UuidRegex = new("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$|^[0-9a-fA-F]{32}$", RegexOptions.Compiled);
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private static readonly HashSet<string> Allowlist = new(StringComparer.OrdinalIgnoreCase)
    {
        "ping",
        "force_inventory",
        "collect_disks",
        "collect_software",
        "collect_security",
        "collect_logs",
        "windows_update_scan",
        "restart_agent",
        "update_agent",
        "update_trusted_release_keys",
        "repair_agent",
        "uninstall_agent"
    };

    private readonly JobStore _store;

    public JobExecutionPolicy()
        : this(new JobStore(Path.Combine(NightOwlPaths.Current.StateDir, "jobs")))
    {
    }

    public JobExecutionPolicy(JobStore store)
    {
        _store = store;
    }

    public JobStore Store => _store;

    public JobDecision Prepare(AgentConfig config, AgentJobRequest job)
    {
        DateTimeOffset now = DateTimeOffset.UtcNow;
        string jobId = (job.Id ?? "").Trim();
        string jobType = (job.Type ?? "").Trim();
        int attempt = Math.Max(job.Attempt, 1);
        string correlationId = job.CorrelationId ?? "";

        if (!UuidRegex.IsMatch(jobId))
        {
            return JobDecision.Final(ToResult(config, job, JobFinalStatuses.InvalidParameters, JobErrorCodes.JobIdInvalid, "job_id must be UUID.", now, now, null));
        }

        JobStateRecord? existing = _store.Load(jobId);
        if (existing?.IsFinal == true)
        {
            return JobDecision.Final(ToDuplicateResult(config, job, existing, now));
        }
        if (existing is not null && string.Equals(existing.Status, "running", StringComparison.OrdinalIgnoreCase))
        {
            return JobDecision.Final(ToDuplicateResult(config, job, existing, now));
        }

        if (!Allowlist.Contains(jobType) || !config.AllowedJobTypes.Contains(jobType, StringComparer.OrdinalIgnoreCase))
        {
            _store.Mark(jobId, jobType, JobFinalStatuses.Unsupported, attempt, correlationId, JobErrorCodes.JobUnsupported, "Unsupported job type.");
            return JobDecision.Final(ToResult(config, job, JobFinalStatuses.Unsupported, JobErrorCodes.JobUnsupported, "Unsupported job type.", now, now, null));
        }

        DateTimeOffset expiresAt = job.ExpiresAt ?? now.Add(GetDefaultTimeout(jobType)).Add(TimeSpan.FromMinutes(5));
        TimeSpan clockSkew = TimeSpan.FromSeconds(30);
        if (expiresAt <= now.Subtract(clockSkew))
        {
            _store.Mark(jobId, jobType, JobFinalStatuses.Expired, attempt, correlationId, JobErrorCodes.JobExpired, "Job expired.");
            return JobDecision.Final(ToResult(config, job, JobFinalStatuses.Expired, JobErrorCodes.JobExpired, "Job expired.", now, now, null));
        }
        if (job.NotBefore is not null && job.NotBefore.Value > now.Add(clockSkew))
        {
            _store.Mark(jobId, jobType, JobFinalStatuses.Cancelled, attempt, correlationId, JobErrorCodes.JobNotReady, "Job not ready yet.");
            return JobDecision.Final(ToResult(config, job, JobFinalStatuses.Cancelled, JobErrorCodes.JobNotReady, "Job not ready yet.", now, now, null));
        }
        if (attempt > Math.Max(job.MaxAttempts, 1))
        {
            _store.Mark(jobId, jobType, JobFinalStatuses.InvalidParameters, attempt, correlationId, JobErrorCodes.JobInvalidParameters, "attempt exceeds max_attempts.");
            return JobDecision.Final(ToResult(config, job, JobFinalStatuses.InvalidParameters, JobErrorCodes.JobInvalidParameters, "attempt exceeds max_attempts.", now, now, null));
        }

        try
        {
            ValidateParameters(job);
        }
        catch (Exception ex)
        {
            _store.Mark(jobId, jobType, JobFinalStatuses.InvalidParameters, attempt, correlationId, JobErrorCodes.JobInvalidParameters, ex.Message);
            return JobDecision.Final(ToResult(config, job, JobFinalStatuses.InvalidParameters, JobErrorCodes.JobInvalidParameters, ex.Message, now, now, null));
        }

        int timeoutSeconds = ResolveTimeoutSeconds(job);
        _store.Mark(jobId, jobType, "validated", attempt, correlationId);
        _store.Prune();
        return JobDecision.Execute(timeoutSeconds);
    }

    public void MarkRunning(AgentJobRequest job)
    {
        _store.Mark(job.Id, job.Type, "running", Math.Max(job.Attempt, 1), job.CorrelationId);
    }

    public void MarkFinal(AgentConfig config, AgentJobRequest job, JobExecutionResult result, string errorCode = "")
    {
        if (string.IsNullOrWhiteSpace(result.JobId) || !UuidRegex.IsMatch(result.JobId))
        {
            return;
        }
        RemoteJobResult remoteResult = ToRemoteResult(config, job, result, errorCode);
        _store.MarkFinal(remoteResult, job.CorrelationId);
    }

    public static int ResolveTimeoutSeconds(AgentJobRequest job)
    {
        TimeSpan fallback = GetDefaultTimeout(job.Type);
        int requested = job.TimeoutSeconds <= 0 ? (int)fallback.TotalSeconds : job.TimeoutSeconds;
        int min = 5;
        int max = job.Type.Equals("windows_update_scan", StringComparison.OrdinalIgnoreCase) ? 900 :
            job.Type.Equals("force_inventory", StringComparison.OrdinalIgnoreCase) || job.Type.Equals("collect_software", StringComparison.OrdinalIgnoreCase) ? 300 :
            job.Type.Equals("collect_logs", StringComparison.OrdinalIgnoreCase) ? 180 :
            job.Type.Equals("restart_agent", StringComparison.OrdinalIgnoreCase) || job.Type.Equals("update_agent", StringComparison.OrdinalIgnoreCase) ? 60 :
            job.Type.Equals("update_trusted_release_keys", StringComparison.OrdinalIgnoreCase) ? 180 :
            job.Type.Equals("repair_agent", StringComparison.OrdinalIgnoreCase) ? 60 :
            120;
        return Math.Clamp(requested, min, max);
    }

    public static TimeSpan GetDefaultTimeout(string jobType) => jobType switch
    {
        "ping" => TimeSpan.FromSeconds(30),
        "force_inventory" => TimeSpan.FromSeconds(300),
        "collect_software" => TimeSpan.FromSeconds(300),
        "collect_logs" => TimeSpan.FromSeconds(180),
        "windows_update_scan" => TimeSpan.FromSeconds(900),
        "restart_agent" => TimeSpan.FromSeconds(60),
        "update_agent" => TimeSpan.FromSeconds(60),
        "update_trusted_release_keys" => TimeSpan.FromSeconds(180),
        "repair_agent" => TimeSpan.FromSeconds(60),
        _ => TimeSpan.FromSeconds(120)
    };

    private static void ValidateParameters(AgentJobRequest job)
    {
        Dictionary<string, object?> p = job.Payload;
        switch (job.Type)
        {
            case "ping":
                EnsureAllowedFields(p, "target");
                string target = GetString(p, "target", "");
                if (target.Length > 253 || target.Contains(';') || target.Contains('&') || target.Contains('|'))
                {
                    throw new InvalidOperationException("Invalid ping target.");
                }
                break;
            case "collect_logs":
                EnsureAllowedFields(p, "source", "max_lines", "max_bytes", "since_minutes");
                string source = GetString(p, "source", "agent");
                if (!source.Equals("agent", StringComparison.OrdinalIgnoreCase) && !source.Equals("updater", StringComparison.OrdinalIgnoreCase) && !source.Equals("tray", StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException("Invalid log source.");
                }
                int maxLines = GetInt(p, "max_lines", 200);
                int maxBytes = GetInt(p, "max_bytes", 64 * 1024);
                int sinceMinutes = GetInt(p, "since_minutes", 1440);
                if (maxLines is < 1 or > 1000 || maxBytes is < 1024 or > MaxOutputBytes || sinceMinutes is < 1 or > 10080)
                {
                    throw new InvalidOperationException("Invalid collect_logs limits.");
                }
                break;
            case "update_agent":
            {
                EnsureAllowedFields(
                    p,
                    "target_version",
                    "release_id",
                    "package_url",
                    "checksum_url",
                    "sha256",
                    "size",
                    "manifest_url",
                    "manifest_sha256",
                    "signature_url",
                    "signature_sha256",
                    "signature_key_id",
                    "signature_valid",
                    "legacy_unsigned",
                    "channel",
                    "minimum_updater_version",
                    "mandatory",
                    "force",
                    "source",
                    "source_channel",
                    "policy_reason",
                    "timeout_seconds");
                string targetVersion = GetString(p, "target_version", "latest");
                string channel = GetString(p, "channel", "stable");
                string releaseId = GetString(p, "release_id", "");
                string packageUrl = GetString(p, "package_url", "");
                string checksumUrl = GetString(p, "checksum_url", "");
                string manifestUrl = GetString(p, "manifest_url", "");
                string manifestSha256 = GetString(p, "manifest_sha256", "");
                string signatureUrl = GetString(p, "signature_url", "");
                string signatureSha256 = GetString(p, "signature_sha256", "");
                string signatureKeyId = GetString(p, "signature_key_id", "");
                string sha256 = GetString(p, "sha256", "");
                string minimumUpdaterVersion = GetString(p, "minimum_updater_version", "");
                long size = GetLong(p, "size", 0);
                if (targetVersion.Length > 64
                    || channel.Length > 32
                    || releaseId.Length > 64
                    || minimumUpdaterVersion.Length > 64
                    || signatureKeyId.Length > 120
                    || size < 0
                    || (!string.IsNullOrWhiteSpace(sha256) && !Regex.IsMatch(sha256, "^[0-9a-fA-F]{64}$"))
                    || (!string.IsNullOrWhiteSpace(manifestSha256) && !Regex.IsMatch(manifestSha256, "^[0-9a-fA-F]{64}$"))
                    || (!string.IsNullOrWhiteSpace(signatureSha256) && !Regex.IsMatch(signatureSha256, "^[0-9a-fA-F]{64}$"))
                    || !IsValidHttpsUrl(packageUrl)
                    || !IsValidHttpsUrl(checksumUrl)
                    || !IsValidHttpsUrl(manifestUrl)
                    || !IsValidHttpsUrl(signatureUrl))
                {
                    throw new InvalidOperationException("Invalid update parameters.");
                }
                break;
            }
            case "update_trusted_release_keys":
            {
                EnsureAllowedFields(
                    p,
                    "metadata_url",
                    "bundle_url",
                    "signature_url",
                    "expected_root_key_id",
                    "expected_bundle_version",
                    "expected_sha256",
                    "source",
                    "timeout_seconds");
                string metadataUrl = GetString(p, "metadata_url", "");
                string bundleUrl = GetString(p, "bundle_url", "");
                string signatureUrl = GetString(p, "signature_url", "");
                string rootKeyId = GetString(p, "expected_root_key_id", "");
                string expectedSha = GetString(p, "expected_sha256", "");
                long expectedBundleVersion = GetLong(p, "expected_bundle_version", 0);
                if (!IsValidHttpsUrl(metadataUrl)
                    || !IsValidHttpsUrl(bundleUrl)
                    || !IsValidHttpsUrl(signatureUrl)
                    || string.IsNullOrWhiteSpace(rootKeyId)
                    || rootKeyId.Length > 120
                    || expectedBundleVersion < 0
                    || (!string.IsNullOrWhiteSpace(expectedSha) && !Regex.IsMatch(expectedSha, "^[0-9a-fA-F]{64}$")))
                {
                    throw new InvalidOperationException("Invalid trust bundle update parameters.");
                }
                break;
            }
            case "uninstall_agent":
            {
                EnsureAllowedFields(p, "mode", "purge_authorized", "source", "timeout_seconds");
                string mode = GetString(p, "mode", "");
                if (!mode.Equals("uninstall", StringComparison.OrdinalIgnoreCase)
                    && !mode.Equals("purge", StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException("Invalid uninstall mode.");
                }
                if (mode.Equals("purge", StringComparison.OrdinalIgnoreCase) && !GetBool(p, "purge_authorized", false))
                {
                    throw new InvalidOperationException("Remote purge requires explicit backend authorization.");
                }
                break;
            }
            case "repair_agent":
            {
                EnsureAllowedFields(
                    p,
                    "operation",
                    "target_version",
                    "current_version",
                    "release_id",
                    "package_url",
                    "checksum_url",
                    "sha256",
                    "size",
                    "manifest_url",
                    "manifest_sha256",
                    "signature_url",
                    "signature_sha256",
                    "signature_key_id",
                    "signature_valid",
                    "legacy_unsigned",
                    "channel",
                    "minimum_updater_version",
                    "mandatory",
                    "force",
                    "source",
                    "identity_preservation_required",
                    "enrollment_allowed",
                    "timeout_seconds");
                string operation = GetString(p, "operation", "");
                string targetVersion = GetString(p, "target_version", "");
                string currentVersion = GetString(p, "current_version", "");
                string channel = GetString(p, "channel", "");
                string releaseId = GetString(p, "release_id", "");
                string packageUrl = GetString(p, "package_url", "");
                string checksumUrl = GetString(p, "checksum_url", "");
                string manifestUrl = GetString(p, "manifest_url", "");
                string manifestSha256 = GetString(p, "manifest_sha256", "");
                string signatureUrl = GetString(p, "signature_url", "");
                string signatureSha256 = GetString(p, "signature_sha256", "");
                string signatureKeyId = GetString(p, "signature_key_id", "");
                string sha256 = GetString(p, "sha256", "");
                long size = GetLong(p, "size", 0);
                if (!operation.Equals("repair", StringComparison.OrdinalIgnoreCase)
                    || string.IsNullOrWhiteSpace(targetVersion)
                    || !targetVersion.Equals(currentVersion, StringComparison.OrdinalIgnoreCase)
                    || targetVersion.Length > 64
                    || channel.Length > 32
                    || releaseId.Length > 64
                    || signatureKeyId.Length > 120
                    || size <= 0
                    || !Regex.IsMatch(sha256, "^[0-9a-fA-F]{64}$")
                    || !Regex.IsMatch(manifestSha256, "^[0-9a-fA-F]{64}$")
                    || !Regex.IsMatch(signatureSha256, "^[0-9a-fA-F]{64}$")
                    || !IsValidHttpsUrl(packageUrl)
                    || !IsValidHttpsUrl(checksumUrl)
                    || !IsValidHttpsUrl(manifestUrl)
                    || !IsValidHttpsUrl(signatureUrl))
                {
                    throw new InvalidOperationException("Invalid repair parameters.");
                }
                break;
            }
            case "restart_agent":
                EnsureAllowedFields(p, "reason");
                if (GetString(p, "reason", "").Length > 256)
                {
                    throw new InvalidOperationException("Invalid restart reason.");
                }
                break;
            default:
                EnsureAllowedFields(p);
                break;
        }
    }

    private static void EnsureAllowedFields(Dictionary<string, object?> parameters, params string[] allowed)
    {
        HashSet<string> allowedSet = new(allowed, StringComparer.OrdinalIgnoreCase);
        foreach (string key in parameters.Keys)
        {
            if (!allowedSet.Contains(key))
            {
                throw new InvalidOperationException($"Unexpected parameter: {key}");
            }
        }
    }

    private static JobExecutionResult ToDuplicateResult(AgentConfig config, AgentJobRequest job, JobStateRecord existing, DateTimeOffset now)
    {
        return ToResult(config, job, JobFinalStatuses.Duplicate, JobErrorCodes.JobDuplicate, "Duplicate job_id.", now, now, new
        {
            original_status = existing.Status,
            original_final_at = existing.FinalAt,
            original_error_code = existing.ErrorCode
        });
    }

    private static JobExecutionResult ToResult(AgentConfig config, AgentJobRequest job, string status, string errorCode, string message, DateTimeOffset started, DateTimeOffset completed, object? output)
    {
        return new JobExecutionResult
        {
            JobId = job.Id,
            Status = status,
            StartedAt = started,
            FinishedAt = completed,
            DurationSeconds = Math.Round((completed - started).TotalSeconds, 3),
            ExitCode = status == JobFinalStatuses.Completed ? 0 : 1,
            Stdout = status == JobFinalStatuses.Completed ? "ok" : "",
            Stderr = status == JobFinalStatuses.Completed ? "" : message,
            ErrorMessage = status == JobFinalStatuses.Completed ? "" : message,
            Result = new
            {
                type = job.Type,
                error_code = errorCode,
                message,
                output,
                agent_version = config.AgentVersion,
                machine_id = config.MachineId
            }
        };
    }

    private static RemoteJobResult ToRemoteResult(AgentConfig config, AgentJobRequest job, JobExecutionResult result, string errorCode)
    {
        DateTimeOffset completed = result.FinishedAt == default || result.FinishedAt == DateTimeOffset.MinValue ? DateTimeOffset.UtcNow : result.FinishedAt;
        return new RemoteJobResult
        {
            JobId = result.JobId,
            JobType = job.Type,
            Status = result.Status,
            StartedAt = result.StartedAt == default ? completed : result.StartedAt,
            CompletedAt = completed,
            DurationMs = (long)Math.Round(Math.Max(0, result.DurationSeconds) * 1000),
            Attempt = Math.Max(job.Attempt, 1),
            ErrorCode = errorCode,
            ErrorMessage = result.ErrorMessage,
            Output = result.Result,
            AgentVersion = config.AgentVersion,
            MachineId = config.MachineId,
            OutputTruncated = false
        };
    }

    private static string GetString(Dictionary<string, object?> parameters, string key, string fallback)
    {
        return parameters.TryGetValue(key, out object? value) && value is not null ? value.ToString() ?? fallback : fallback;
    }

    private static int GetInt(Dictionary<string, object?> parameters, string key, int fallback)
    {
        if (!parameters.TryGetValue(key, out object? value) || value is null)
        {
            return fallback;
        }
        if (value is JsonElement element && element.TryGetInt32(out int parsedElement))
        {
            return parsedElement;
        }
        return int.TryParse(value.ToString(), out int parsed) ? parsed : fallback;
    }

    private static long GetLong(Dictionary<string, object?> parameters, string key, long fallback)
    {
        if (!parameters.TryGetValue(key, out object? value) || value is null)
        {
            return fallback;
        }
        if (value is JsonElement element && element.TryGetInt64(out long parsedElement))
        {
            return parsedElement;
        }
        return long.TryParse(value.ToString(), out long parsed) ? parsed : fallback;
    }

    private static bool GetBool(Dictionary<string, object?> parameters, string key, bool fallback)
    {
        if (!parameters.TryGetValue(key, out object? value) || value is null)
        {
            return fallback;
        }
        if (value is JsonElement element)
        {
            return element.ValueKind switch
            {
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.String => bool.TryParse(element.GetString(), out bool parsedElement) ? parsedElement : fallback,
                _ => fallback
            };
        }
        return bool.TryParse(value.ToString(), out bool parsed) ? parsed : fallback;
    }

    private static bool IsValidHttpsUrl(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return true;
        }
        return Uri.TryCreate(value, UriKind.Absolute, out Uri? uri)
            && uri.Scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase);
    }
}

public sealed record JobDecision(bool ShouldExecute, JobExecutionResult? FinalResult, int TimeoutSeconds)
{
    public static JobDecision Execute(int timeoutSeconds) => new(true, null, timeoutSeconds);
    public static JobDecision Final(JobExecutionResult result) => new(false, result, 0);
}
