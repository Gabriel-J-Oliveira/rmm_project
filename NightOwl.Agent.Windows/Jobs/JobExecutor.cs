using System.Diagnostics;
using System.Net.NetworkInformation;
using System.Text.Json;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Windows.Jobs;

public sealed class JobExecutor
{
    private readonly WindowsInventoryCollector _collector;
    private readonly JsonlLogger _logger;
    private readonly JobExecutionPolicy _policy;

    public JobExecutor(WindowsInventoryCollector collector, JsonlLogger logger, JobExecutionPolicy policy)
    {
        _collector = collector;
        _logger = logger;
        _policy = policy;
    }

    public async Task<JobExecutionResult> ExecuteAsync(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        DateTimeOffset started = DateTimeOffset.UtcNow;
        Stopwatch stopwatch = Stopwatch.StartNew();
        JobDecision decision;
        try
        {
            decision = _policy.Prepare(config, job);
        }
        catch (Exception ex)
        {
            await _logger.LogAsync("job.state.invalid", ex.Message, new { job.Id, job.Type, error_code = JobErrorCodes.JobStateInvalid }, ct, "error");
            return BuildFailure(config, job, started, stopwatch, JobFinalStatuses.Failed, JobErrorCodes.JobStateInvalid, ex.Message, ex.ToString());
        }

        if (!decision.ShouldExecute)
        {
            JobExecutionResult final = decision.FinalResult!;
            _policy.MarkFinal(config, job, final, ExtractErrorCode(final));
            await _logger.LogAsync("job.rejected", "Job rejected before execution.", BuildJobLog(job, final.Status, ExtractErrorCode(final), final.DurationSeconds, decision.TimeoutSeconds), ct, "warning");
            return final;
        }

        try
        {
            _policy.MarkRunning(job);
        }
        catch (Exception ex)
        {
            stopwatch.Stop();
            await _logger.LogAsync("job.state.invalid", ex.Message, new { job.Id, job.Type, error_code = JobErrorCodes.JobStateInvalid }, ct, "error");
            return BuildFailure(config, job, started, stopwatch, JobFinalStatuses.Failed, JobErrorCodes.JobStateInvalid, ex.Message, ex.ToString());
        }

        await _logger.LogAsync("job.started", "Job started.", BuildJobLog(job, "running", "", 0, decision.TimeoutSeconds), ct);

        using CancellationTokenSource timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(TimeSpan.FromSeconds(decision.TimeoutSeconds));
        CancellationToken jobToken = timeoutCts.Token;

        try
        {
            if (job.Type == "update_agent")
            {
                JobExecutionResult updateResult = await StartUpdateAgentAsync(config, job, started, stopwatch, jobToken);
                _policy.MarkFinal(config, job, updateResult);
                return updateResult;
            }
            if (job.Type == "restart_agent")
            {
                JobExecutionResult restartResult = await StartRestartAgentAsync(config, job, started, stopwatch, jobToken);
                _policy.MarkFinal(config, job, restartResult);
                return restartResult;
            }
            if (job.Type == "update_trusted_release_keys")
            {
                JobExecutionResult trustResult = await UpdateTrustedReleaseKeysAsync(config, job, started, stopwatch, jobToken);
                _policy.MarkFinal(config, job, trustResult, ExtractErrorCode(trustResult));
                return trustResult;
            }
            if (job.Type == "uninstall_agent")
            {
                JobExecutionResult uninstallResult = await StartUninstallAgentAsync(config, job, started, stopwatch, jobToken);
                _policy.MarkFinal(config, job, uninstallResult, ExtractErrorCode(uninstallResult));
                return uninstallResult;
            }

            object result = job.Type switch
            {
                "force_inventory" => _collector.BuildCollectPayload(config),
                "collect_disks" => new { disks = await RunCollectionAsync("disks", _collector.GetDisks, jobToken), collected_at = DateTimeOffset.UtcNow },
                "collect_software" => new { installed_software = await RunCollectionAsync("software", _collector.GetSoftware, jobToken), collected_at = DateTimeOffset.UtcNow },
                "collect_security" => await RunCollectionAsync("security", _collector.GetSecurity, jobToken),
                "windows_update_scan" => await RunCollectionAsync("patches", _collector.GetPatchStatus, jobToken),
                "collect_logs" => CollectLogs(config, job),
                "ping" => await RunPingAsync(config, job, jobToken),
                _ => throw new NotSupportedException("unsupported_job_type")
            };

            stopwatch.Stop();
            JobExecutionResult completed = new()
            {
                JobId = job.Id,
                Status = "completed",
                StartedAt = started,
                FinishedAt = DateTimeOffset.UtcNow,
                DurationSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
                ExitCode = 0,
                Stdout = "ok",
                Result = result
            };
            completed = LimitResult(config, job, completed);
            _policy.MarkFinal(config, job, completed);
            await _logger.LogAsync("job.completed", "Job completed.", BuildJobLog(job, completed.Status, "", completed.DurationSeconds, decision.TimeoutSeconds), ct);
            return completed;
        }
        catch (OperationCanceledException ex) when (!ct.IsCancellationRequested)
        {
            stopwatch.Stop();
            JobExecutionResult timedOut = BuildFailure(config, job, started, stopwatch, JobFinalStatuses.TimedOut, JobErrorCodes.JobTimeout, "Job timed out.", ex.ToString());
            _policy.MarkFinal(config, job, timedOut, JobErrorCodes.JobTimeout);
            await _logger.LogAsync("job.timed_out", "Job timed out.", BuildJobLog(job, timedOut.Status, JobErrorCodes.JobTimeout, timedOut.DurationSeconds, decision.TimeoutSeconds), CancellationToken.None, "error");
            return timedOut;
        }
        catch (Exception ex)
        {
            stopwatch.Stop();
            JobExecutionResult failed = BuildFailure(config, job, started, stopwatch, JobFinalStatuses.Failed, JobErrorCodes.JobExecutionFailed, ex.Message, ex.ToString());
            _policy.MarkFinal(config, job, failed, JobErrorCodes.JobExecutionFailed);
            await _logger.LogAsync("job.failed", ex.Message, BuildJobLog(job, failed.Status, JobErrorCodes.JobExecutionFailed, failed.DurationSeconds, decision.TimeoutSeconds), ct, "error");
            return failed;
        }
    }

    private static object CollectLogs(AgentConfig config, AgentJobRequest job)
    {
        string source = GetPayloadString(job, "source", "agent");
        string logPath = source.ToLowerInvariant() switch
        {
            "updater" => NightOwlPaths.Current.UpdaterLogPath,
            "tray" => NightOwlPaths.Current.TrayLogPath,
            _ => config.LogPath
        };
        int maxLines = GetPayloadInt(job, "max_lines", 200);
        int maxBytes = GetPayloadInt(job, "max_bytes", 64 * 1024);
        maxLines = Math.Clamp(maxLines, 1, 1000);
        maxBytes = Math.Clamp(maxBytes, 1024, JobExecutionPolicy.MaxOutputBytes);

        if (!File.Exists(logPath))
        {
            return new { source, lines = Array.Empty<string>(), log_path = Path.GetFileName(logPath), output_truncated = false };
        }

        string[] lines = File.ReadLines(logPath).TakeLast(maxLines).Select(Sanitize).ToArray();
        bool truncated = false;
        while (System.Text.Encoding.UTF8.GetByteCount(JsonSerializer.Serialize(lines)) > maxBytes && lines.Length > 0)
        {
            truncated = true;
            lines = lines.Skip(Math.Max(1, lines.Length / 10)).ToArray();
        }
        return new { source, lines, log_path = Path.GetFileName(logPath), output_truncated = truncated };
    }

    private async Task<T> RunCollectionAsync<T>(string section, Func<T> collect, CancellationToken ct)
    {
        await _logger.LogAsync($"collection.{section}.started", $"{section} collection started by job.", null, ct);
        Task<T>? task = null;
        try
        {
            task = Task.Run(collect, ct);
            T result = await task.WaitAsync(ct);
            await _logger.LogAsync($"collection.{section}.completed", $"{section} collection completed by job.", null, ct);
            return result;
        }
        catch (OperationCanceledException) when (task is not null && !task.IsCompleted)
        {
            await _logger.LogAsync($"collection.{section}.cancel_pending", $"{section} collection did not respond immediately to cancellation.", null, CancellationToken.None, "warning");
            throw;
        }
        catch (Exception ex)
        {
            await _logger.LogAsync($"collection.{section}.failed", ex.Message, new { exception = ex.ToString() }, ct, "error");
            throw;
        }
    }

    private static async Task<object> RunPingAsync(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        string target = job.Payload.TryGetValue("target", out object? value)
            ? value?.ToString() ?? string.Empty
            : string.Empty;
        if (string.IsNullOrWhiteSpace(target))
        {
            target = string.IsNullOrWhiteSpace(config.MachineId) ? "127.0.0.1" : config.MachineId;
        }

        int timeoutSeconds = job.TimeoutSeconds <= 0 ? 30 : job.TimeoutSeconds;
        int timeoutMs = Math.Clamp(timeoutSeconds * 1000, 1000, 30000);

        using Ping ping = new();
        PingReply reply = await ping.SendPingAsync(target, timeoutMs).WaitAsync(ct);
        return new
        {
            target,
            status = reply.Status.ToString(),
            roundtrip_time_ms = reply.Status == IPStatus.Success ? reply.RoundtripTime : null as long?,
            address = reply.Address?.ToString() ?? "",
            success = reply.Status == IPStatus.Success
        };
    }

    private async Task<JobExecutionResult> StartRestartAgentAsync(AgentConfig config, AgentJobRequest job, DateTimeOffset started, Stopwatch stopwatch, CancellationToken ct)
    {
        await _logger.LogAsync("job.restart_agent.received", "Restart agent job received.", new { job.Id }, ct);
        WritePendingJobResult(
            config,
            new JobExecutionResult
            {
                JobId = job.Id,
                Status = "completed",
                StartedAt = started,
                FinishedAt = DateTimeOffset.UtcNow,
                DurationSeconds = 0,
                ExitCode = 0,
                Stdout = "restart requested",
                Result = new
                {
                    type = "restart_agent",
                    restart_status = "requested",
                    message = $"{NightOwlPaths.ServiceName} restart requested successfully.",
                    completed_at = DateTimeOffset.UtcNow
                }
            });

        string script = $"Start-Sleep -Seconds 2; Restart-Service -Name '{NightOwlPaths.ServiceName}' -Force";
        using Process process = new()
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -Command " + QuotePowerShell(script),
                WorkingDirectory = config.InstallPath,
                UseShellExecute = true,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            }
        };
        process.Start();
        stopwatch.Stop();

        await _logger.LogAsync("job.restart_agent.started", "Restart helper process started.", new { job.Id, process_id = process.Id }, ct);
        return new JobExecutionResult
        {
            JobId = job.Id,
            Status = "running",
            StartedAt = started,
            FinishedAt = DateTimeOffset.MinValue,
            DurationSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
            ExitCode = 0,
            Stdout = "restart helper started",
            Result = new
            {
                type = "restart_agent",
                restart_status = "helper_started",
                message = "Restart helper started. Final result will be sent after service restart."
            }
        };
    }

    private async Task<JobExecutionResult> UpdateTrustedReleaseKeysAsync(AgentConfig config, AgentJobRequest job, DateTimeOffset started, Stopwatch stopwatch, CancellationToken ct)
    {
        string metadataUrl = GetPayloadString(job, "metadata_url", "");
        string bundleUrl = GetPayloadString(job, "bundle_url", "");
        string signatureUrl = GetPayloadString(job, "signature_url", "");
        string expectedRootKeyId = GetPayloadString(job, "expected_root_key_id", "");
        string expectedSha256 = GetPayloadString(job, "expected_sha256", "");
        long expectedBundleVersion = GetPayloadLong(job, "expected_bundle_version", 0);

        await _logger.LogAsync("trust.sync.start", "Trusted release keys sync started.", new
        {
            job_id = job.Id,
            metadata_url = SanitizeUrl(metadataUrl),
            bundle_url = SanitizeUrl(bundleUrl),
            signature_url = SanitizeUrl(signatureUrl),
            expected_root_key_id = expectedRootKeyId,
            expected_bundle_version = expectedBundleVersion > 0 ? (long?)expectedBundleVersion : null
        }, ct);

        ReleaseTrustStore store = new(NightOwlPaths.Current);
        IReadOnlyList<ReleaseTrustRootKey> roots = ReleaseTrustAnchors.Load(NightOwlPaths.Current);
        if (roots.Count == 0)
        {
            stopwatch.Stop();
            await _logger.LogAsync("trust.sync.failed", "No release trust roots are installed.", new
            {
                job_id = job.Id,
                error_code = ReleaseTrustErrorCodes.TrustRootUnknown
            }, ct, "error");
            return BuildFailure(config, job, started, stopwatch, JobFinalStatuses.Failed, ReleaseTrustErrorCodes.TrustRootUnknown, "No release trust roots are installed.", "");
        }

        using HttpClient http = new() { Timeout = TimeSpan.FromSeconds(Math.Clamp(job.TimeoutSeconds <= 0 ? 180 : job.TimeoutSeconds, 30, 300)) };
        ReleaseTrustBundleUpdater updater = new(http, store, roots);
        ReleaseTrustSyncResult syncResult = await updater.SyncAsync(new ReleaseTrustSyncRequest
        {
            MetadataUrl = metadataUrl,
            BundleUrl = bundleUrl,
            SignatureUrl = signatureUrl,
            ExpectedRootKeyId = expectedRootKeyId,
            ExpectedBundleVersion = expectedBundleVersion > 0 ? expectedBundleVersion : null,
            ExpectedSha256 = expectedSha256,
            JobId = job.Id
        }, ct);

        stopwatch.Stop();
        if (!syncResult.Status.Equals("completed", StringComparison.OrdinalIgnoreCase))
        {
            await _logger.LogAsync("trust.sync.failed", "Trusted release keys sync failed.", new
            {
                job_id = job.Id,
                error_code = syncResult.ErrorCode,
                error = syncResult.ErrorMessage
            }, ct, "error");
            return BuildFailure(config, job, started, stopwatch, JobFinalStatuses.Failed, syncResult.ErrorCode, syncResult.ErrorMessage, syncResult.ErrorMessage);
        }

        string updateStatus = string.IsNullOrWhiteSpace(syncResult.UpdateStatus) ? "updated" : syncResult.UpdateStatus;
        string eventType = updateStatus.Equals("no_update", StringComparison.OrdinalIgnoreCase)
            ? "trust.sync.no_update"
            : "trust.install.completed";
        string eventMessage = updateStatus.Equals("no_update", StringComparison.OrdinalIgnoreCase)
            ? "Trusted release keys bundle is already current."
            : "Trusted release keys bundle installed.";
        await _logger.LogAsync(eventType, eventMessage, new
        {
            job_id = job.Id,
            update_status = updateStatus,
            bundle_version = syncResult.InstalledBundleVersion,
            bundle_sha256 = syncResult.InstalledBundleSha256,
            root_key_id = syncResult.RootKeyId,
            active_key_ids = syncResult.ActiveKeyIds,
            revoked_key_ids = syncResult.RevokedKeyIds,
            duration_seconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3)
        }, ct);

        return new JobExecutionResult
        {
            JobId = job.Id,
            Status = "completed",
            StartedAt = started,
            FinishedAt = DateTimeOffset.UtcNow,
            DurationSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
            ExitCode = 0,
            Stdout = updateStatus.Equals("no_update", StringComparison.OrdinalIgnoreCase)
                ? "trusted release keys already current"
                : "trusted release keys updated",
            Result = new
            {
                type = "update_trusted_release_keys",
                update_status = updateStatus,
                bundle_version = syncResult.InstalledBundleVersion,
                bundle_sha256 = syncResult.InstalledBundleSha256,
                root_key_id = syncResult.RootKeyId,
                active_key_ids = syncResult.ActiveKeyIds,
                revoked_key_ids = syncResult.RevokedKeyIds
            }
        };
    }

    private async Task<JobExecutionResult> StartUpdateAgentAsync(AgentConfig config, AgentJobRequest job, DateTimeOffset started, Stopwatch stopwatch, CancellationToken ct)
    {
        await _logger.LogAsync("job.update_agent.received", "Update agent job received.", new { job.Id }, ct);
        string updater = Path.Combine(config.InstallPath, "NightOwl.Agent.Updater.exe");
        if (!File.Exists(updater))
        {
            await _logger.LogAsync("job.update_agent.failed", "Updater executable was not found.", new { job.Id, updater }, ct, "error");
            throw new FileNotFoundException("Updater nao encontrado no endpoint.", updater);
        }
        await _logger.LogAsync("job.update_agent.updater_found", "Updater executable found.", new { job.Id, updater }, ct);

        string channel = GetPayloadString(job, "channel", "stable");
        string targetVersion = GetPayloadString(job, "target_version", "latest");
        string releaseId = GetPayloadString(job, "release_id", "");
        string packageUrl = GetPayloadString(job, "package_url", "");
        string checksumUrl = GetPayloadString(job, "checksum_url", "");
        string sha256 = GetPayloadString(job, "sha256", "");
        long size = GetPayloadLong(job, "size", 0);
        string manifestUrl = GetPayloadString(job, "manifest_url", "");
        string manifestSha256 = GetPayloadString(job, "manifest_sha256", "");
        string signatureUrl = GetPayloadString(job, "signature_url", "");
        string signatureSha256 = GetPayloadString(job, "signature_sha256", "");
        string signatureKeyId = GetPayloadString(job, "signature_key_id", "");
        string minimumUpdaterVersion = GetPayloadString(job, "minimum_updater_version", "");
        bool mandatory = GetPayloadBool(job, "mandatory", false);
        bool force = GetPayloadBool(job, "force", false);

        List<string> args = new()
        {
            "update",
            "--source", "job",
            "--job-id", job.Id,
            "--channel", channel,
            "--target-version", targetVersion,
            "--quiet",
            "--json-output"
        };
        AddOption(args, "--release-id", releaseId);
        AddOption(args, "--package-url", packageUrl);
        AddOption(args, "--checksum-url", checksumUrl);
        AddOption(args, "--sha256", sha256);
        AddOption(args, "--manifest-url", manifestUrl);
        AddOption(args, "--manifest-sha256", manifestSha256);
        AddOption(args, "--signature-url", signatureUrl);
        AddOption(args, "--signature-sha256", signatureSha256);
        AddOption(args, "--signature-key-id", signatureKeyId);
        if (size > 0)
        {
            AddOption(args, "--size", size.ToString(System.Globalization.CultureInfo.InvariantCulture));
        }
        AddOption(args, "--minimum-updater-version", minimumUpdaterVersion);
        if (mandatory)
        {
            args.Add("--mandatory");
        }
        if (force)
        {
            args.Add("--force");
        }

        string arguments = string.Join(" ", args.Select(QuoteArg));
        await _logger.LogAsync(
            "job.update_agent.started",
            "Starting updater for update_agent job.",
            new
            {
                job.Id,
                channel,
                targetVersion,
                releaseId,
                hasPackageUrl = !string.IsNullOrWhiteSpace(packageUrl),
                hasChecksumUrl = !string.IsNullOrWhiteSpace(checksumUrl),
                hasManifestUrl = !string.IsNullOrWhiteSpace(manifestUrl),
                hasSignatureUrl = !string.IsNullOrWhiteSpace(signatureUrl),
                signatureKeyId,
                hasSha256 = !string.IsNullOrWhiteSpace(sha256),
                size,
                minimumUpdaterVersion,
                mandatory,
                force
            },
            ct);

        using Process process = new()
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = updater,
                Arguments = arguments,
                WorkingDirectory = config.InstallPath,
                UseShellExecute = true,
                CreateNoWindow = true
            }
        };

        process.Start();
        stopwatch.Stop();

        await _logger.LogAsync("job.update_agent.runner_started", "Updater bootstrap started; final result will be sent after service restart.", new { job.Id, process_id = process.Id }, ct);

        return new JobExecutionResult
        {
            JobId = job.Id,
            Status = "running",
            StartedAt = started,
            FinishedAt = DateTimeOffset.MinValue,
            DurationSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
            ExitCode = 0,
            Stdout = "update runner started",
            Result = new
            {
                type = "update_agent",
                update_status = "runner_started",
                message = "Updater runner started. Final result will be sent by the restarted service.",
                channel,
                target_version = targetVersion,
                release_id = releaseId,
                package_url = SanitizeUrl(packageUrl),
                checksum_url = SanitizeUrl(checksumUrl),
                manifest_url = SanitizeUrl(manifestUrl),
                manifest_sha256 = manifestSha256,
                signature_url = SanitizeUrl(signatureUrl),
                signature_sha256 = signatureSha256,
                signature_key_id = signatureKeyId,
                sha256_present = !string.IsNullOrWhiteSpace(sha256),
                size,
                minimum_updater_version = minimumUpdaterVersion,
                mandatory,
                force
            }
        };
    }

    private async Task<JobExecutionResult> StartUninstallAgentAsync(AgentConfig config, AgentJobRequest job, DateTimeOffset started, Stopwatch stopwatch, CancellationToken ct)
    {
        string mode = GetPayloadString(job, "mode", "uninstall").ToLowerInvariant();
        bool purgeAuthorized = GetPayloadBool(job, "purge_authorized", false);
        if (mode == "purge" && !purgeAuthorized)
        {
            stopwatch.Stop();
            return BuildFailure(config, job, started, stopwatch, JobFinalStatuses.InvalidParameters, JobErrorCodes.JobInvalidParameters, "Remote purge requires explicit backend authorization.", "");
        }

        string sourceRunner = Path.Combine(config.InstallPath, "NightOwl.Agent.Uninstaller.exe");
        if (!File.Exists(sourceRunner))
        {
            await _logger.LogAsync("job.uninstall_agent.failed", "Uninstaller executable was not found.", new { job.Id, sourceRunner }, ct, "error");
            throw new FileNotFoundException("Uninstaller nao encontrado no endpoint.", sourceRunner);
        }

        string runnerDir = Path.Combine(NightOwlPaths.Current.UpdatesRunnerDir, "uninstall-" + job.Id);
        int filesCopied = CopyUninstallerRunnerPayload(config.InstallPath, runnerDir);

        string runner = Path.Combine(runnerDir, "NightOwl.Agent.Uninstaller.exe");
        List<string> args = new()
        {
            "uninstall",
            "--job-id", job.Id,
            "--mode", mode,
            "--config-path", Path.Combine(NightOwlPaths.Current.ConfigDir, "agent.config.json"),
            "--root-path", NightOwlPaths.Current.Root,
            "--install-path", config.InstallPath,
            "--service-name", NightOwlPaths.ServiceName,
            "--quiet",
            "--json-output"
        };
        if (purgeAuthorized)
        {
            args.Add("--purge-authorized");
        }

        await _logger.LogAsync("job.uninstall_agent.started", "Starting uninstaller runner for uninstall_agent job.", new
        {
            job.Id,
            mode,
            purge_authorized = purgeAuthorized,
            runner_dir = runnerDir,
            files_copied = filesCopied
        }, ct);

        using Process process = new()
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = runner,
                Arguments = string.Join(" ", args.Select(QuoteArg)),
                WorkingDirectory = runnerDir,
                UseShellExecute = true,
                CreateNoWindow = true
            }
        };
        process.Start();
        stopwatch.Stop();

        await _logger.LogAsync("job.uninstall_agent.runner_started", "Uninstaller runner started; final result will be sent by the runner.", new { job.Id, process_id = process.Id }, ct);

        return new JobExecutionResult
        {
            JobId = job.Id,
            Status = "running",
            StartedAt = started,
            FinishedAt = DateTimeOffset.MinValue,
            DurationSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
            ExitCode = 0,
            Stdout = "uninstaller runner started",
            Result = new
            {
                type = "uninstall_agent",
                uninstall_status = "runner_started",
                mode,
                purge_authorized = purgeAuthorized,
                message = "Uninstaller runner started. Final result will be sent by the runner."
            }
        };
    }

    public static int CopyUninstallerRunnerPayload(string installPath, string runnerDir)
    {
        if (string.IsNullOrWhiteSpace(installPath))
        {
            throw new ArgumentException("Install path is required.", nameof(installPath));
        }
        if (string.IsNullOrWhiteSpace(runnerDir))
        {
            throw new ArgumentException("Runner path is required.", nameof(runnerDir));
        }
        if (!Directory.Exists(installPath))
        {
            throw new DirectoryNotFoundException(installPath);
        }
        string sourceRunner = Path.Combine(installPath, "NightOwl.Agent.Uninstaller.exe");
        if (!File.Exists(sourceRunner))
        {
            throw new FileNotFoundException("Uninstaller nao encontrado no endpoint.", sourceRunner);
        }

        if (Directory.Exists(runnerDir))
        {
            Directory.Delete(runnerDir, recursive: true);
        }
        Directory.CreateDirectory(runnerDir);

        int filesCopied = 0;
        foreach (string source in Directory.EnumerateFileSystemEntries(installPath, "*", SearchOption.AllDirectories))
        {
            FileAttributes attributes = File.GetAttributes(source);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
            {
                continue;
            }

            string relative = Path.GetRelativePath(installPath, source);
            string destination = Path.Combine(runnerDir, relative);
            if ((attributes & FileAttributes.Directory) != 0)
            {
                Directory.CreateDirectory(destination);
                continue;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(source, destination, overwrite: true);
            filesCopied++;
        }

        if (!File.Exists(Path.Combine(runnerDir, "NightOwl.Agent.Uninstaller.exe")))
        {
            throw new FileNotFoundException("Payload do runner de desinstalacao sem executavel principal.", Path.Combine(runnerDir, "NightOwl.Agent.Uninstaller.exe"));
        }
        return filesCopied;
    }

    private static void AddOption(List<string> args, string name, string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return;
        }
        args.Add(name);
        args.Add(value);
    }

    private static string GetPayloadString(AgentJobRequest job, string key, string fallback)
    {
        return job.Payload.TryGetValue(key, out object? value) && value is not null && !string.IsNullOrWhiteSpace(value.ToString())
            ? value.ToString()!
            : fallback;
    }

    private static int GetPayloadInt(AgentJobRequest job, string key, int fallback)
    {
        if (!job.Payload.TryGetValue(key, out object? value) || value is null)
        {
            return fallback;
        }
        if (value is JsonElement element && element.TryGetInt32(out int parsedElement))
        {
            return parsedElement;
        }
        return int.TryParse(value.ToString(), out int parsed) ? parsed : fallback;
    }

    private static long GetPayloadLong(AgentJobRequest job, string key, long fallback)
    {
        if (!job.Payload.TryGetValue(key, out object? value) || value is null)
        {
            return fallback;
        }
        if (value is JsonElement element && element.TryGetInt64(out long parsedElement))
        {
            return parsedElement;
        }
        return long.TryParse(value.ToString(), out long parsed) ? parsed : fallback;
    }

    private static bool GetPayloadBool(AgentJobRequest job, string key, bool fallback)
    {
        if (!job.Payload.TryGetValue(key, out object? value) || value is null)
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

    private static string QuoteArg(string arg)
    {
        if (string.IsNullOrEmpty(arg))
        {
            return "\"\"";
        }
        if (!arg.Any(char.IsWhiteSpace) && !arg.Contains('"'))
        {
            return arg;
        }
        return "\"" + arg.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    private static string SanitizeUrl(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out Uri? uri))
        {
            return "";
        }
        return uri.GetLeftPart(UriPartial.Path);
    }

    private static void WritePendingJobResult(AgentConfig config, JobExecutionResult result)
    {
        string pendingDir = string.IsNullOrWhiteSpace(config.PendingResultsPath)
            ? Path.Combine(config.JobsPath, "Pending")
            : config.PendingResultsPath;
        Directory.CreateDirectory(pendingDir);
        string jobId = string.IsNullOrWhiteSpace(result.JobId) ? Guid.NewGuid().ToString("N") : result.JobId;
        string path = Path.Combine(pendingDir, $"job-result-{jobId}.json");
        File.WriteAllText(path, JsonSerializer.Serialize(result, new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true }));
    }

    private static JobExecutionResult BuildFailure(AgentConfig config, AgentJobRequest job, DateTimeOffset started, Stopwatch stopwatch, string status, string errorCode, string message, string stderr)
    {
        DateTimeOffset finished = DateTimeOffset.UtcNow;
        return new JobExecutionResult
        {
            JobId = job.Id,
            Status = status,
            StartedAt = started,
            FinishedAt = finished,
            DurationSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
            ExitCode = 1,
            Stderr = Trim(Sanitize(stderr), 8000),
            ErrorMessage = Sanitize(message),
            Result = new
            {
                type = job.Type,
                error_code = errorCode,
                error_message = Sanitize(message),
                agent_version = config.AgentVersion,
                machine_id = config.MachineId
            }
        };
    }

    private static JobExecutionResult LimitResult(AgentConfig config, AgentJobRequest job, JobExecutionResult result)
    {
        string json = JsonSerializer.Serialize(result.Result, new JsonSerializerOptions(JsonSerializerDefaults.Web));
        bool truncated = false;
        object? output = result.Result;
        if (System.Text.Encoding.UTF8.GetByteCount(json) > JobExecutionPolicy.MaxOutputBytes)
        {
            truncated = true;
            output = new
            {
                type = job.Type,
                output_truncated = true,
                error_code = JobErrorCodes.JobResultTooLarge,
                message = "Job result exceeded maximum size and was truncated.",
                preview = Trim(Sanitize(json), 12000)
            };
        }

        result.Stdout = Trim(Sanitize(result.Stdout), 8000);
        result.Stderr = Trim(Sanitize(result.Stderr), 8000);
        result.Result = new
        {
            output,
            output_truncated = truncated,
            agent_version = config.AgentVersion,
            machine_id = config.MachineId
        };
        return result;
    }

    private static object BuildJobLog(AgentJobRequest job, string status, string errorCode, double durationSeconds, int timeoutSeconds)
    {
        return new
        {
            job_id = job.Id,
            job_type = job.Type,
            correlation_id = job.CorrelationId,
            attempt = Math.Max(job.Attempt, 1),
            status,
            error_code = errorCode,
            duration_ms = (long)Math.Round(Math.Max(0, durationSeconds) * 1000),
            timeout_seconds = timeoutSeconds
        };
    }

    private static string ExtractErrorCode(JobExecutionResult result)
    {
        try
        {
            string json = JsonSerializer.Serialize(result.Result, new JsonSerializerOptions(JsonSerializerDefaults.Web));
            using JsonDocument document = JsonDocument.Parse(json);
            if (TryFindProperty(document.RootElement, "error_code", out string value))
            {
                return value;
            }
        }
        catch
        {
            // Best effort.
        }
        return "";
    }

    private static bool TryFindProperty(JsonElement element, string name, out string value)
    {
        if (element.ValueKind == JsonValueKind.Object)
        {
            foreach (JsonProperty property in element.EnumerateObject())
            {
                if (property.NameEquals(name) && property.Value.ValueKind == JsonValueKind.String)
                {
                    value = property.Value.GetString() ?? "";
                    return true;
                }
                if (TryFindProperty(property.Value, name, out value))
                {
                    return true;
                }
            }
        }
        value = "";
        return false;
    }

    private static string Sanitize(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return "";
        }
        string sanitized = value;
        foreach (string marker in new[] { "agentToken", "agent_token", "EnrollmentToken", "Bearer " })
        {
            sanitized = sanitized.Replace(marker, "[redacted]", StringComparison.OrdinalIgnoreCase);
        }
        return sanitized;
    }

    private static string QuotePowerShell(string value)
    {
        return "\"" + value.Replace("\"", "`\"") + "\"";
    }

    private static string Trim(string value, int max)
    {
        if (string.IsNullOrEmpty(value) || value.Length <= max)
        {
            return value;
        }

        return value[..max];
    }
}
