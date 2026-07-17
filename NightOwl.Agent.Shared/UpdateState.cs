using System.Text.Json;
using System.Text.Json.Serialization;

namespace NightOwl.Agent.Shared;

public static class UpdateStages
{
    public const string Received = "received";
    public const string CheckingVersion = "checking_version";
    public const string Downloading = "downloading";
    public const string Downloaded = "downloaded";
    public const string Validating = "validating";
    public const string Validated = "validated";
    public const string Staging = "staging";
    public const string Staged = "staged";
    public const string StoppingService = "stopping_service";
    public const string ServiceStopped = "service_stopped";
    public const string CreatingBackup = "creating_backup";
    public const string BackupCreated = "backup_created";
    public const string ReplacingFiles = "replacing_files";
    public const string FilesReplaced = "files_replaced";
    public const string StartingService = "starting_service";
    public const string ServiceStarted = "service_started";
    public const string WaitingHealthCheck = "waiting_health_check";
    public const string RollbackRequired = "rollback_required";
    public const string RollbackStarting = "rollback_starting";
    public const string RollbackStoppingService = "rollback_stopping_service";
    public const string RollbackRestoringFiles = "rollback_restoring_files";
    public const string RollbackStartingService = "rollback_starting_service";
    public const string RollbackWaitingHealthCheck = "rollback_waiting_health_check";
    public const string RolledBack = "rolled_back";
    public const string RollbackFailed = "rollback_failed";
    public const string Completed = "completed";
    public const string Failed = "failed";
}

public static class UpdateStatuses
{
    public const string Running = "running";
    public const string Completed = "completed";
    public const string Failed = "failed";
}

public static class UpdateErrorCodes
{
    public const string UpdateAlreadyRunning = "UPDATE_ALREADY_RUNNING";
    public const string UpdateStateInvalid = "UPDATE_STATE_INVALID";
    public const string UpdateDownloadFailed = "UPDATE_DOWNLOAD_FAILED";
    public const string UpdateHashMismatch = "UPDATE_HASH_MISMATCH";
    public const string UpdatePackageInvalid = "UPDATE_PACKAGE_INVALID";
    public const string UpdateServiceStopTimeout = "UPDATE_SERVICE_STOP_TIMEOUT";
    public const string UpdateBackupFailed = "UPDATE_BACKUP_FAILED";
    public const string UpdateFileReplaceFailed = "UPDATE_FILE_REPLACE_FAILED";
    public const string UpdateServiceStartFailed = "UPDATE_SERVICE_START_FAILED";
    public const string UpdateInterrupted = "UPDATE_INTERRUPTED";
    public const string UpdateUnexpectedError = "UPDATE_UNEXPECTED_ERROR";
    public const string UpdateHealthcheckVersionMismatch = "UPDATE_HEALTHCHECK_VERSION_MISMATCH";
    public const string UpdateHealthcheckTimeout = "UPDATE_HEALTHCHECK_TIMEOUT";
    public const string UpdateProcessExitedEarly = "UPDATE_PROCESS_EXITED_EARLY";
    public const string RollbackBackupInvalid = "ROLLBACK_BACKUP_INVALID";
    public const string RollbackServiceStopFailed = "ROLLBACK_SERVICE_STOP_FAILED";
    public const string RollbackFileRestoreFailed = "ROLLBACK_FILE_RESTORE_FAILED";
    public const string RollbackServiceStartFailed = "ROLLBACK_SERVICE_START_FAILED";
    public const string RollbackHealthcheckTimeout = "ROLLBACK_HEALTHCHECK_TIMEOUT";
    public const string RollbackVersionMismatch = "ROLLBACK_VERSION_MISMATCH";
    public const string RollbackFailed = "ROLLBACK_FAILED";
}

public sealed class UpdateState
{
    [JsonPropertyName("update_id")]
    public string UpdateId { get; set; } = Guid.NewGuid().ToString();

    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = "";

    [JsonPropertyName("from_version")]
    public string FromVersion { get; set; } = "";

    [JsonPropertyName("target_version")]
    public string TargetVersion { get; set; } = "";

    [JsonPropertyName("current_stage")]
    public string CurrentStage { get; set; } = UpdateStages.Received;

    [JsonPropertyName("status")]
    public string Status { get; set; } = UpdateStatuses.Running;

    [JsonPropertyName("attempt")]
    public int Attempt { get; set; } = 1;

    [JsonPropertyName("started_at")]
    public DateTimeOffset StartedAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("updated_at")]
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;

    [JsonPropertyName("completed_at")]
    public DateTimeOffset? CompletedAt { get; set; }

    [JsonPropertyName("package_url")]
    public string PackageUrl { get; set; } = "";

    [JsonPropertyName("expected_sha256")]
    public string ExpectedSha256 { get; set; } = "";

    [JsonPropertyName("staging_path")]
    public string StagingPath { get; set; } = "";

    [JsonPropertyName("backup_path")]
    public string BackupPath { get; set; } = "";

    [JsonPropertyName("error_code")]
    public string ErrorCode { get; set; } = "";

    [JsonPropertyName("error_message")]
    public string ErrorMessage { get; set; } = "";

    [JsonPropertyName("rollback_required")]
    public bool RollbackRequired { get; set; }

    [JsonPropertyName("service_started")]
    public bool ServiceStarted { get; set; }

    [JsonPropertyName("health_check_confirmed")]
    public bool HealthCheckConfirmed { get; set; }

    [JsonPropertyName("rollback_started_at")]
    public DateTimeOffset? RollbackStartedAt { get; set; }

    [JsonPropertyName("rollback_completed_at")]
    public DateTimeOffset? RollbackCompletedAt { get; set; }

    [JsonPropertyName("rollback_reason")]
    public string RollbackReason { get; set; } = "";

    [JsonPropertyName("rollback_error_code")]
    public string RollbackErrorCode { get; set; } = "";

    [JsonPropertyName("rollback_error_message")]
    public string RollbackErrorMessage { get; set; } = "";

    [JsonPropertyName("rollback_attempt")]
    public int RollbackAttempt { get; set; }

    [JsonPropertyName("previous_version_confirmed")]
    public bool PreviousVersionConfirmed { get; set; }

    [JsonPropertyName("restored_file_count")]
    public int RestoredFileCount { get; set; }

    [JsonIgnore]
    public bool IsActive => !Status.Equals(UpdateStatuses.Completed, StringComparison.OrdinalIgnoreCase)
        && !Status.Equals(UpdateStatuses.Failed, StringComparison.OrdinalIgnoreCase);

    public static UpdateState Create(string updateId, string jobId, string fromVersion, string targetVersion)
    {
        DateTimeOffset now = DateTimeOffset.UtcNow;
        return new UpdateState
        {
            UpdateId = string.IsNullOrWhiteSpace(updateId) ? Guid.NewGuid().ToString() : updateId,
            JobId = jobId ?? "",
            FromVersion = fromVersion ?? "",
            TargetVersion = targetVersion ?? "",
            CurrentStage = UpdateStages.Received,
            Status = UpdateStatuses.Running,
            StartedAt = now,
            UpdatedAt = now
        };
    }

    public void MarkStage(string stage, string status = UpdateStatuses.Running)
    {
        CurrentStage = stage;
        Status = status;
        UpdatedAt = DateTimeOffset.UtcNow;
        if (stage.Equals(UpdateStages.ServiceStarted, StringComparison.OrdinalIgnoreCase))
        {
            ServiceStarted = true;
        }
        if (stage.Equals(UpdateStages.RollbackRequired, StringComparison.OrdinalIgnoreCase))
        {
            RollbackRequired = true;
        }
        if (stage.Equals(UpdateStages.RollbackStarting, StringComparison.OrdinalIgnoreCase))
        {
            RollbackRequired = true;
            RollbackStartedAt ??= UpdatedAt;
        }
        if (stage.Equals(UpdateStages.RolledBack, StringComparison.OrdinalIgnoreCase))
        {
            Status = UpdateStatuses.Failed;
            CurrentStage = UpdateStages.RolledBack;
            PreviousVersionConfirmed = true;
            RollbackCompletedAt = UpdatedAt;
            CompletedAt = UpdatedAt;
        }
        if (stage.Equals(UpdateStages.RollbackFailed, StringComparison.OrdinalIgnoreCase))
        {
            Status = UpdateStatuses.Failed;
            CurrentStage = UpdateStages.RollbackFailed;
            RollbackCompletedAt = UpdatedAt;
            CompletedAt = UpdatedAt;
        }
        if (stage.Equals(UpdateStages.Completed, StringComparison.OrdinalIgnoreCase))
        {
            Status = UpdateStatuses.Completed;
            CurrentStage = UpdateStages.Completed;
            HealthCheckConfirmed = true;
            CompletedAt = UpdatedAt;
        }
        if (stage.Equals(UpdateStages.Failed, StringComparison.OrdinalIgnoreCase))
        {
            Status = UpdateStatuses.Failed;
            CurrentStage = UpdateStages.Failed;
            CompletedAt = UpdatedAt;
        }
    }

    public void MarkFailed(string errorCode, string errorMessage, bool rollbackRequired = false)
    {
        ErrorCode = string.IsNullOrWhiteSpace(errorCode) ? UpdateErrorCodes.UpdateUnexpectedError : errorCode;
        ErrorMessage = errorMessage ?? "";
        RollbackRequired = rollbackRequired;
        MarkStage(UpdateStages.Failed, UpdateStatuses.Failed);
    }

    public void MarkRollbackRequired(string reason, string originalErrorCode, string originalErrorMessage)
    {
        RollbackRequired = true;
        RollbackReason = reason ?? "";
        ErrorCode = string.IsNullOrWhiteSpace(originalErrorCode) ? ErrorCode : originalErrorCode;
        ErrorMessage = string.IsNullOrWhiteSpace(originalErrorMessage) ? ErrorMessage : originalErrorMessage;
        MarkStage(UpdateStages.RollbackRequired, UpdateStatuses.Running);
    }

    public void MarkRollbackFailed(string errorCode, string errorMessage)
    {
        RollbackErrorCode = string.IsNullOrWhiteSpace(errorCode) ? UpdateErrorCodes.RollbackFailed : errorCode;
        RollbackErrorMessage = errorMessage ?? "";
        RollbackRequired = true;
        MarkStage(UpdateStages.RollbackFailed, UpdateStatuses.Failed);
    }
}

public sealed class UpdateStateStore
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    public string Path { get; }

    public UpdateStateStore(string path)
    {
        Path = path;
    }

    public UpdateState? Load()
    {
        if (!File.Exists(Path))
        {
            return null;
        }

        try
        {
            string json = File.ReadAllText(Path);
            UpdateState state = JsonSerializer.Deserialize<UpdateState>(json, JsonOptions)
                ?? throw new InvalidOperationException("Update state JSON is empty.");
            Validate(state);
            return state;
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException("Update state is invalid.", ex);
        }
    }

    public bool TryLoad(out UpdateState? state, out string error)
    {
        try
        {
            state = Load();
            error = "";
            return true;
        }
        catch (Exception ex)
        {
            state = null;
            error = ex.Message;
            return false;
        }
    }

    public void Save(UpdateState state)
    {
        Validate(state);
        string? directory = System.IO.Path.GetDirectoryName(Path);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        string tempPath = System.IO.Path.Combine(directory ?? ".", $".{System.IO.Path.GetFileName(Path)}.{Guid.NewGuid():N}.tmp");
        string json = JsonSerializer.Serialize(state, JsonOptions);
        File.WriteAllText(tempPath, json);
        if (File.Exists(Path))
        {
            File.Replace(tempPath, Path, null);
        }
        else
        {
            File.Move(tempPath, Path);
        }
    }

    public static void Validate(UpdateState state)
    {
        if (string.IsNullOrWhiteSpace(state.UpdateId))
        {
            throw new InvalidOperationException("update_id is required.");
        }
        if (string.IsNullOrWhiteSpace(state.CurrentStage))
        {
            throw new InvalidOperationException("current_stage is required.");
        }
        if (string.IsNullOrWhiteSpace(state.Status))
        {
            throw new InvalidOperationException("status is required.");
        }
        if (state.Attempt < 1)
        {
            throw new InvalidOperationException("attempt must be >= 1.");
        }
        if (state.StartedAt == default)
        {
            throw new InvalidOperationException("started_at is required.");
        }
        if (state.UpdatedAt == default)
        {
            throw new InvalidOperationException("updated_at is required.");
        }
    }
}

public sealed class UpdateStateLock : IDisposable
{
    private readonly Mutex _mutex;
    private bool _hasHandle;

    private UpdateStateLock(Mutex mutex, bool hasHandle)
    {
        _mutex = mutex;
        _hasHandle = hasHandle;
    }

    public static UpdateStateLock TryAcquire()
    {
        Mutex mutex;
        try
        {
            mutex = new Mutex(false, @"Global\NightOwl.Agent.Update");
        }
        catch
        {
            mutex = new Mutex(false, "NightOwl.Agent.Update");
        }

        bool acquired = false;
        try
        {
            acquired = mutex.WaitOne(TimeSpan.Zero);
        }
        catch (AbandonedMutexException)
        {
            acquired = true;
        }

        return new UpdateStateLock(mutex, acquired);
    }

    public bool Acquired => _hasHandle;

    public void Dispose()
    {
        if (_hasHandle)
        {
            try
            {
                _mutex.ReleaseMutex();
            }
            catch
            {
                // Best effort only.
            }
            _hasHandle = false;
        }
        _mutex.Dispose();
    }
}
