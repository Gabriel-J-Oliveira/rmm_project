using System.Text.Json;
using System.Security.Cryptography;
using System.Security.AccessControl;
using System.Security.Principal;
using NightOwl.Agent.Shared;

string root = Path.Combine(Path.GetTempPath(), "NightOwlPathsTests", Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
Environment.SetEnvironmentVariable("NIGHTOWL_HOME", root);

try
{
    NightOwlPaths paths = NightOwlPaths.FromEnvironment();
    Require(NightOwlPaths.SystemSid == "S-1-5-18", "SYSTEM SID constant mismatch.");
    Require(NightOwlPaths.AdministratorsSid == "S-1-5-32-544", "Builtin Administrators SID constant mismatch.");
    Require(NightOwlPaths.UsersSid == "S-1-5-32-545", "Builtin Users SID constant mismatch.");
    Require(NightOwlPaths.EveryoneSid == "S-1-1-0", "Everyone SID constant mismatch.");
    Require(NightOwlPaths.AuthenticatedUsersSid == "S-1-5-11", "Authenticated Users SID constant mismatch.");
    IReadOnlyList<NightOwlAclPolicy> aclPolicies = paths.GetAclPolicies();
    Require(aclPolicies.Count > 0, "ACL policy list should not be empty.");
    Require(aclPolicies.Any(policy => SamePath(policy.Path, paths.Root)), "Root directory should have explicit ACL policy.");
    Require(aclPolicies.Any(policy => SamePath(policy.Path, paths.InstallDir)), "Install directory should have explicit ACL policy.");
    foreach (string required in paths.RequiredDirectories)
    {
        Require(aclPolicies.Any(policy => SamePath(policy.Path, required)), $"Required directory missing ACL policy: {required}");
    }
    foreach (NightOwlAclPolicy policy in aclPolicies)
    {
        string joined = string.Join("|", policy.Grants);
        Require(joined.Contains("*S-1-5-18:", StringComparison.Ordinal), $"ACL policy {policy.Scope} missing SYSTEM SID.");
        Require(joined.Contains("*S-1-5-32-544:", StringComparison.Ordinal), $"ACL policy {policy.Scope} missing Administrators SID.");
        Require(!joined.Contains("Administrators", StringComparison.OrdinalIgnoreCase), $"ACL policy {policy.Scope} should not use localized Administrators name.");
        Require(!joined.Contains("Administradores", StringComparison.OrdinalIgnoreCase), $"ACL policy {policy.Scope} should not use Portuguese Administrators name.");
        Require(!joined.Contains("Users", StringComparison.OrdinalIgnoreCase), $"ACL policy {policy.Scope} should not use localized Users name.");
        Require(!joined.Contains("Usuários", StringComparison.OrdinalIgnoreCase), $"ACL policy {policy.Scope} should not use Portuguese Users name.");
    }
    foreach (string sensitive in new[] { paths.UpdatesDir, paths.UpdatesDownloadsDir, paths.UpdatesStagingDir, paths.UpdatesBackupDir, paths.UpdatesPendingDir, paths.UpdatesRunnerDir, paths.TrustBackupsDir, paths.TrustDownloadsDir, paths.PackagesDir, paths.CacheDir, paths.DiagnosticsDir })
    {
        NightOwlAclPolicy policy = aclPolicies.Single(item => SamePath(item.Path, sensitive));
        Require(!PolicyGrantsUsers(policy), $"Sensitive directory should not grant Users: {sensitive}");
    }
    foreach (string usersReadPath in new[] { paths.Root, paths.InstallDir, paths.ConfigDir, paths.IdentityDir, paths.StateDir, paths.PendingResultsDir, paths.TrustDir, paths.LogsDir })
    {
        NightOwlAclPolicy policy = aclPolicies.Single(item => SamePath(item.Path, usersReadPath));
        Require(PolicyGrantsUsersReadOnly(policy), $"Users-read directory should grant Users RX only: {usersReadPath}");
    }
    Require(aclPolicies.Single(policy => SamePath(policy.Path, paths.InstallDir)).NormalizeChildren, "AgentDotNet ACL policy should normalize existing children.");
    Require(aclPolicies.Single(policy => SamePath(policy.Path, paths.UpdatesDir)).NormalizeChildren, "Updates ACL policy should normalize existing children.");
    Require(aclPolicies.Single(policy => SamePath(policy.Path, paths.TrustDir)).NormalizeChildren, "Trust ACL policy should normalize existing children.");
    Require(NightOwlPaths.ShouldSkipAclTraversal(FileAttributes.ReparsePoint), "Reparse points should be skipped during ACL traversal.");
    Require(!NightOwlPaths.ShouldSkipAclTraversal(FileAttributes.Archive), "Normal files should not be skipped during ACL traversal.");

    if (OperatingSystem.IsWindows() && IsProcessElevated())
    {
        string aclRoot = Path.Combine(root, "acl-repair");
        NightOwlPaths aclPaths = new(aclRoot);
        Directory.CreateDirectory(aclPaths.InstallDir);
        Directory.CreateDirectory(aclPaths.UpdatesRunnerDir);
        Directory.CreateDirectory(aclPaths.TrustBackupsDir);
        string agentDll = Path.Combine(aclPaths.InstallDir, "NightOwl.Agent.Windows.dll");
        string runnerExe = Path.Combine(aclPaths.UpdatesRunnerDir, "NightOwl.Agent.Updater.exe");
        string trustBundle = Path.Combine(aclPaths.TrustDir, "release-public-keys.json");
        string trustBackup = Path.Combine(aclPaths.TrustBackupsDir, "release-public-keys.old.json");
        File.WriteAllText(agentDll, "agent");
        File.WriteAllText(runnerExe, "runner");
        File.WriteAllText(trustBundle, "{}");
        File.WriteAllText(trustBackup, "{}");
        AddUsersModify(agentDll);
        AddUsersModify(runnerExe);
        AddUsersModify(trustBundle);
        AddUsersModify(trustBackup);

        aclPaths.Bootstrap("test-acl", applyAcl: true);
        Require(!FileAllowsUsersWrite(agentDll), "AgentDotNet file should not preserve Users write/modify.");
        Require(FileAllowsUsersReadExecute(agentDll), "AgentDotNet file should keep Users read/execute.");
        Require(!FileHasUsersAllow(runnerExe), "Updates runner file should not grant Users.");
        Require(!FileAllowsUsersWrite(trustBundle), "Trust root file should not preserve Users write/modify.");
        Require(FileAllowsUsersReadExecute(trustBundle), "Trust root file should keep Users read/execute.");
        Require(!FileHasUsersAllow(trustBackup), "Trust backup file should not grant Users.");

        aclPaths.Bootstrap("test-acl", applyAcl: true);
        Require(!FileAllowsUsersWrite(agentDll), "Second ACL repair should remain idempotent for AgentDotNet.");
        Require(!FileHasUsersAllow(runnerExe), "Second ACL repair should remain idempotent for Updates runner.");
    }

    Directory.CreateDirectory(paths.InstallDir);
    File.WriteAllText(paths.LegacyConfigPath, """
    {
      "agentToken": "token-test",
      "machineId": "machine-test",
      "serverBaseUrl": "https://nightowl.example",
      "heartbeatUrl": "https://nightowl.example/api/agent/heartbeat/"
    }
    """);
    File.WriteAllText(paths.LegacyStatePath, """
    {
      "machine_id": "machine-test",
      "lastHeartbeatAt": "2026-07-17T10:00:00Z"
    }
    """);

    paths.Bootstrap("test", applyAcl: false);
    Require(File.Exists(paths.ConfigPath), "Config was not migrated.");
    Require(File.Exists(paths.StatePath), "State was not migrated.");
    Require(File.Exists(paths.IdentityPath), "Identity was not prepared.");
    Require(Directory.Exists(paths.PendingResultsDir), "Pending results directory was not created.");

    string firstConfig = File.ReadAllText(paths.ConfigPath);
    string firstState = File.ReadAllText(paths.StatePath);
    string firstIdentity = File.ReadAllText(paths.IdentityPath);
    paths.Bootstrap("test", applyAcl: false);
    Require(firstConfig == File.ReadAllText(paths.ConfigPath), "Second bootstrap changed config.");
    Require(firstState == File.ReadAllText(paths.StatePath), "Second bootstrap changed state.");
    Require(firstIdentity == File.ReadAllText(paths.IdentityPath), "Second bootstrap changed identity.");

    using JsonDocument identity = JsonDocument.Parse(firstIdentity);
    Require(identity.RootElement.GetProperty("machine_id").GetString() == "machine-test", "Identity machine_id was not preserved.");

    string safeStorePath = Path.Combine(paths.StateDir, "safe-store-test.json");
    NightOwlFileStore.WriteAllText(safeStorePath, "{\"value\":1}");
    Require(File.ReadAllText(safeStorePath).Contains("\"value\":1", StringComparison.Ordinal), "Safe file store first write failed.");
    RequireNoSafeFileTemps(safeStorePath, "Safe file store first write should not leave temp files.");
    NightOwlFileStore.WriteAllText(safeStorePath, "{\"value\":2}");
    Require(File.ReadAllText(safeStorePath).Contains("\"value\":2", StringComparison.Ordinal), "Safe file store overwrite failed.");
    RequireNoSafeFileTemps(safeStorePath, "Safe file store overwrite should not leave temp files.");
    if (OperatingSystem.IsWindows())
    {
        File.SetAttributes(safeStorePath, File.GetAttributes(safeStorePath) | FileAttributes.ReadOnly);
        bool failedOverReadOnly = false;
        try
        {
            NightOwlFileStore.WriteAllText(safeStorePath, "{\"value\":3}");
        }
        catch
        {
            failedOverReadOnly = true;
        }
        finally
        {
            File.SetAttributes(safeStorePath, File.GetAttributes(safeStorePath) & ~FileAttributes.ReadOnly);
        }
        Require(failedOverReadOnly, "Safe file store should report overwrite failure for read-only destination.");
        Require(File.ReadAllText(safeStorePath).Contains("\"value\":2", StringComparison.Ordinal), "Safe file store failure should preserve previous content.");
        RequireNoSafeFileTemps(safeStorePath, "Safe file store failure should clean temp files.");
    }

    string updateStatePath = paths.UpdateStatePath;
    UpdateStateStore store = new(updateStatePath);
    UpdateState state = UpdateState.Create("update-test", "job-test", "0.1.0.7", "0.1.0.8");
    state.PackageUrl = "https://nightowl.example/downloads/nightowl-agent/NightOwl.Agent.Windows.zip";
    state.ExpectedSha256 = new string('a', 64);
    store.Save(state);
    Require(File.Exists(updateStatePath), "Update state was not written.");
    RequireNoUpdateStateTemps(updateStatePath, "Initial update state save should not leave temp files.");

    UpdateState loaded = store.Load() ?? throw new InvalidOperationException("Update state was not loaded.");
    Require(loaded.UpdateId == "update-test", "Update ID was not preserved.");
    Require(loaded.JobId == "job-test", "Job ID was not preserved.");
    Require(loaded.CurrentStage == UpdateStages.Received, "Initial update stage mismatch.");
    Require(loaded.IsActive, "New update state must be active.");

    foreach (string stage in new[] { UpdateStages.Downloading, UpdateStages.Downloaded, UpdateStages.Validating, UpdateStages.Validated, UpdateStages.Staging })
    {
        loaded.MarkStage(stage);
        store.Save(loaded);
        RequireNoUpdateStateTemps(updateStatePath, $"Update state transition to {stage} should not leave temp files.");
        UpdateState reloadedTransition = store.Load() ?? throw new InvalidOperationException($"{stage} state was not loaded.");
        Require(reloadedTransition.CurrentStage == stage, $"Update transition to {stage} was not persisted.");
        Require(reloadedTransition.UpdateId == "update-test", $"Update ID changed after {stage} transition.");
        Require(reloadedTransition.JobId == "job-test", $"Job ID changed after {stage} transition.");
        loaded = reloadedTransition;
    }

    string rawJson = File.ReadAllText(updateStatePath);
    Require(rawJson.TrimStart().StartsWith("{"), "Atomic write produced invalid JSON prefix.");
    Require(rawJson.Contains("\"update_id\"", StringComparison.Ordinal), "Update state JSON does not use snake_case.");

    File.WriteAllText(updateStatePath, "{ invalid json");
    Require(!store.TryLoad(out _, out string invalidError), "Invalid update state should fail TryLoad.");
    Require(invalidError.Length > 0, "Invalid state error should be reported.");

    state.MarkStage(UpdateStages.WaitingHealthCheck);
    store.Save(state);
    RequireNoUpdateStateTemps(updateStatePath, "Waiting health check update state save should not leave temp files.");
    UpdateState interrupted = store.Load() ?? throw new InvalidOperationException("Interrupted state was not loaded.");
    Require(interrupted.IsActive, "Waiting health check should be treated as incomplete.");
    Require(interrupted.CurrentStage == UpdateStages.WaitingHealthCheck, "Interrupted stage was not preserved.");

    interrupted.MarkStage(UpdateStages.Completed, UpdateStatuses.Completed);
    store.Save(interrupted);
    RequireNoUpdateStateTemps(updateStatePath, "Completed update state save should not leave temp files.");
    UpdateState completed = store.Load() ?? throw new InvalidOperationException("Completed state was not loaded.");
    Require(!completed.IsActive, "Completed update state should not be active.");
    Require(completed.HealthCheckConfirmed, "Completed update must have health check confirmed.");

    UpdateState mismatch = UpdateState.Create("update-mismatch", "job-mismatch", "0.1.0.7", "0.1.0.8");
    mismatch.MarkStage(UpdateStages.WaitingHealthCheck);
    mismatch.MarkFailed(UpdateErrorCodes.UpdateHealthcheckVersionMismatch, "Running version mismatch.");
    store.Save(mismatch);
    UpdateState mismatchLoaded = store.Load() ?? throw new InvalidOperationException("Mismatch state was not loaded.");
    Require(!mismatchLoaded.IsActive, "Version mismatch should close the active update attempt.");
    Require(mismatchLoaded.ErrorCode == UpdateErrorCodes.UpdateHealthcheckVersionMismatch, "Mismatch error code was not preserved.");
    Require(mismatchLoaded.UpdateId == "update-mismatch", "Mismatch update_id was not preserved.");
    Require(mismatchLoaded.JobId == "job-mismatch", "Mismatch job_id was not preserved.");

    using UpdateStateLock firstLock = UpdateStateLock.TryAcquire();
    Require(firstLock.Acquired, "First update mutex acquisition failed.");
    bool secondAcquired = await Task.Run(() =>
    {
        using UpdateStateLock secondLock = UpdateStateLock.TryAcquire();
        return secondLock.Acquired;
    });
    Require(!secondAcquired, "Second update mutex acquisition should be blocked.");

    string jobsDir = Path.Combine(paths.StateDir, "jobs");
    JobStore jobStore = new(jobsDir, maxRecords: 2, maxAge: TimeSpan.FromDays(30));
    string jobId = Guid.NewGuid().ToString();
    jobStore.Mark(jobId, "ping", "received", 1, "corr-1");
    JobStateRecord received = jobStore.Load(jobId) ?? throw new InvalidOperationException("Job state was not loaded.");
    Require(received.Status == "received", "Job received transition was not persisted.");
    Require(received.CorrelationId == "corr-1", "Job correlation_id was not preserved.");
    RequireNoJobStoreTemps(jobsDir, "Initial job state save should not leave temp files.");

    string updateJobId = Guid.NewGuid().ToString();
    jobStore.Mark(updateJobId, "update_agent", "validated", 1, "corr-update");
    JobStateRecord validatedUpdate = jobStore.Load(updateJobId) ?? throw new InvalidOperationException("Validated update job state was not loaded.");
    Require(validatedUpdate.Status == "validated", "Validated transition was not persisted.");
    Require(validatedUpdate.JobType == "update_agent", "Validated job_type was not preserved.");
    RequireNoJobStoreTemps(jobsDir, "Validated transition should not leave temp files.");

    jobStore.Mark(updateJobId, "update_agent", "running", 1, "corr-update");
    JobStateRecord runningUpdate = jobStore.Load(updateJobId) ?? throw new InvalidOperationException("Running update job state was not loaded.");
    Require(runningUpdate.Status == "running", "Running transition was not persisted.");
    Require(runningUpdate.CorrelationId == "corr-update", "Running correlation_id was not preserved.");
    RequireNoJobStoreTemps(jobsDir, "Running transition should not leave temp files.");

    string repairJobId = Guid.NewGuid().ToString();
    string repairRunnerPath = Path.Combine(paths.UpdatesRunnerDir, "repair-test", "Run-NightOwlAgentRepair.ps1");
    jobStore.Mark(repairJobId, "repair_agent", "running", 1, "corr-repair");
    jobStore.MarkExternalRunnerStarted(repairJobId, "repair_agent", repairRunnerPath, 900);
    RemoteJobResult repairRunningResult = new()
    {
        JobId = repairJobId,
        JobType = "repair_agent",
        Status = "running",
        StartedAt = DateTimeOffset.UtcNow,
        CompletedAt = DateTimeOffset.UtcNow,
        DurationMs = 1,
        Attempt = 1,
        AgentVersion = "0.1.1.0-rc21",
        MachineId = "machine-test",
        Output = new { repair_status = "runner_started" }
    };
    jobStore.MarkFinal(repairRunningResult, "corr-repair");
    JobStateRecord runningRepair = jobStore.Load(repairJobId) ?? throw new InvalidOperationException("Running repair job state was not loaded.");
    Require(runningRepair.Status == "running", "Running external repair state should remain running.");
    Require(runningRepair.ExternalRunnerActive, "Running external repair marker should remain active.");
    Require(runningRepair.ExternalRunnerPath == repairRunnerPath, "External repair runner path should be preserved.");

    RemoteJobResult jobResult = new()
    {
        JobId = jobId,
        JobType = "ping",
        Status = JobFinalStatuses.Completed,
        StartedAt = DateTimeOffset.UtcNow.AddSeconds(-1),
        CompletedAt = DateTimeOffset.UtcNow,
        DurationMs = 1000,
        Attempt = 1,
        AgentVersion = "0.1.0.8",
        MachineId = "machine-test",
        Output = new { ok = true }
    };
    jobStore.MarkFinal(jobResult, "corr-1");
    JobStateRecord completedJob = jobStore.Load(jobId) ?? throw new InvalidOperationException("Completed job was not loaded.");
    Require(completedJob.IsFinal, "Completed job should be final.");
    Require(completedJob.Result?.JobId == jobId, "Completed job result was not preserved.");
    Require(JobFinalStatuses.All.Contains(JobFinalStatuses.RolledBack), "rolled_back should be a final job result status.");
    Require(JobFinalStatuses.All.Contains(JobFinalStatuses.RollbackFailed), "rollback_failed should be a final job result status.");
    RequireNoJobStoreTemps(jobsDir, "Completed transition should not leave temp files.");

    repairRunningResult.Status = JobFinalStatuses.Completed;
    repairRunningResult.CompletedAt = DateTimeOffset.UtcNow;
    jobStore.MarkFinal(repairRunningResult, "corr-repair");
    JobStateRecord completedRepair = jobStore.Load(repairJobId) ?? throw new InvalidOperationException("Completed repair job state was not loaded.");
    Require(completedRepair.IsFinal, "Completed repair job should be final.");
    Require(!completedRepair.ExternalRunnerActive, "Completed repair should clear external runner marker.");

    RemoteJobResult failedUpdateResult = new()
    {
        JobId = updateJobId,
        JobType = "update_agent",
        Status = JobFinalStatuses.Failed,
        StartedAt = DateTimeOffset.UtcNow.AddSeconds(-2),
        CompletedAt = DateTimeOffset.UtcNow,
        DurationMs = 2000,
        Attempt = 1,
        AgentVersion = "0.1.0.8",
        MachineId = "machine-test",
        ErrorCode = JobErrorCodes.JobExecutionFailed,
        ErrorMessage = "synthetic failure",
        Output = new { ok = false }
    };
    jobStore.MarkFinal(failedUpdateResult, "corr-update");
    JobStateRecord failedUpdate = jobStore.Load(updateJobId) ?? throw new InvalidOperationException("Failed update job state was not loaded.");
    Require(failedUpdate.Status == JobFinalStatuses.Failed, "Failed transition was not persisted.");
    Require(failedUpdate.IsFinal, "Failed update job should be final.");
    Require(failedUpdate.ErrorCode == JobErrorCodes.JobExecutionFailed, "Failed update error_code was not preserved.");
    Require(failedUpdate.Result?.JobId == updateJobId, "Failed update result was not preserved.");
    RequireNoJobStoreTemps(jobsDir, "Failed transition should not leave temp files.");

    string failedRawJson = File.ReadAllText(Path.Combine(jobsDir, $"{updateJobId}.json"));
    Require(failedRawJson.TrimStart().StartsWith("{"), "Repeated JobStore writes produced invalid JSON prefix.");
    using JsonDocument failedDocument = JsonDocument.Parse(failedRawJson);
    Require(failedDocument.RootElement.ValueKind == JsonValueKind.Object, "Repeated JobStore writes produced non-object JSON.");

    File.WriteAllText(Path.Combine(jobsDir, $"{Guid.NewGuid()}.json"), "{ invalid json");
    bool invalidThrown = false;
    try
    {
        foreach (string file in Directory.GetFiles(jobsDir, "*.json"))
        {
            string candidate = Path.GetFileNameWithoutExtension(file);
            if (!candidate.Equals(jobId, StringComparison.OrdinalIgnoreCase))
            {
                _ = jobStore.Load(candidate);
            }
        }
    }
    catch
    {
        invalidThrown = true;
    }
    Require(invalidThrown, "Corrupted job state should be detected.");

    jobStore.Mark(Guid.NewGuid().ToString(), "ping", JobFinalStatuses.Completed, 1);
    jobStore.Mark(Guid.NewGuid().ToString(), "ping", JobFinalStatuses.Completed, 1);
    jobStore.Mark(Guid.NewGuid().ToString(), "ping", JobFinalStatuses.Completed, 1);
    jobStore.Prune();
    Require(Directory.GetFiles(jobsDir, "*.json").Length <= 2, "Job retention did not prune old records.");

    string resultQueueDir = Path.Combine(paths.StateDir, "pending-results-test");
    PendingResultQueue resultQueue = new(resultQueueDir, maxRecords: 5, maxTotalBytes: 512 * 1024, maxPayloadBytes: 64 * 1024);
    var resultPayload = new
    {
        job_id = Guid.NewGuid().ToString(),
        status = JobFinalStatuses.Completed,
        started_at = DateTimeOffset.UtcNow.AddSeconds(-1),
        finished_at = DateTimeOffset.UtcNow,
        result = new { type = "ping", success = true }
    };
    PendingResultRecord pending = resultQueue.Enqueue("ping", resultPayload);
    Require(File.Exists(Path.Combine(resultQueueDir, $"{pending.ResultId}.json")), "Pending result was not persisted.");
    RequireNoPendingResultTemps(resultQueueDir, "Pending result enqueue should not leave temp files.");
    Require(resultQueue.ListDue(DateTimeOffset.UtcNow).Count == 1, "Pending result should be due immediately.");
    Require(!string.IsNullOrWhiteSpace(pending.PayloadSha256), "Payload hash was not calculated.");
    PendingResultRecord enqueued = resultQueue.LoadAll().Single(record => record.ResultId == pending.ResultId);
    Require(enqueued.JobId == pending.JobId, "Reloaded pending result job_id mismatch after enqueue.");
    Require(enqueued.JobType == "ping", "Reloaded pending result job_type mismatch after enqueue.");
    Require(enqueued.PayloadSha256 == pending.PayloadSha256, "Reloaded pending result payload hash mismatch after enqueue.");

    resultQueue.MarkAttemptFailed(pending, JobErrorCodes.ResultSendFailed, "backend unavailable");
    RequireNoPendingResultTemps(resultQueueDir, "Pending result resave should not leave temp files.");
    PendingResultRecord retried = resultQueue.LoadAll().Single(record => record.ResultId == pending.ResultId);
    Require(retried.JobId == pending.JobId, "Reloaded pending result job_id mismatch after resave.");
    Require(retried.JobType == pending.JobType, "Reloaded pending result job_type mismatch after resave.");
    Require(retried.PayloadSha256 == pending.PayloadSha256, "Reloaded pending result payload hash mismatch after resave.");
    Require(retried.AttemptCount == 1, "Retry attempt was not recorded.");
    Require(retried.NextAttemptAt > DateTimeOffset.UtcNow, "Retry backoff was not applied.");
    Require(resultQueue.ListDue(DateTimeOffset.UtcNow).Count == 0, "Backoff result should not be due yet.");

    resultQueue.MarkSent(retried);
    Require(resultQueue.LoadAll().Count == 0, "Sent pending result should leave active queue.");
    Require(Directory.GetFiles(resultQueue.SentDirectory, "*.json").Length == 1, "Sent result was not archived.");

    string corruptPath = Path.Combine(resultQueueDir, "corrupt.json");
    File.WriteAllText(corruptPath, "{ invalid json");
    _ = resultQueue.LoadAll();
    Require(!File.Exists(corruptPath), "Corrupted pending result should be moved out of active queue.");
    Require(Directory.GetFiles(resultQueue.QuarantineDirectory, "*.json").Length == 1, "Corrupted pending result was not quarantined.");
    Require(resultQueue.DrainQuarantineEvents().Count == 1, "Quarantine reason should be available for logging.");

    bool queueFull = false;
    PendingResultQueue tinyQueue = new(Path.Combine(paths.StateDir, "pending-results-full"), maxRecords: 1, maxTotalBytes: 512, maxPayloadBytes: 64 * 1024);
    tinyQueue.Enqueue("update_agent", resultPayload, critical: true);
    try
    {
        tinyQueue.Enqueue("ping", new { job_id = Guid.NewGuid().ToString(), status = JobFinalStatuses.Completed });
    }
    catch (InvalidOperationException ex) when (ex.Message == JobErrorCodes.ResultQueueFull)
    {
        queueFull = true;
    }
    Require(queueFull, "Queue full condition should be explicit.");

    string secretText = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz token=secret-value https://nightowl.example/path?agentToken=secret";
    SanitizationResult sanitizedText = NightOwlSanitizer.SanitizeText(secretText);
    Require(sanitizedText.RedactionApplied, "Text sanitizer should redact known secret patterns.");
    Require(!sanitizedText.Value.Contains("abcdefghijklmnopqrstuvwxyz", StringComparison.Ordinal), "Bearer token was not redacted.");
    Require(!sanitizedText.Value.Contains("secret-value", StringComparison.Ordinal), "Key-value token was not redacted.");
    Require(!sanitizedText.Value.Contains("agentToken=secret", StringComparison.Ordinal), "Sensitive URL query was not removed.");

    SanitizationResult sanitizedJson = NightOwlSanitizer.SanitizeJson("""
    {
      "agentToken": "fixture-token-value",
      "machineId": "machine-test",
      "nested": {
        "Authorization": "Bearer nested-secret-token"
      }
    }
    """);
    Require(sanitizedJson.RedactionApplied, "JSON sanitizer should report redaction.");
    Require(!sanitizedJson.Value.Contains("fixture-token-value", StringComparison.Ordinal), "JSON token was not redacted.");
    Require(!sanitizedJson.Value.Contains("nested-secret-token", StringComparison.Ordinal), "Nested authorization token was not redacted.");
    Require(!NightOwlSanitizer.ContainsSecretLikeContent(sanitizedJson.Value), "Sanitized JSON still contains secret-like content.");

    using RSA rootKey = RSA.Create(3072);
    using RSA releaseKey = RSA.Create(3072);
    string rootPublicXml = rootKey.ToXmlString(false);
    string releasePublicXml = releaseKey.ToXmlString(false);
    byte[] trustBundleBytes = JsonSerializer.SerializeToUtf8Bytes(new ReleaseTrustBundle
    {
        SchemaVersion = 1,
        BundleVersion = 2,
        GeneratedAt = DateTimeOffset.UtcNow,
        ValidFrom = DateTimeOffset.UtcNow.AddMinutes(-1),
        ValidUntil = DateTimeOffset.UtcNow.AddDays(7),
        Keys = new()
        {
            new ReleaseTrustKey
            {
                KeyId = "nightowl-release-test-01",
                Algorithm = "RSA-PSS-SHA256",
                PublicKeyXml = releasePublicXml,
                Status = "active"
            }
        }
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web));
    byte[] trustSignature = rootKey.SignData(trustBundleBytes, HashAlgorithmName.SHA256, RSASignaturePadding.Pss);
    ReleaseTrustBundleMetadata trustMetadata = new()
    {
        SchemaVersion = 1,
        BundleVersion = 2,
        BundleSha256 = ReleaseTrustBundleValidator.Sha256Hex(trustBundleBytes),
        SignatureSha256 = ReleaseTrustBundleValidator.Sha256Hex(trustSignature),
        RootKeyId = "nightowl-root-test",
        Size = trustBundleBytes.Length,
        GeneratedAt = DateTimeOffset.UtcNow,
        BundleUrl = "https://nightowl.example/trust/bundles/2/release-public-keys.json",
        SignatureUrl = "https://nightowl.example/trust/bundles/2/release-public-keys.sig",
        MetadataUrl = "https://nightowl.example/trust/bundles/2/release-public-keys.meta.json"
    };
    ReleaseTrustValidationResult trustOk = ReleaseTrustBundleValidator.Validate(
        trustBundleBytes,
        trustSignature,
        trustMetadata,
        new[] { new ReleaseTrustRootKey { KeyId = "nightowl-root-test", PublicKeyXml = rootPublicXml } },
        new ReleaseTrustState { InstalledBundleVersion = 1, InstalledBundleSha256 = "" });
    Require(trustOk.IsValid, $"Valid trust bundle failed validation: {trustOk.ErrorCode} {trustOk.ErrorMessage}");
    ReleaseTrustValidationResult trustBase64Ok = ReleaseTrustBundleValidator.Validate(
        trustBundleBytes,
        System.Text.Encoding.ASCII.GetBytes(Convert.ToBase64String(trustSignature)),
        new ReleaseTrustBundleMetadata
        {
            SchemaVersion = trustMetadata.SchemaVersion,
            BundleVersion = trustMetadata.BundleVersion,
            BundleSha256 = trustMetadata.BundleSha256,
            SignatureSha256 = ReleaseTrustBundleValidator.Sha256Hex(System.Text.Encoding.ASCII.GetBytes(Convert.ToBase64String(trustSignature))),
            RootKeyId = trustMetadata.RootKeyId,
            Size = trustMetadata.Size
        },
        new[] { new ReleaseTrustRootKey { KeyId = "nightowl-root-test", PublicKeyXml = rootPublicXml } });
    Require(trustBase64Ok.IsValid, $"Base64 trust signature failed validation: {trustBase64Ok.ErrorCode} {trustBase64Ok.ErrorMessage}");

    ReleaseTrustBundle normalizedDateBundle = JsonSerializer.Deserialize<ReleaseTrustBundle>("""
    {
      "schema_version": 1,
      "bundle_version": 7,
      "generated_at": "2026-08-11T12:00:00.0000000Z",
      "valid_from": "2026-08-11T12:00:00.0000000Z",
      "valid_until": "2027-08-11T12:00:00.0000000Z",
      "keys": [
        {
          "key_id": "nightowl-release-normalized-date",
          "algorithm": "RSA-PSS-SHA256",
          "public_key_xml": "<RSAKeyValue><Modulus>AA==</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>",
          "status": "active",
          "valid_from": null,
          "valid_until": "2026-01-02T13:04:05.0000000Z",
          "revoked_at": null
        }
      ]
    }
    """, new JsonSerializerOptions(JsonSerializerDefaults.Web)) ?? throw new InvalidOperationException("Normalized trust bundle JSON should deserialize.");
    Require(normalizedDateBundle.Keys[0].ValidFrom is null, "Null valid_from should deserialize as null.");
    Require(normalizedDateBundle.Keys[0].RevokedAt is null, "Null revoked_at should deserialize as null.");
    Require(normalizedDateBundle.Keys[0].ValidUntil?.UtcDateTime == new DateTime(2026, 1, 2, 13, 4, 5, DateTimeKind.Utc), "Normalized valid_until should deserialize as UTC DateTimeOffset.");

    string trustSyncRoot = Path.Combine(root, "trust-sync");
    NightOwlPaths trustSyncPaths = new(trustSyncRoot);
    ReleaseTrustStore trustStore = new(trustSyncPaths, applyAcl: false);
    Dictionary<string, byte[]> trustResponses = new(StringComparer.OrdinalIgnoreCase)
    {
        ["https://nightowl.example/trust/bundles/2/release-public-keys.meta.json"] = JsonSerializer.SerializeToUtf8Bytes(trustMetadata, new JsonSerializerOptions(JsonSerializerDefaults.Web)),
        ["https://nightowl.example/trust/bundles/2/release-public-keys.json"] = trustBundleBytes,
        ["https://nightowl.example/trust/bundles/2/release-public-keys.sig"] = trustSignature
    };
    using HttpClient trustHttp = new(new StaticBytesHandler(trustResponses));
    ReleaseTrustBundleUpdater trustUpdater = new(trustHttp, trustStore, new[] { new ReleaseTrustRootKey { KeyId = "nightowl-root-test", PublicKeyXml = rootPublicXml } });
    ReleaseTrustSyncRequest trustRequest = new()
    {
        MetadataUrl = "https://nightowl.example/trust/bundles/2/release-public-keys.meta.json",
        BundleUrl = "https://nightowl.example/trust/bundles/2/release-public-keys.json",
        SignatureUrl = "https://nightowl.example/trust/bundles/2/release-public-keys.sig",
        ExpectedRootKeyId = "nightowl-root-test",
        ExpectedBundleVersion = 2,
        ExpectedSha256 = trustMetadata.BundleSha256,
        JobId = "trust-job-1"
    };
    ReleaseTrustSyncResult firstTrustSync = await trustUpdater.SyncAsync(trustRequest, CancellationToken.None);
    Require(firstTrustSync.Status == "completed", "First trust sync should complete.");
    Require(firstTrustSync.UpdateStatus == "updated", "First trust sync should install the bundle.");
    ReleaseTrustState installedTrustState = trustStore.LoadState();
    DateTimeOffset? firstInstalledAt = installedTrustState.InstalledAt;
    int firstBackupCount = Directory.Exists(trustSyncPaths.TrustBackupsDir) ? Directory.GetFiles(trustSyncPaths.TrustBackupsDir).Length : 0;

    ReleaseTrustSyncRequest secondTrustRequest = new()
    {
        MetadataUrl = trustRequest.MetadataUrl,
        BundleUrl = trustRequest.BundleUrl,
        SignatureUrl = trustRequest.SignatureUrl,
        ExpectedRootKeyId = trustRequest.ExpectedRootKeyId,
        ExpectedBundleVersion = trustRequest.ExpectedBundleVersion,
        ExpectedSha256 = trustRequest.ExpectedSha256,
        JobId = "trust-job-2"
    };
    ReleaseTrustSyncResult secondTrustSync = await trustUpdater.SyncAsync(secondTrustRequest, CancellationToken.None);
    ReleaseTrustState noUpdateTrustState = trustStore.LoadState();
    int secondBackupCount = Directory.Exists(trustSyncPaths.TrustBackupsDir) ? Directory.GetFiles(trustSyncPaths.TrustBackupsDir).Length : 0;
    Require(secondTrustSync.Status == "completed", "Second identical trust sync should complete.");
    Require(secondTrustSync.UpdateStatus == "no_update", "Second identical trust sync should report no_update.");
    Require(secondBackupCount == firstBackupCount, "No-update trust sync should not create a backup.");
    Require(noUpdateTrustState.InstalledAt == firstInstalledAt, "No-update trust sync should not change installed_at.");
    Require(noUpdateTrustState.InstalledBundleVersion == installedTrustState.InstalledBundleVersion, "No-update trust sync should preserve bundle version.");
    Require(noUpdateTrustState.InstalledBundleSha256 == installedTrustState.InstalledBundleSha256, "No-update trust sync should preserve bundle SHA.");
    Require(noUpdateTrustState.LastJobId == "trust-job-2", "No-update trust sync should update last job id.");

    byte[] tamperedSignature = trustSignature.ToArray();
    tamperedSignature[0] ^= 1;
    ReleaseTrustValidationResult badSignature = ReleaseTrustBundleValidator.Validate(
        trustBundleBytes,
        tamperedSignature,
        trustMetadata,
        new[] { new ReleaseTrustRootKey { KeyId = "nightowl-root-test", PublicKeyXml = rootPublicXml } });
    Require(!badSignature.IsValid && badSignature.ErrorCode == ReleaseTrustErrorCodes.TrustMetadataInvalid,
        "Signature byte tamper should fail metadata hash before signature validation.");

    ReleaseTrustValidationResult unknownRoot = ReleaseTrustBundleValidator.Validate(
        trustBundleBytes,
        trustSignature,
        trustMetadata,
        Array.Empty<ReleaseTrustRootKey>());
    Require(!unknownRoot.IsValid && unknownRoot.ErrorCode == ReleaseTrustErrorCodes.TrustRootUnknown, "Unknown trust root should be blocked.");

    ReleaseTrustValidationResult downgrade = ReleaseTrustBundleValidator.Validate(
        trustBundleBytes,
        trustSignature,
        trustMetadata,
        new[] { new ReleaseTrustRootKey { KeyId = "nightowl-root-test", PublicKeyXml = rootPublicXml } },
        new ReleaseTrustState { InstalledBundleVersion = 3, InstalledBundleSha256 = "" });
    Require(!downgrade.IsValid && downgrade.ErrorCode == ReleaseTrustErrorCodes.TrustBundleDowngrade, "Trust bundle downgrade should be blocked.");

    byte[] divergentSameVersionBytes = JsonSerializer.SerializeToUtf8Bytes(new ReleaseTrustBundle
    {
        SchemaVersion = 1,
        BundleVersion = 2,
        GeneratedAt = DateTimeOffset.UtcNow,
        ValidUntil = DateTimeOffset.UtcNow.AddDays(7),
        Keys = new()
        {
            new ReleaseTrustKey
            {
                KeyId = "nightowl-release-test-02",
                Algorithm = "RSA-PSS-SHA256",
                PublicKeyXml = releasePublicXml,
                Status = "active"
            }
        }
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web));
    byte[] divergentSameVersionSig = rootKey.SignData(divergentSameVersionBytes, HashAlgorithmName.SHA256, RSASignaturePadding.Pss);
    ReleaseTrustValidationResult divergentSameVersion = ReleaseTrustBundleValidator.Validate(
        divergentSameVersionBytes,
        divergentSameVersionSig,
        new ReleaseTrustBundleMetadata
        {
            SchemaVersion = 1,
            BundleVersion = 2,
            BundleSha256 = ReleaseTrustBundleValidator.Sha256Hex(divergentSameVersionBytes),
            SignatureSha256 = ReleaseTrustBundleValidator.Sha256Hex(divergentSameVersionSig),
            RootKeyId = "nightowl-root-test",
            Size = divergentSameVersionBytes.Length
        },
        new[] { new ReleaseTrustRootKey { KeyId = "nightowl-root-test", PublicKeyXml = rootPublicXml } },
        installedTrustState);
    Require(!divergentSameVersion.IsValid && divergentSameVersion.ErrorCode == ReleaseTrustErrorCodes.TrustBundleSameVersionDivergent, "Same trust bundle version with different content should be blocked.");

    byte[] duplicateBundleBytes = JsonSerializer.SerializeToUtf8Bytes(new ReleaseTrustBundle
    {
        SchemaVersion = 1,
        BundleVersion = 4,
        GeneratedAt = DateTimeOffset.UtcNow,
        ValidUntil = DateTimeOffset.UtcNow.AddDays(7),
        Keys = new()
        {
            new ReleaseTrustKey { KeyId = "dup", PublicKeyXml = releasePublicXml, Status = "active" },
            new ReleaseTrustKey { KeyId = "dup", PublicKeyXml = releasePublicXml, Status = "active" }
        }
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web));
    byte[] duplicateSig = rootKey.SignData(duplicateBundleBytes, HashAlgorithmName.SHA256, RSASignaturePadding.Pss);
    ReleaseTrustValidationResult duplicate = ReleaseTrustBundleValidator.Validate(
        duplicateBundleBytes,
        duplicateSig,
        new ReleaseTrustBundleMetadata
        {
            SchemaVersion = 1,
            BundleVersion = 4,
            BundleSha256 = ReleaseTrustBundleValidator.Sha256Hex(duplicateBundleBytes),
            SignatureSha256 = ReleaseTrustBundleValidator.Sha256Hex(duplicateSig),
            RootKeyId = "nightowl-root-test",
            Size = duplicateBundleBytes.Length
        },
        new[] { new ReleaseTrustRootKey { KeyId = "nightowl-root-test", PublicKeyXml = rootPublicXml } });
    Require(!duplicate.IsValid && duplicate.ErrorCode == ReleaseTrustErrorCodes.TrustKeyDuplicate, "Duplicate release key should be blocked.");

    byte[] privateParamBundleBytes = JsonSerializer.SerializeToUtf8Bytes(new ReleaseTrustBundle
    {
        SchemaVersion = 1,
        BundleVersion = 5,
        GeneratedAt = DateTimeOffset.UtcNow,
        ValidUntil = DateTimeOffset.UtcNow.AddDays(7),
        Keys = new() { new ReleaseTrustKey { KeyId = "private", PublicKeyXml = releaseKey.ToXmlString(true), Status = "active" } }
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web));
    byte[] privateParamSig = rootKey.SignData(privateParamBundleBytes, HashAlgorithmName.SHA256, RSASignaturePadding.Pss);
    ReleaseTrustValidationResult privateParam = ReleaseTrustBundleValidator.Validate(
        privateParamBundleBytes,
        privateParamSig,
        new ReleaseTrustBundleMetadata
        {
            SchemaVersion = 1,
            BundleVersion = 5,
            BundleSha256 = ReleaseTrustBundleValidator.Sha256Hex(privateParamBundleBytes),
            SignatureSha256 = ReleaseTrustBundleValidator.Sha256Hex(privateParamSig),
            RootKeyId = "nightowl-root-test",
            Size = privateParamBundleBytes.Length
        },
        new[] { new ReleaseTrustRootKey { KeyId = "nightowl-root-test", PublicKeyXml = rootPublicXml } });
    Require(!privateParam.IsValid && privateParam.ErrorCode == ReleaseTrustErrorCodes.TrustPrivateParameters, "Private RSA parameters should be blocked.");

    File.WriteAllText(paths.LegacyTrustBundlePath, JsonSerializer.Serialize(new { keys = new[] { new { key_id = "legacy", public_key_xml = releasePublicXml, algorithm = "RSA-PSS-SHA256", status = "active" } } }));
    string trustRoot2 = Path.Combine(root, "trust-migration");
    Directory.CreateDirectory(trustRoot2);
    Environment.SetEnvironmentVariable("NIGHTOWL_HOME", trustRoot2);
    NightOwlPaths trustPaths = NightOwlPaths.FromEnvironment();
    Directory.CreateDirectory(trustPaths.InstallDir);
    File.Copy(paths.LegacyTrustBundlePath, trustPaths.LegacyTrustBundlePath);
    File.WriteAllText(Path.Combine(trustPaths.InstallDir, "release-trust-roots.json"), JsonSerializer.Serialize(new
    {
        schema_version = 1,
        roots = new[]
        {
            new
            {
                key_id = "nightowl-root-test",
                algorithm = "RSA-PSS-SHA256",
                public_key_xml = rootPublicXml,
                status = "active"
            }
        }
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web)));
    trustPaths.Bootstrap("test", applyAcl: false);
    Require(File.Exists(trustPaths.TrustBundlePath), "Legacy trust bundle was not migrated to Trust directory.");
    Require(Directory.Exists(trustPaths.TrustBackupsDir), "Trust backups directory was not created.");
    IReadOnlyList<ReleaseTrustRootKey> loadedRoots = ReleaseTrustAnchors.Load(trustPaths);
    Require(loadedRoots.Count == 1, "Agent did not load release-trust-roots.json.");
    Require(loadedRoots[0].KeyId == "nightowl-root-test", "Loaded trust root key_id mismatch.");

    Console.WriteLine("NightOwlPaths migration tests passed.");
}
finally
{
    try
    {
        Directory.Delete(root, recursive: true);
    }
    catch
    {
        // Best-effort cleanup.
    }
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}

static bool SamePath(string left, string right)
{
    return string.Equals(
        Path.GetFullPath(left).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
        Path.GetFullPath(right).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
        StringComparison.OrdinalIgnoreCase);
}

static void RequireNoJobStoreTemps(string jobsDir, string message)
{
    string[] temps = Directory.Exists(jobsDir)
        ? Directory.GetFiles(jobsDir, ".*.tmp", SearchOption.TopDirectoryOnly)
        : Array.Empty<string>();
    Require(temps.Length == 0, $"{message} Found: {string.Join(", ", temps.Select(Path.GetFileName))}");
}

static void RequireNoUpdateStateTemps(string updateStatePath, string message)
{
    string directory = Path.GetDirectoryName(updateStatePath) ?? ".";
    string fileName = Path.GetFileName(updateStatePath);
    string[] temps = Directory.Exists(directory)
        ? Directory.GetFiles(directory, $".{fileName}.*.tmp", SearchOption.TopDirectoryOnly)
        : Array.Empty<string>();
    Require(temps.Length == 0, $"{message} Found: {string.Join(", ", temps.Select(Path.GetFileName))}");
}

static void RequireNoPendingResultTemps(string queueDir, string message)
{
    string[] temps = Directory.Exists(queueDir)
        ? Directory.GetFiles(queueDir, ".*.tmp", SearchOption.TopDirectoryOnly)
        : Array.Empty<string>();
    Require(temps.Length == 0, $"{message} Found: {string.Join(", ", temps.Select(Path.GetFileName))}");
}

static void RequireNoSafeFileTemps(string path, string message)
{
    string directory = Path.GetDirectoryName(path) ?? ".";
    string fileName = Path.GetFileName(path);
    string[] temps = Directory.Exists(directory)
        ? Directory.GetFiles(directory, $".{fileName}.*.tmp", SearchOption.TopDirectoryOnly)
        : Array.Empty<string>();
    Require(temps.Length == 0, $"{message} Found: {string.Join(", ", temps.Select(Path.GetFileName))}");
}

static bool PolicyGrantsUsers(NightOwlAclPolicy policy)
{
    return policy.Grants.Any(grant => grant.Contains("*S-1-5-32-545:", StringComparison.OrdinalIgnoreCase));
}

static bool PolicyGrantsUsersReadOnly(NightOwlAclPolicy policy)
{
    return policy.Grants.Any(grant => grant.Equals("*S-1-5-32-545:(OI)(CI)(RX)", StringComparison.OrdinalIgnoreCase))
        && !policy.Grants.Any(grant => grant.Contains("*S-1-5-32-545:", StringComparison.OrdinalIgnoreCase)
            && !grant.EndsWith("(RX)", StringComparison.OrdinalIgnoreCase));
}

static void AddUsersModify(string path)
{
    FileSecurity security = new FileInfo(path).GetAccessControl();
    security.AddAccessRule(new FileSystemAccessRule(
        new SecurityIdentifier(NightOwlPaths.UsersSid),
        FileSystemRights.Modify,
        AccessControlType.Allow));
    new FileInfo(path).SetAccessControl(security);
}

static bool IsProcessElevated()
{
    if (!OperatingSystem.IsWindows())
    {
        return false;
    }
    using WindowsIdentity identity = WindowsIdentity.GetCurrent();
    WindowsPrincipal principal = new(identity);
    return principal.IsInRole(WindowsBuiltInRole.Administrator);
}

static bool FileHasUsersAllow(string path)
{
    FileSecurity security = new FileInfo(path).GetAccessControl();
    return security.GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier))
        .Cast<FileSystemAccessRule>()
        .Any(rule => rule.AccessControlType == AccessControlType.Allow
            && rule.IdentityReference is SecurityIdentifier sid
            && sid.Value.Equals(NightOwlPaths.UsersSid, StringComparison.OrdinalIgnoreCase));
}

static bool FileAllowsUsersReadExecute(string path)
{
    FileSecurity security = new FileInfo(path).GetAccessControl();
    return security.GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier))
        .Cast<FileSystemAccessRule>()
        .Any(rule => rule.AccessControlType == AccessControlType.Allow
            && rule.IdentityReference is SecurityIdentifier sid
            && sid.Value.Equals(NightOwlPaths.UsersSid, StringComparison.OrdinalIgnoreCase)
            && (rule.FileSystemRights & FileSystemRights.ReadAndExecute) == FileSystemRights.ReadAndExecute);
}

static bool FileAllowsUsersWrite(string path)
{
    FileSecurity security = new FileInfo(path).GetAccessControl();
    return security.GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier))
        .Cast<FileSystemAccessRule>()
        .Any(rule => rule.AccessControlType == AccessControlType.Allow
            && rule.IdentityReference is SecurityIdentifier sid
            && sid.Value.Equals(NightOwlPaths.UsersSid, StringComparison.OrdinalIgnoreCase)
            && GrantsWriteLikeAccess(rule.FileSystemRights));
}

static bool GrantsWriteLikeAccess(FileSystemRights rights)
{
    return (rights & FileSystemRights.FullControl) == FileSystemRights.FullControl
        || (rights & FileSystemRights.Modify) == FileSystemRights.Modify
        || (rights & FileSystemRights.Write) == FileSystemRights.Write
        || (rights & FileSystemRights.Delete) == FileSystemRights.Delete
        || (rights & FileSystemRights.WriteData) == FileSystemRights.WriteData
        || (rights & FileSystemRights.AppendData) == FileSystemRights.AppendData
        || (rights & FileSystemRights.WriteExtendedAttributes) == FileSystemRights.WriteExtendedAttributes
        || (rights & FileSystemRights.WriteAttributes) == FileSystemRights.WriteAttributes
        || (rights & FileSystemRights.ChangePermissions) == FileSystemRights.ChangePermissions
        || (rights & FileSystemRights.TakeOwnership) == FileSystemRights.TakeOwnership;
}

sealed class StaticBytesHandler : HttpMessageHandler
{
    private readonly IReadOnlyDictionary<string, byte[]> _responses;

    public StaticBytesHandler(IReadOnlyDictionary<string, byte[]> responses)
    {
        _responses = responses;
    }

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        string key = request.RequestUri?.ToString() ?? "";
        if (!_responses.TryGetValue(key, out byte[]? body))
        {
            return Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.NotFound)
            {
                Content = new ByteArrayContent(Array.Empty<byte>())
            });
        }

        return Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.OK)
        {
            Content = new ByteArrayContent(body)
        });
    }
}
