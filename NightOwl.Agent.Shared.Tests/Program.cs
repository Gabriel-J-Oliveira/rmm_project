using System.Text.Json;
using System.Security.Cryptography;
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
    Require(JobFinalStatuses.All.Contains(JobFinalStatuses.RolledBack), "rolled_back should be a final job result status.");
    Require(JobFinalStatuses.All.Contains(JobFinalStatuses.RollbackFailed), "rollback_failed should be a final job result status.");

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
    Require(resultQueue.ListDue(DateTimeOffset.UtcNow).Count == 1, "Pending result should be due immediately.");
    Require(!string.IsNullOrWhiteSpace(pending.PayloadSha256), "Payload hash was not calculated.");

    resultQueue.MarkAttemptFailed(pending, JobErrorCodes.ResultSendFailed, "backend unavailable");
    PendingResultRecord retried = resultQueue.LoadAll().Single(record => record.ResultId == pending.ResultId);
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
