using System.Text.Json;
using System.Text.Json.Serialization;

namespace NightOwl.Agent.Shared;

public static class JobFinalStatuses
{
    public const string Completed = "completed";
    public const string Failed = "failed";
    public const string TimedOut = "timed_out";
    public const string Expired = "expired";
    public const string Duplicate = "duplicate";
    public const string Unsupported = "unsupported";
    public const string InvalidParameters = "invalid_parameters";
    public const string Cancelled = "cancelled";
    public const string Interrupted = "interrupted";
    public const string RolledBack = "rolled_back";
    public const string RollbackFailed = "rollback_failed";

    public static readonly HashSet<string> All = new(StringComparer.OrdinalIgnoreCase)
    {
        Completed,
        Failed,
        TimedOut,
        Expired,
        Duplicate,
        Unsupported,
        InvalidParameters,
        Cancelled,
        Interrupted,
        RolledBack,
        RollbackFailed
    };
}

public static class JobErrorCodes
{
    public const string JobIdInvalid = "JOB_ID_INVALID";
    public const string JobDuplicate = "JOB_DUPLICATE";
    public const string JobExpired = "JOB_EXPIRED";
    public const string JobNotReady = "JOB_NOT_READY";
    public const string JobUnsupported = "JOB_UNSUPPORTED";
    public const string JobInvalidParameters = "JOB_INVALID_PARAMETERS";
    public const string JobTimeout = "JOB_TIMEOUT";
    public const string JobCancelled = "JOB_CANCELLED";
    public const string JobExecutionFailed = "JOB_EXECUTION_FAILED";
    public const string JobResultTooLarge = "JOB_RESULT_TOO_LARGE";
    public const string JobStateInvalid = "JOB_STATE_INVALID";
    public const string JobConcurrencyLimit = "JOB_CONCURRENCY_LIMIT";
    public const string JobExclusiveConflict = "JOB_EXCLUSIVE_CONFLICT";
    public const string JobInterrupted = "JOB_INTERRUPTED";
    public const string ResultSendFailed = "RESULT_SEND_FAILED";
    public const string ResultQueueCorrupted = "RESULT_QUEUE_CORRUPTED";
    public const string ResultQueueFull = "RESULT_QUEUE_FULL";
    public const string ResultPayloadInvalid = "RESULT_PAYLOAD_INVALID";
    public const string ResultRetryExhausted = "RESULT_RETRY_EXHAUSTED";
}

public sealed class RemoteJob
{
    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = "";

    [JsonPropertyName("job_type")]
    public string JobType { get; set; } = "";

    [JsonPropertyName("created_at")]
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("not_before")]
    public DateTimeOffset? NotBefore { get; set; }

    [JsonPropertyName("expires_at")]
    public DateTimeOffset ExpiresAt { get; set; }

    [JsonPropertyName("timeout_seconds")]
    public int TimeoutSeconds { get; set; }

    [JsonPropertyName("attempt")]
    public int Attempt { get; set; } = 1;

    [JsonPropertyName("max_attempts")]
    public int MaxAttempts { get; set; } = 1;

    [JsonPropertyName("priority")]
    public int Priority { get; set; }

    [JsonPropertyName("parameters")]
    public Dictionary<string, object?> Parameters { get; set; } = new();

    [JsonPropertyName("correlation_id")]
    public string CorrelationId { get; set; } = "";
}

public sealed class RemoteJobResult
{
    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = "";

    [JsonPropertyName("job_type")]
    public string JobType { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("started_at")]
    public DateTimeOffset StartedAt { get; set; }

    [JsonPropertyName("completed_at")]
    public DateTimeOffset CompletedAt { get; set; }

    [JsonPropertyName("duration_ms")]
    public long DurationMs { get; set; }

    [JsonPropertyName("attempt")]
    public int Attempt { get; set; }

    [JsonPropertyName("error_code")]
    public string ErrorCode { get; set; } = "";

    [JsonPropertyName("error_message")]
    public string ErrorMessage { get; set; } = "";

    [JsonPropertyName("output")]
    public object? Output { get; set; }

    [JsonPropertyName("agent_version")]
    public string AgentVersion { get; set; } = "";

    [JsonPropertyName("machine_id")]
    public string MachineId { get; set; } = "";

    [JsonPropertyName("output_truncated")]
    public bool OutputTruncated { get; set; }
}

public sealed class JobStateRecord
{
    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = "";

    [JsonPropertyName("job_type")]
    public string JobType { get; set; } = "";

    [JsonPropertyName("correlation_id")]
    public string CorrelationId { get; set; } = "";

    [JsonPropertyName("attempt")]
    public int Attempt { get; set; }

    [JsonPropertyName("status")]
    public string Status { get; set; } = "received";

    [JsonPropertyName("created_at")]
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("updated_at")]
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("final_at")]
    public DateTimeOffset? FinalAt { get; set; }

    [JsonPropertyName("error_code")]
    public string ErrorCode { get; set; } = "";

    [JsonPropertyName("error_message")]
    public string ErrorMessage { get; set; } = "";

    [JsonPropertyName("result")]
    public RemoteJobResult? Result { get; set; }

    [JsonPropertyName("external_runner_active")]
    public bool ExternalRunnerActive { get; set; }

    [JsonPropertyName("external_runner_started_at")]
    public DateTimeOffset? ExternalRunnerStartedAt { get; set; }

    [JsonPropertyName("external_runner_timeout_seconds")]
    public int ExternalRunnerTimeoutSeconds { get; set; }

    [JsonPropertyName("external_runner_path")]
    public string ExternalRunnerPath { get; set; } = "";

    [JsonPropertyName("external_runner_completed_at")]
    public DateTimeOffset? ExternalRunnerCompletedAt { get; set; }

    [JsonIgnore]
    public bool IsFinal => JobFinalStatuses.All.Contains(Status);
}

public sealed class JobStore
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private readonly string _directory;
    private readonly int _maxRecords;
    private readonly TimeSpan _maxAge;

    public JobStore(string directory, int maxRecords = 500, TimeSpan? maxAge = null)
    {
        _directory = directory;
        _maxRecords = maxRecords;
        _maxAge = maxAge ?? TimeSpan.FromDays(14);
    }

    public string DirectoryPath => _directory;

    public JobStateRecord? Load(string jobId)
    {
        if (string.IsNullOrWhiteSpace(jobId))
        {
            return null;
        }
        string path = PathFor(jobId);
        if (!File.Exists(path))
        {
            return null;
        }
        try
        {
            JobStateRecord record = JsonSerializer.Deserialize<JobStateRecord>(File.ReadAllText(path), JsonOptions)
                ?? throw new InvalidOperationException("Job state JSON is empty.");
            Validate(record);
            return record;
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException("Job state is invalid.", ex);
        }
    }

    public void Save(JobStateRecord record)
    {
        Validate(record);
        Directory.CreateDirectory(_directory);
        string path = PathFor(record.JobId);
        NightOwlFileStore.WriteAllText(path, JsonSerializer.Serialize(record, JsonOptions));
    }

    public JobStateRecord Mark(string jobId, string jobType, string status, int attempt, string correlationId = "", string errorCode = "", string errorMessage = "")
    {
        JobStateRecord record = Load(jobId) ?? new JobStateRecord
        {
            JobId = jobId,
            JobType = jobType,
            CorrelationId = correlationId,
            Attempt = attempt,
            CreatedAt = DateTimeOffset.UtcNow
        };
        record.JobType = string.IsNullOrWhiteSpace(record.JobType) ? jobType : record.JobType;
        record.CorrelationId = string.IsNullOrWhiteSpace(record.CorrelationId) ? correlationId : record.CorrelationId;
        record.Attempt = attempt <= 0 ? record.Attempt : attempt;
        record.Status = status;
        record.ErrorCode = errorCode;
        record.ErrorMessage = errorMessage;
        record.UpdatedAt = DateTimeOffset.UtcNow;
        if (JobFinalStatuses.All.Contains(status))
        {
            record.FinalAt = record.UpdatedAt;
        }
        Save(record);
        return record;
    }

    public void MarkFinal(RemoteJobResult result, string correlationId = "")
    {
        JobStateRecord record = Load(result.JobId) ?? new JobStateRecord
        {
            JobId = result.JobId,
            JobType = result.JobType,
            CorrelationId = correlationId,
            CreatedAt = result.StartedAt == default ? DateTimeOffset.UtcNow : result.StartedAt
        };
        record.Status = result.Status;
        record.Attempt = result.Attempt;
        record.ErrorCode = result.ErrorCode;
        record.ErrorMessage = result.ErrorMessage;
        record.Result = result;
        record.UpdatedAt = DateTimeOffset.UtcNow;
        if (JobFinalStatuses.All.Contains(result.Status))
        {
            record.ExternalRunnerActive = false;
            record.ExternalRunnerCompletedAt = DateTimeOffset.UtcNow;
            record.FinalAt = record.UpdatedAt;
        }
        Save(record);
    }

    public JobStateRecord MarkExternalRunnerStarted(string jobId, string jobType, string runnerPath, int timeoutSeconds)
    {
        JobStateRecord record = Load(jobId) ?? new JobStateRecord
        {
            JobId = jobId,
            JobType = jobType,
            CreatedAt = DateTimeOffset.UtcNow
        };
        record.JobType = string.IsNullOrWhiteSpace(record.JobType) ? jobType : record.JobType;
        record.ExternalRunnerActive = true;
        record.ExternalRunnerStartedAt = DateTimeOffset.UtcNow;
        record.ExternalRunnerTimeoutSeconds = Math.Clamp(timeoutSeconds, 60, 3600);
        record.ExternalRunnerPath = runnerPath;
        record.ExternalRunnerCompletedAt = null;
        record.UpdatedAt = DateTimeOffset.UtcNow;
        Save(record);
        return record;
    }

    public void Prune()
    {
        if (!Directory.Exists(_directory))
        {
            return;
        }
        DateTimeOffset cutoff = DateTimeOffset.UtcNow.Subtract(_maxAge);
        FileInfo[] files = new DirectoryInfo(_directory)
            .GetFiles("*.json", SearchOption.TopDirectoryOnly)
            .OrderByDescending(file => file.LastWriteTimeUtc)
            .ToArray();
        for (int i = 0; i < files.Length; i++)
        {
            bool tooMany = i >= _maxRecords;
            bool tooOld = files[i].LastWriteTimeUtc < cutoff.UtcDateTime;
            if (tooMany || tooOld)
            {
                try { files[i].Delete(); } catch { }
            }
        }
    }

    public IReadOnlyList<JobStateRecord> LoadAll()
    {
        if (!Directory.Exists(_directory))
        {
            return Array.Empty<JobStateRecord>();
        }

        List<JobStateRecord> records = new();
        foreach (string path in Directory.GetFiles(_directory, "*.json", SearchOption.TopDirectoryOnly))
        {
            try
            {
                JobStateRecord record = JsonSerializer.Deserialize<JobStateRecord>(File.ReadAllText(path), JsonOptions)
                    ?? throw new InvalidOperationException("Job state JSON is empty.");
                Validate(record);
                records.Add(record);
            }
            catch
            {
                // Corrupt job-state files are handled by the policy/coordinator paths that need them.
            }
        }
        return records;
    }

    public string PathFor(string jobId)
    {
        string safe = jobId.Replace("/", "_").Replace("\\", "_").Replace(":", "_");
        return Path.Combine(_directory, $"{safe}.json");
    }

    public static void Validate(JobStateRecord record)
    {
        if (string.IsNullOrWhiteSpace(record.JobId))
        {
            throw new InvalidOperationException("job_id is required.");
        }
        if (string.IsNullOrWhiteSpace(record.Status))
        {
            throw new InvalidOperationException("status is required.");
        }
    }
}

public sealed class PendingResultRecord
{
    [JsonPropertyName("result_id")]
    public string ResultId { get; set; } = Guid.NewGuid().ToString("D");

    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = "";

    [JsonPropertyName("job_type")]
    public string JobType { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("payload")]
    public JsonElement Payload { get; set; }

    [JsonPropertyName("created_at")]
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("last_attempt_at")]
    public DateTimeOffset? LastAttemptAt { get; set; }

    [JsonPropertyName("attempt_count")]
    public int AttemptCount { get; set; }

    [JsonPropertyName("next_attempt_at")]
    public DateTimeOffset NextAttemptAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("last_error_code")]
    public string LastErrorCode { get; set; } = "";

    [JsonPropertyName("last_error_message")]
    public string LastErrorMessage { get; set; } = "";

    [JsonPropertyName("payload_sha256")]
    public string PayloadSha256 { get; set; } = "";

    [JsonPropertyName("critical")]
    public bool Critical { get; set; }
}

public sealed class PendingResultQueue
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private static readonly TimeSpan[] BackoffSchedule =
    {
        TimeSpan.FromSeconds(5),
        TimeSpan.FromSeconds(15),
        TimeSpan.FromSeconds(30),
        TimeSpan.FromMinutes(1),
        TimeSpan.FromMinutes(5)
    };

    private readonly string _directory;
    private readonly int _maxRecords;
    private readonly long _maxTotalBytes;
    private readonly int _maxPayloadBytes;
    private readonly TimeSpan _retention;
    private readonly List<PendingResultQuarantineEvent> _quarantineEvents = new();

    public PendingResultQueue(
        string directory,
        int maxRecords = 1000,
        long maxTotalBytes = 128L * 1024L * 1024L,
        int maxPayloadBytes = 256 * 1024,
        TimeSpan? retention = null)
    {
        _directory = directory;
        _maxRecords = maxRecords;
        _maxTotalBytes = maxTotalBytes;
        _maxPayloadBytes = maxPayloadBytes;
        _retention = retention ?? TimeSpan.FromDays(14);
    }

    public string DirectoryPath => _directory;
    public string SentDirectory => Path.Combine(_directory, "sent");
    public string QuarantineDirectory => Path.Combine(_directory, "quarantine");

    public IReadOnlyList<PendingResultQuarantineEvent> DrainQuarantineEvents()
    {
        PendingResultQuarantineEvent[] events = _quarantineEvents.ToArray();
        _quarantineEvents.Clear();
        return events;
    }

    public PendingResultRecord Enqueue<TPayload>(string jobType, TPayload payload, bool critical = false, string? resultId = null)
    {
        Directory.CreateDirectory(_directory);
        JsonElement element = JsonSerializer.SerializeToElement(payload, JsonOptions);
        byte[] payloadBytes = JsonSerializer.SerializeToUtf8Bytes(element, JsonOptions);
        if (payloadBytes.Length > _maxPayloadBytes)
        {
            throw new InvalidOperationException(JobErrorCodes.ResultPayloadInvalid);
        }

        string jobId = GetJsonString(element, "job_id") ?? GetJsonString(element, "jobId") ?? "";
        string status = GetJsonString(element, "status") ?? "";
        PendingResultRecord record = new()
        {
            ResultId = string.IsNullOrWhiteSpace(resultId) ? Guid.NewGuid().ToString("D") : resultId,
            JobId = jobId,
            JobType = jobType,
            Status = status,
            Payload = element,
            CreatedAt = DateTimeOffset.UtcNow,
            NextAttemptAt = DateTimeOffset.UtcNow,
            PayloadSha256 = Sha256Hex(payloadBytes),
            Critical = critical
        };
        EnforceLimits(record.Critical);
        Save(record);
        return record;
    }

    public IReadOnlyList<PendingResultRecord> ListDue(DateTimeOffset now)
    {
        return LoadAll(now, dueOnly: true);
    }

    public IReadOnlyList<PendingResultRecord> LoadAll(DateTimeOffset? now = null, bool dueOnly = false)
    {
        if (!Directory.Exists(_directory))
        {
            return Array.Empty<PendingResultRecord>();
        }

        List<PendingResultRecord> records = new();
        foreach (string path in Directory.GetFiles(_directory, "*.json", SearchOption.TopDirectoryOnly))
        {
            try
            {
                PendingResultRecord record = JsonSerializer.Deserialize<PendingResultRecord>(File.ReadAllText(path), JsonOptions)
                    ?? throw new InvalidOperationException("Pending result JSON is empty.");
                Validate(record);
                if (!dueOnly || record.NextAttemptAt <= (now ?? DateTimeOffset.UtcNow))
                {
                    records.Add(record);
                }
            }
            catch (Exception ex)
            {
                Quarantine(path, ex.Message);
            }
        }
        return records.OrderBy(record => record.CreatedAt).ToArray();
    }

    public void MarkAttemptFailed(PendingResultRecord record, string errorCode, string errorMessage)
    {
        record.AttemptCount++;
        record.LastAttemptAt = DateTimeOffset.UtcNow;
        record.LastErrorCode = errorCode;
        record.LastErrorMessage = errorMessage.Length > 1000 ? errorMessage[..1000] : errorMessage;
        TimeSpan delay = BackoffSchedule[Math.Min(record.AttemptCount - 1, BackoffSchedule.Length - 1)];
        if (record.AttemptCount > BackoffSchedule.Length)
        {
            delay = TimeSpan.FromMinutes(Math.Min(15, 5 + record.AttemptCount));
        }
        int jitter = Random.Shared.Next(0, Math.Max(1, (int)Math.Min(delay.TotalSeconds / 4, 30)));
        record.NextAttemptAt = DateTimeOffset.UtcNow.Add(delay).Add(TimeSpan.FromSeconds(jitter));
        Save(record);
    }

    public void MarkSent(PendingResultRecord record)
    {
        Directory.CreateDirectory(SentDirectory);
        string source = PathFor(record.ResultId);
        if (!File.Exists(source))
        {
            return;
        }
        string destination = Path.Combine(SentDirectory, $"{record.ResultId}-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}.json");
        File.Move(source, destination, overwrite: true);
    }

    public void Prune()
    {
        EnforceLimits(false);
    }

    private void EnforceLimits(bool incomingCritical)
    {
        Directory.CreateDirectory(_directory);
        FileInfo[] files = new DirectoryInfo(_directory)
            .GetFiles("*.json", SearchOption.TopDirectoryOnly)
            .OrderBy(file => file.CreationTimeUtc)
            .ToArray();

        long totalBytes = files.Sum(file => file.Length);
        DateTimeOffset cutoff = DateTimeOffset.UtcNow.Subtract(_retention);
        foreach (FileInfo file in files)
        {
            if (files.Length <= _maxRecords && totalBytes <= _maxTotalBytes && file.LastWriteTimeUtc >= cutoff.UtcDateTime)
            {
                continue;
            }

            PendingResultRecord? record = TryLoad(file.FullName);
            if (record?.Critical == true)
            {
                continue;
            }

            try
            {
                totalBytes -= file.Length;
                file.Delete();
            }
            catch { }
            files = new DirectoryInfo(_directory).GetFiles("*.json", SearchOption.TopDirectoryOnly).OrderBy(f => f.CreationTimeUtc).ToArray();
        }

        int currentCount = Directory.GetFiles(_directory, "*.json", SearchOption.TopDirectoryOnly).Length;
        long currentBytes = new DirectoryInfo(_directory).GetFiles("*.json", SearchOption.TopDirectoryOnly).Sum(file => file.Length);
        if ((currentCount >= _maxRecords || currentBytes >= _maxTotalBytes) && incomingCritical == false)
        {
            throw new InvalidOperationException(JobErrorCodes.ResultQueueFull);
        }
    }

    private PendingResultRecord? TryLoad(string path)
    {
        try
        {
            return JsonSerializer.Deserialize<PendingResultRecord>(File.ReadAllText(path), JsonOptions);
        }
        catch
        {
            return null;
        }
    }

    private void Save(PendingResultRecord record)
    {
        Validate(record);
        Directory.CreateDirectory(_directory);
        string path = PathFor(record.ResultId);
        NightOwlFileStore.WriteAllText(path, JsonSerializer.Serialize(record, JsonOptions));
    }

    private void Quarantine(string path, string reason)
    {
        try
        {
            Directory.CreateDirectory(QuarantineDirectory);
            string destination = Path.Combine(QuarantineDirectory, $"{Path.GetFileNameWithoutExtension(path)}-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}.json");
            File.Move(path, destination, overwrite: true);
            _quarantineEvents.Add(new PendingResultQuarantineEvent(path, destination, reason));
        }
        catch { }
    }

    private string PathFor(string resultId)
    {
        string safe = resultId.Replace("/", "_").Replace("\\", "_").Replace(":", "_");
        return Path.Combine(_directory, $"{safe}.json");
    }

    private static void Validate(PendingResultRecord record)
    {
        if (string.IsNullOrWhiteSpace(record.ResultId))
        {
            throw new InvalidOperationException("result_id is required.");
        }
        if (record.Payload.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
        {
            throw new InvalidOperationException(JobErrorCodes.ResultPayloadInvalid);
        }
        byte[] payloadBytes = JsonSerializer.SerializeToUtf8Bytes(record.Payload, JsonOptions);
        string actualHash = Sha256Hex(payloadBytes);
        if (!string.IsNullOrWhiteSpace(record.PayloadSha256) && !actualHash.Equals(record.PayloadSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(JobErrorCodes.ResultPayloadInvalid);
        }
        record.PayloadSha256 = actualHash;
    }

    private static string? GetJsonString(JsonElement element, string propertyName)
    {
        return element.ValueKind == JsonValueKind.Object && element.TryGetProperty(propertyName, out JsonElement value)
            ? value.GetString()
            : null;
    }

    private static string Sha256Hex(byte[] bytes)
    {
        byte[] hash = System.Security.Cryptography.SHA256.HashData(bytes);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }
}

public sealed record PendingResultQuarantineEvent(string SourcePath, string DestinationPath, string Reason);
