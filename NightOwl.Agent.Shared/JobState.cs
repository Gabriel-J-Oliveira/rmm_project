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

    public static readonly HashSet<string> All = new(StringComparer.OrdinalIgnoreCase)
    {
        Completed,
        Failed,
        TimedOut,
        Expired,
        Duplicate,
        Unsupported,
        InvalidParameters,
        Cancelled
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
        string temp = Path.Combine(_directory, $".{record.JobId}.{Guid.NewGuid():N}.tmp");
        File.WriteAllText(temp, JsonSerializer.Serialize(record, JsonOptions));
        if (File.Exists(path))
        {
            File.Replace(temp, path, null);
        }
        else
        {
            File.Move(temp, path);
        }
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
        record.FinalAt = record.UpdatedAt;
        Save(record);
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

    private string PathFor(string jobId)
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
