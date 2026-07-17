using System.Text.Json;
using NightOwl.Agent.Shared;

string root = Path.Combine(Path.GetTempPath(), "NightOwlPathsTests", Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
Environment.SetEnvironmentVariable("NIGHTOWL_HOME", root);

try
{
    NightOwlPaths paths = NightOwlPaths.FromEnvironment();
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

    string updateStatePath = paths.UpdateStatePath;
    UpdateStateStore store = new(updateStatePath);
    UpdateState state = UpdateState.Create("update-test", "job-test", "0.1.0.7", "0.1.0.8");
    state.PackageUrl = "https://nightowl.example/downloads/nightowl-agent/NightOwl.Agent.Windows.zip";
    state.ExpectedSha256 = new string('a', 64);
    store.Save(state);
    Require(File.Exists(updateStatePath), "Update state was not written.");

    UpdateState loaded = store.Load() ?? throw new InvalidOperationException("Update state was not loaded.");
    Require(loaded.UpdateId == "update-test", "Update ID was not preserved.");
    Require(loaded.JobId == "job-test", "Job ID was not preserved.");
    Require(loaded.CurrentStage == UpdateStages.Received, "Initial update stage mismatch.");
    Require(loaded.IsActive, "New update state must be active.");

    loaded.MarkStage(UpdateStages.Downloading);
    store.Save(loaded);
    UpdateState downloading = store.Load() ?? throw new InvalidOperationException("Downloading state was not loaded.");
    Require(downloading.CurrentStage == UpdateStages.Downloading, "Update transition was not persisted.");
    Require(downloading.UpdateId == "update-test", "Update ID changed after transition.");
    Require(downloading.JobId == "job-test", "Job ID changed after transition.");

    string rawJson = File.ReadAllText(updateStatePath);
    Require(rawJson.TrimStart().StartsWith("{"), "Atomic write produced invalid JSON prefix.");
    Require(rawJson.Contains("\"update_id\"", StringComparison.Ordinal), "Update state JSON does not use snake_case.");

    File.WriteAllText(updateStatePath, "{ invalid json");
    Require(!store.TryLoad(out _, out string invalidError), "Invalid update state should fail TryLoad.");
    Require(invalidError.Length > 0, "Invalid state error should be reported.");

    state.MarkStage(UpdateStages.WaitingHealthCheck);
    store.Save(state);
    UpdateState interrupted = store.Load() ?? throw new InvalidOperationException("Interrupted state was not loaded.");
    Require(interrupted.IsActive, "Waiting health check should be treated as incomplete.");
    Require(interrupted.CurrentStage == UpdateStages.WaitingHealthCheck, "Interrupted stage was not preserved.");

    interrupted.MarkStage(UpdateStages.Completed, UpdateStatuses.Completed);
    store.Save(interrupted);
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
