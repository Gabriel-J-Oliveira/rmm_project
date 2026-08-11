using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Updater;

internal static class Program
{
    private const string ServiceName = NightOwlPaths.ServiceName;
    private const string TrayProcessName = NightOwlPaths.TrayProcessName;
    private const string ProductName = "NightOwl Agent Windows";
    private const string DefaultServerUrl = NightOwlPaths.DefaultServerUrl;
    private static readonly NightOwlPaths Paths = NightOwlPaths.Current;
    private static readonly string LogPath = Paths.UpdaterLogPath;
    private static readonly string UpdatesRoot = Paths.UpdatesDir;
    private static readonly string DownloadsRoot = Paths.UpdatesDownloadsDir;
    private static readonly string StagingRoot = Paths.UpdatesStagingDir;
    private static readonly string RunnerRoot = Paths.UpdatesRunnerDir;
    private static readonly string BackupsRoot = Paths.UpdatesBackupDir;
    private static readonly string PendingJobsRoot = Paths.PendingResultsDir;
    private static readonly string PendingUpdateResultPath = Path.Combine(PendingJobsRoot, "pending-update-result.json");
    private static readonly UpdateStateStore UpdateStateStore = new(Paths.UpdateStatePath);
    private const int DefaultHealthCheckTimeoutSeconds = 180;
    private const int DefaultQuiesceTimeoutSeconds = 30;
    private const int DefaultFileReplaceTimeoutSeconds = 30;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    [STAThread]
    private static async Task<int> Main(string[] args)
    {
        string command = args.FirstOrDefault(arg => !arg.StartsWith("--", StringComparison.OrdinalIgnoreCase))?.ToLowerInvariant() ?? "status";
        bool interactive = HasFlag(args, "--interactive");

        try
        {
            Paths.Bootstrap("updater");

            WriteLog("updater.start", "NightOwl Agent Updater iniciado.", new { command, interactive });

            int exitCode = command switch
            {
                "check" => await RunCheckAsync(interactive),
                "update" => await RunUpdateAsync(args, interactive),
                "status" => RunStatus(interactive),
                "rollback" => RunRollback(interactive),
                _ => RunUsage(command, interactive)
            };

            return exitCode;
        }
        catch (Exception ex)
        {
            WriteLog("updater.error", "Falha nao tratada no updater.", new { command, error = ex.Message });
            WriteJson(new { ok = false, error = ex.Message });
            if (interactive)
            {
                ShowMessage("Falha no atualizador: " + ex.Message, MessageBoxIcon.Error);
            }
            return 1;
        }
    }

    private static async Task<int> RunCheckAsync(bool interactive)
    {
        AgentConfig config = LoadConfig();
        AgentVersionInfo installed = LoadInstalledVersion(config);
        UpdateManifest manifest = await DownloadManifestAsync(config);
        bool updateAvailable = CompareVersions(manifest.Version, installed.Version) > 0;

        var result = new
        {
            ok = true,
            updateAvailable,
            installedVersion = installed.Version,
            availableVersion = manifest.Version,
            manifest.Notes,
            manifest.Force,
            manifest.RequiresRestart,
            manifest.PublishedAt
        };
        WriteLog("updater.check.completed", "Verificacao de atualizacao concluida.", result);
        WriteJson(result);

        if (interactive)
        {
            string message = updateAvailable
                ? $"Atualizacao disponivel.\n\nInstalada: {installed.Version}\nDisponivel: {manifest.Version}\n\n{manifest.Notes}"
                : $"Agente atualizado.\n\nVersao instalada: {installed.Version}";
            ShowMessage(message, updateAvailable ? MessageBoxIcon.Information : MessageBoxIcon.None);
        }

        return 0;
    }

    private static async Task<int> RunUpdateAsync(string[] args, bool interactive)
    {
        JobContext jobContext = JobContext.FromArgs(args);
        string updateId = GetOption(args, "--update-id") ?? Guid.NewGuid().ToString();
        string source = GetOption(args, "--source") ?? "";
        WriteLog("update.start", "Update requested.", new { update_id = updateId, job_id = jobContext.JobId, runner = HasFlag(args, "--runner"), source });

        string? stagedPath = GetOption(args, "--apply-staged");
        if (!string.IsNullOrWhiteSpace(stagedPath))
        {
            string stagedManifestPath = GetOption(args, "--manifest") ?? throw new InvalidOperationException("Manifesto ausente para aplicacao staged.");
            string packageSha256 = GetOption(args, "--package-sha256") ?? "";
            return ApplyStagedUpdate(stagedPath, stagedManifestPath, packageSha256, interactive);
        }

        if (!HasFlag(args, "--runner"))
        {
            if (HasActiveUpdate(out UpdateState? active, out string invalidError) && active is not null)
            {
                WriteLog("update.already_running", "Active update state already exists.", new { active.UpdateId, active.JobId, active.CurrentStage, active.Status });
                WriteJson(new { ok = false, error_code = UpdateErrorCodes.UpdateAlreadyRunning, update_id = active.UpdateId, message = "Update already running." });
                return 31;
            }
            if (!string.IsNullOrWhiteSpace(invalidError))
            {
                WriteLog("update.state.invalid", "Invalid update state detected before starting update.", new { error_code = UpdateErrorCodes.UpdateStateInvalid, error = invalidError });
                WriteJson(new { ok = false, error_code = UpdateErrorCodes.UpdateStateInvalid, message = invalidError });
                return 32;
            }

            AgentConfig bootstrapConfig = LoadConfig();
            AgentVersionInfo bootstrapInstalled = LoadInstalledVersion(bootstrapConfig);
            string requestedTarget = GetOption(args, "--target-version") ?? "latest";
            UpdateState bootstrapState = UpdateState.Create(updateId, jobContext.JobId, bootstrapInstalled.Version, requestedTarget);
            UpdateStateStore.Save(bootstrapState);
            WriteLog("update.state.created", "Update state created before runner launch.", new { update_id = bootstrapState.UpdateId, job_id = bootstrapState.JobId, stage = bootstrapState.CurrentStage, from_version = bootstrapState.FromVersion, target_version = bootstrapState.TargetVersion });
            return LaunchIndependentRunner(args, interactive, updateId);
        }

        using UpdateStateLock updateLock = UpdateStateLock.TryAcquire();
        if (!updateLock.Acquired)
        {
            UpdateState? active = UpdateStateStore.TryLoad(out UpdateState? loadedState, out _) ? loadedState : null;
            WriteLog("update.lock.busy", "Another updater is already running.", new { error_code = UpdateErrorCodes.UpdateAlreadyRunning, active_update_id = active?.UpdateId ?? "" });
            WriteJson(new { ok = false, error_code = UpdateErrorCodes.UpdateAlreadyRunning, update_id = active?.UpdateId ?? "", message = "Update already running." });
            return 31;
        }

        UpdateState state = LoadOrCreateRunnerState(updateId, jobContext);
        if (!state.CurrentStage.Equals(UpdateStages.Received, StringComparison.OrdinalIgnoreCase)
            && !state.CurrentStage.Equals(UpdateStages.CheckingVersion, StringComparison.OrdinalIgnoreCase))
        {
            WriteLog("update.interrupted_detected", "Incomplete update state detected at runner start.", new { update_id = state.UpdateId, job_id = state.JobId, stage = state.CurrentStage, status = state.Status });
        }

        AgentConfig config = LoadConfig();
        MarkStage(state, UpdateStages.CheckingVersion);
        AgentVersionInfo installed = LoadInstalledVersion(config);
        state.FromVersion = installed.Version;
        if (jobContext.HasExplicitTarget)
        {
            state.TargetVersion = jobContext.TargetVersion;
        }
        UpdateStateStore.Save(state);
        UpdateManifest manifest;
        ChecksumsManifest? explicitChecksums = null;
        try
        {
            if (jobContext.HasExplicitReleaseMetadata)
            {
                manifest = BuildManifestFromJobContext(config, jobContext);
                explicitChecksums = ChecksumsManifest.FromPackage("NightOwl.Agent.Windows.zip", jobContext.Sha256, jobContext.Size);
                WriteLog("updater.release.explicit", "Using explicit release metadata from update_agent job.", new
                {
                    update_id = state.UpdateId,
                    job_id = state.JobId,
                    release_id = jobContext.ReleaseId,
                    target_version = jobContext.TargetVersion,
                    channel = jobContext.Channel,
                    package_url = SanitizeUrl(jobContext.PackageUrl),
                    has_sha256 = !string.IsNullOrWhiteSpace(jobContext.Sha256),
                    size = jobContext.Size
                });
            }
            else if (jobContext.HasExplicitTarget)
            {
                string message = "Explicit target_version was provided without complete release metadata.";
                MarkFailed(state, UpdateErrorCodes.UpdateReleaseMetadataMissing, message);
                var missing = new
                {
                    ok = false,
                    updated = false,
                    reason = "release_metadata_missing",
                    error_code = UpdateErrorCodes.UpdateReleaseMetadataMissing,
                    installedVersion = installed.Version,
                    targetVersion = jobContext.TargetVersion,
                    availableVersion = ""
                };
                if (jobContext.IsJob)
                {
                    WritePendingUpdateResult(jobContext, state, "failed", 25, installed.Version, installed.Version, message, missing, "");
                }
                WriteJson(missing);
                return 25;
            }
            else
            {
                manifest = await DownloadManifestAsync(config);
            }
        }
        catch (Exception ex)
        {
            MarkFailed(state, ErrorCodeFromException(ex, UpdateErrorCodes.UpdatePackageInvalid), SanitizeMessage(ex.Message));
            var failedResolution = new
            {
                ok = false,
                updated = false,
                reason = jobContext.HasExplicitTarget ? "target_release_not_resolved" : "manifest_resolution_failed",
                error_code = state.ErrorCode,
                installedVersion = installed.Version,
                targetVersion = jobContext.TargetVersion,
                error = SanitizeMessage(ex.Message)
            };
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, state, "failed", 25, installed.Version, installed.Version, SanitizeMessage(ex.Message), failedResolution, SanitizeMessage(ex.ToString()));
            }
            WriteJson(failedResolution);
            return 25;
        }

        state.FromVersion = installed.Version;
        state.TargetVersion = manifest.Version;
        state.PackageUrl = ResolvePackageUrl(config, manifest);
        if (explicitChecksums is not null)
        {
            state.ExpectedSha256 = explicitChecksums.GetRequired("NightOwl.Agent.Windows.zip").Sha256;
        }
        UpdateStateStore.Save(state);
        try
        {
            await ValidateReleaseManifestAsync(config, manifest);
        }
        catch (Exception ex)
        {
            MarkFailed(state, ErrorCodeFromException(ex, UpdateErrorCodes.ReleaseManifestInvalid), SanitizeMessage(ex.Message));
            var failedManifest = new
            {
                ok = false,
                updated = false,
                reason = "release_manifest_invalid",
                error_code = state.ErrorCode,
                installedVersion = installed.Version,
                targetVersion = manifest.Version,
                availableVersion = manifest.Version,
                error = SanitizeMessage(ex.Message)
            };
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, state, "failed", 25, installed.Version, installed.Version, SanitizeMessage(ex.Message), failedManifest, SanitizeMessage(ex.ToString()));
            }
            WriteJson(failedManifest);
            return 25;
        }
        if (jobContext.HasExplicitTarget && !manifest.Version.Equals(jobContext.TargetVersion, StringComparison.OrdinalIgnoreCase))
        {
            string message = $"Resolved release version {manifest.Version} does not match requested target {jobContext.TargetVersion}.";
            MarkFailed(state, UpdateErrorCodes.UpdateTargetReleaseNotResolved, message);
            var mismatch = new
            {
                ok = false,
                updated = false,
                reason = "target_release_not_resolved",
                error_code = UpdateErrorCodes.UpdateTargetReleaseNotResolved,
                installedVersion = installed.Version,
                targetVersion = jobContext.TargetVersion,
                availableVersion = manifest.Version
            };
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, state, "failed", 25, installed.Version, installed.Version, message, mismatch, "");
            }
            WriteJson(mismatch);
            return 25;
        }

        VersionUpdateAction versionAction = DecideVersionAction(installed.Version, manifest.Version, manifest.Force);
        if (versionAction == VersionUpdateAction.DowngradeBlocked)
        {
            string message = $"Explicit target {manifest.Version} is older than installed version {installed.Version}.";
            MarkFailed(state, UpdateErrorCodes.UpdateTargetReleaseNotResolved, message);
            var targetOlder = new
            {
                ok = false,
                updated = false,
                reason = "downgrade_blocked",
                error_code = UpdateErrorCodes.UpdateTargetReleaseNotResolved,
                installedVersion = installed.Version,
                targetVersion = manifest.Version,
                availableVersion = manifest.Version
            };
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, state, "failed", 25, installed.Version, installed.Version, message, targetOlder, "");
            }
            WriteJson(targetOlder);
            return 25;
        }
        if (versionAction == VersionUpdateAction.AlreadyCurrent)
        {
            WriteLog("updater.update.skipped", "Nenhuma atualizacao disponivel.", new { installed = installed.Version, available = manifest.Version });
            state.HealthCheckConfirmed = true;
            MarkStage(state, UpdateStages.Completed, UpdateStatuses.Completed);
            var skipped = new { ok = true, updated = false, reason = "already_current", installedVersion = installed.Version, targetVersion = manifest.Version, availableVersion = manifest.Version };
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, state, "completed", 10, installed.Version, installed.Version, "Agent already up to date.", skipped, "");
            }
            WriteJson(skipped);
            if (interactive)
            {
                ShowMessage("O agente ja esta atualizado.", MessageBoxIcon.Information);
            }
            return jobContext.IsJob ? 10 : 0;
        }

        if (!IsAdministrator())
        {
            WriteLog("updater.update.elevation_required", "Atualizacao requer elevacao administrativa.");
            RelaunchElevated(BuildRunnerArguments(args, updateId));
            WriteJson(new { ok = true, elevated = true, message = "Updater relancado como administrador." });
            return 0;
        }

        ChecksumsManifest checksums = explicitChecksums ?? await DownloadChecksumsAsync(config, manifest);
        string packageUrl = state.PackageUrl;
        EnsurePackageUrlAllowed(config, packageUrl);
        state.PackageUrl = packageUrl;

        string downloadPath = Path.Combine(DownloadsRoot, "NightOwl.Agent.Windows.zip");
        MarkStage(state, UpdateStages.Downloading);
        WriteLog("package.download", "Downloading update package.", new { update_id = state.UpdateId, job_id = state.JobId, stage = state.CurrentStage, url = SanitizeUrl(packageUrl), downloadPath });
        try
        {
            await DownloadFileAsync(packageUrl, downloadPath);
        }
        catch (Exception ex)
        {
            MarkFailed(state, UpdateErrorCodes.UpdateDownloadFailed, ex.Message);
            throw;
        }
        MarkStage(state, UpdateStages.Downloaded);
        FileChecksum packageChecksum = checksums.GetRequired("NightOwl.Agent.Windows.zip");
        state.ExpectedSha256 = packageChecksum.Sha256;
        MarkStage(state, UpdateStages.Validating);
        try
        {
            ValidateFile(downloadPath, packageChecksum);
        }
        catch (Exception ex)
        {
            MarkFailed(state, UpdateErrorCodes.UpdateHashMismatch, ex.Message);
            throw;
        }
        MarkStage(state, UpdateStages.Validated);
        WriteLog("checksum.ok", "Package checksum validated.", new { packageChecksum.Sha256, packageChecksum.Size });

        string stagingPath = Path.Combine(StagingRoot, SanitizePathSegment(manifest.Version));
        state.StagingPath = stagingPath;
        MarkStage(state, UpdateStages.Staging);
        if (Directory.Exists(stagingPath))
        {
            Directory.Delete(stagingPath, recursive: true);
        }
        Directory.CreateDirectory(stagingPath);
        try
        {
            ExtractZipSafe(downloadPath, stagingPath);
        }
        catch (Exception ex)
        {
            MarkFailed(state, UpdateErrorCodes.UpdatePackageInvalid, ex.Message);
            throw;
        }
        MarkStage(state, UpdateStages.Staged);
        WriteLog("staging.ready", "Staging directory ready.", new { stagingPath, version = manifest.Version });

        string manifestPath = Path.Combine(stagingPath, "version.json");
        File.WriteAllText(manifestPath, JsonSerializer.Serialize(manifest, JsonOptions));

        string stagedUpdater = Path.Combine(stagingPath, "NightOwl.Agent.Updater.exe");
        if (!File.Exists(stagedUpdater))
        {
            throw new FileNotFoundException("Pacote sem NightOwl.Agent.Updater.exe.", stagedUpdater);
        }

        WriteLog("updater.update.staged", "Pacote baixado, validado e extraido.", new { stagingPath, version = manifest.Version });
        return ApplyStagedUpdate(stagingPath, manifestPath, packageChecksum.Sha256, interactive);
    }

    private static int LaunchIndependentRunner(string[] args, bool interactive, string updateId)
    {
        string runnerExe = CopyRunnerFiles();
        string arguments = BuildRunnerArguments(args, updateId);
        WriteLog("runner.start", "Starting independent updater runner.", new { runnerExe, arguments = SanitizeCommandLine(arguments) });

        ProcessStartInfo psi = new()
        {
            FileName = runnerExe,
            Arguments = arguments,
            WorkingDirectory = RunnerRoot,
            UseShellExecute = true
        };

        if (!IsAdministrator())
        {
            psi.Verb = "runas";
        }

        Process.Start(psi);
        WriteJson(new { ok = true, runnerStarted = true, runner = runnerExe });
        if (interactive)
        {
            ShowMessage("Atualizacao iniciada. O servico NightOwl pode reiniciar durante o processo.", MessageBoxIcon.Information);
        }
        return 0;
    }

    private static string CopyRunnerFiles()
    {
        Directory.CreateDirectory(RunnerRoot);
        foreach (string path in Directory.EnumerateFileSystemEntries(RunnerRoot))
        {
            try
            {
                if (Directory.Exists(path))
                {
                    Directory.Delete(path, recursive: true);
                }
                else
                {
                    File.Delete(path);
                }
            }
            catch (Exception ex)
            {
                WriteLog("runner.cleanup_failed", "Failed to clean previous runner file.", new { path, error = ex.Message });
            }
        }

        CopyDirectory(AppContext.BaseDirectory, RunnerRoot, overwrite: true, excludeNames: ProtectedInstallFileNames);
        string runnerExe = Path.Combine(RunnerRoot, "NightOwl.Agent.Updater.exe");
        if (!File.Exists(runnerExe))
        {
            throw new FileNotFoundException("NightOwl.Agent.Updater.exe nao foi copiado para o runner.", runnerExe);
        }
        WriteLog("runner.copy", "Independent runner copied.", new { source = AppContext.BaseDirectory, runner = RunnerRoot });
        return runnerExe;
    }

    private static string BuildRunnerArguments(string[] args, string updateId)
    {
        List<string> filtered = new();
        bool commandAdded = false;
        foreach (string arg in args)
        {
            if (!commandAdded && !arg.StartsWith("--", StringComparison.OrdinalIgnoreCase))
            {
                filtered.Add(arg);
                commandAdded = true;
                continue;
            }
            if (arg.Equals("--runner", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            filtered.Add(arg);
        }
        if (!commandAdded)
        {
            filtered.Insert(0, "update");
        }
        filtered.Add("--runner");
        if (!filtered.Any(arg => arg.Equals("--update-id", StringComparison.OrdinalIgnoreCase)))
        {
            filtered.Add("--update-id");
            filtered.Add(updateId);
        }
        return string.Join(" ", filtered.Select(QuoteArg));
    }

    private static bool HasActiveUpdate(out UpdateState? active, out string invalidError)
    {
        active = null;
        invalidError = "";
        if (!UpdateStateStore.TryLoad(out UpdateState? state, out string error))
        {
            invalidError = error;
            return false;
        }

        if (state is not null && state.IsActive)
        {
            active = state;
            return true;
        }

        return false;
    }

    private static UpdateState LoadOrCreateRunnerState(string updateId, JobContext jobContext)
    {
        if (!UpdateStateStore.TryLoad(out UpdateState? loaded, out string error))
        {
            WriteLog("update.state.invalid", "Invalid update state detected.", new { error_code = UpdateErrorCodes.UpdateStateInvalid, error });
            UpdateState invalid = UpdateState.Create(updateId, jobContext.JobId, "", "");
            invalid.MarkFailed(UpdateErrorCodes.UpdateStateInvalid, error);
            UpdateStateStore.Save(invalid);
            throw new InvalidOperationException(error);
        }

        if (loaded is null)
        {
            UpdateState created = UpdateState.Create(updateId, jobContext.JobId, "", "");
            UpdateStateStore.Save(created);
            return created;
        }

        if (loaded.IsActive && !string.IsNullOrWhiteSpace(updateId) && !loaded.UpdateId.Equals(updateId, StringComparison.OrdinalIgnoreCase))
        {
            WriteLog("update.already_running", "Different active update detected.", new { error_code = UpdateErrorCodes.UpdateAlreadyRunning, active_update_id = loaded.UpdateId, requested_update_id = updateId });
            throw new InvalidOperationException($"{UpdateErrorCodes.UpdateAlreadyRunning}: {loaded.UpdateId}");
        }

        if (loaded.IsActive)
        {
            return loaded;
        }

        int nextAttempt = loaded.Attempt + 1;
        UpdateState replacement = UpdateState.Create(string.IsNullOrWhiteSpace(updateId) ? Guid.NewGuid().ToString() : updateId, jobContext.JobId, loaded.FromVersion, loaded.TargetVersion);
        replacement.Attempt = nextAttempt;
        UpdateStateStore.Save(replacement);
        return replacement;
    }

    private static void MarkStage(UpdateState state, string stage, string status = UpdateStatuses.Running)
    {
        DateTimeOffset previous = state.UpdatedAt == default ? DateTimeOffset.UtcNow : state.UpdatedAt;
        state.MarkStage(stage, status);
        UpdateStateStore.Save(state);
        WriteLog("update.stage", "Update stage persisted.", new
        {
            update_id = state.UpdateId,
            job_id = state.JobId,
            stage,
            status = state.Status,
            from_version = state.FromVersion,
            target_version = state.TargetVersion,
            duration_seconds = Math.Round((state.UpdatedAt - previous).TotalSeconds, 3)
        });
    }

    private static void MarkFailed(UpdateState state, string errorCode, string errorMessage, bool rollbackRequired = false)
    {
        DateTimeOffset previous = state.UpdatedAt == default ? DateTimeOffset.UtcNow : state.UpdatedAt;
        state.MarkFailed(errorCode, SanitizeMessage(errorMessage), rollbackRequired);
        UpdateStateStore.Save(state);
        WriteLog("update.stage", "Update failed state persisted.", new
        {
            update_id = state.UpdateId,
            job_id = state.JobId,
            stage = state.CurrentStage,
            status = state.Status,
            from_version = state.FromVersion,
            target_version = state.TargetVersion,
            error_code = state.ErrorCode,
            duration_seconds = Math.Round((state.UpdatedAt - previous).TotalSeconds, 3)
        });
    }

    private static void MarkRollbackRequired(UpdateState state, string failureStage, string errorCode, string errorMessage)
    {
        DateTimeOffset previous = state.UpdatedAt == default ? DateTimeOffset.UtcNow : state.UpdatedAt;
        state.MarkRollbackRequired(failureStage, errorCode, SanitizeMessage(errorMessage));
        UpdateStateStore.Save(state);
        WriteLog("rollback.required", "Rollback required.", new
        {
            update_id = state.UpdateId,
            job_id = state.JobId,
            stage = state.CurrentStage,
            failure_stage = failureStage,
            from_version = state.FromVersion,
            target_version = state.TargetVersion,
            error_code = state.ErrorCode,
            duration_seconds = Math.Round((state.UpdatedAt - previous).TotalSeconds, 3)
        });
    }

    private static UpdateState ReloadUpdateState(UpdateState fallback)
    {
        return UpdateStateStore.TryLoad(out UpdateState? current, out _) && current is not null
            ? current
            : fallback;
    }

    private static int ApplyStagedUpdate(string stagedPath, string manifestPath, string packageSha256, bool interactive)
    {
        if (!IsAdministrator())
        {
            throw new UnauthorizedAccessException("Aplicacao da atualizacao requer administrador.");
        }

        AgentConfig config = LoadConfig();
        JobContext jobContext = JobContext.FromArgs(Environment.GetCommandLineArgs());
        UpdateManifest manifest = JsonSerializer.Deserialize<UpdateManifest>(File.ReadAllText(manifestPath), JsonOptions)
            ?? throw new InvalidOperationException("Manifesto staged invalido.");
        AgentVersionInfo installed = LoadInstalledVersion(config);
        string installPath = config.InstallPathOrDefault;
        UpdateState state = LoadOrCreateRunnerState(GetOption(Environment.GetCommandLineArgs(), "--update-id") ?? "", jobContext);
        string backupPath = Path.Combine(BackupsRoot, state.UpdateId);
        state.FromVersion = installed.Version;
        state.TargetVersion = manifest.Version;
        state.StagingPath = stagedPath;
        state.BackupPath = backupPath;
        state.ExpectedSha256 = packageSha256;
        UpdateStateStore.Save(state);

        WriteLog("updater.apply.started", "Aplicando atualizacao staged.", new { update_id = state.UpdateId, job_id = state.JobId, stagedPath, installPath, backupPath, from = installed.Version, to = manifest.Version });

        try
        {
            MarkStage(state, UpdateStages.CreatingBackup);
            try
            {
                CreateBackup(installPath, backupPath, state.UpdateId, installed.Version);
                ValidateBackup(backupPath, state.UpdateId, installed.Version);
            }
            catch (Exception ex)
            {
                MarkFailed(state, UpdateErrorCodes.UpdateBackupFailed, ex.Message);
                throw;
            }
            MarkStage(state, UpdateStages.BackupCreated);
            WriteLog("backup.created", "Install backup created.", new { backupPath });
            StopTray();
            WriteLog("tray.stop.done", "Tray process stopped.");
            WriteLog("service.stop.start", "Stopping service for update.");
            MarkStage(state, UpdateStages.StoppingService);
            try
            {
                StopService();
            }
            catch (Exception ex)
            {
                MarkFailed(state, UpdateErrorCodes.UpdateServiceStopTimeout, ex.Message);
                throw;
            }
            MarkStage(state, UpdateStages.ServiceStopped);
            WriteLog("service.stop.done", "Service stopped for update.");
            MarkStage(state, UpdateStages.Quiescing);
            WriteLog("update.quiesce.start", "Waiting for NightOwl processes and install files to become idle.", new { installPath, runnerPath = RunnerRoot });
            WaitForNightOwlProcessesToExit(
                new[] { installPath, RunnerRoot },
                TimeSpan.FromSeconds(GetOptionInt(Environment.GetCommandLineArgs(), "--quiesce-timeout-seconds", DefaultQuiesceTimeoutSeconds)));
            WaitForInstallFilesReady(
                stagedPath,
                installPath,
                TimeSpan.FromSeconds(GetOptionInt(Environment.GetCommandLineArgs(), "--file-ready-timeout-seconds", DefaultFileReplaceTimeoutSeconds)));
            WriteLog("update.files.ready", "Install files are ready for replacement.", new { stagedPath, installPath });
            WriteLog("files.copy.start", "Copying staged files to install path.", new { stagedPath, installPath });
            MarkStage(state, UpdateStages.ReplacingFiles);
            try
            {
                CopyStagedFilesWithRetry(
                    stagedPath,
                    installPath,
                    TimeSpan.FromSeconds(GetOptionInt(Environment.GetCommandLineArgs(), "--file-replace-timeout-seconds", DefaultFileReplaceTimeoutSeconds)));
            }
            catch (Exception ex)
            {
                string errorCode = ex is FileReplaceException fileReplaceException
                    ? fileReplaceException.ErrorCode
                    : ErrorCodeFromException(ex, UpdateErrorCodes.UpdateFileReplaceFailed);
                MarkRollbackRequired(state, state.CurrentStage, errorCode, ex.Message);
                throw;
            }
            MarkStage(state, UpdateStages.FilesReplaced);
            WriteLog("files.copy.done", "Staged files copied to install path.", new { stagedPath, installPath });
            WriteLocalVersion(installPath, manifest, packageSha256, "updater");
            MarkStage(state, UpdateStages.StartingService);
            try
            {
                StartService();
            }
            catch (Exception ex)
            {
                MarkRollbackRequired(state, state.CurrentStage, UpdateErrorCodes.UpdateServiceStartFailed, ex.Message);
                throw;
            }
            MarkStage(state, UpdateStages.ServiceStarted);
            WriteLog("service.start.done", "Service started after update.");
            StartTrayIfPossible(installPath);
            try
            {
                ValidatePostUpdate(installPath, manifest.Version);
            }
            catch (Exception ex)
            {
                MarkRollbackRequired(state, state.CurrentStage, UpdateErrorCodes.UpdateHealthcheckVersionMismatch, ex.Message);
                throw;
            }
            MarkStage(state, UpdateStages.WaitingHealthCheck);

            WriteLog("updater.apply.waiting_health_check", "Servico iniciado; aguardando confirmacao do agente.", new { update_id = state.UpdateId, job_id = state.JobId, version = manifest.Version, previous_version = installed.Version });
            int healthTimeoutSeconds = GetOptionInt(Environment.GetCommandLineArgs(), "--health-timeout-seconds", DefaultHealthCheckTimeoutSeconds);
            HealthCheckWaitResult health = WaitForHealthCheck(state, expectRollback: false, TimeSpan.FromSeconds(healthTimeoutSeconds));
            if (health == HealthCheckWaitResult.Completed)
            {
                CleanupStaging(stagedPath);
                WriteLog("update.completed", "Update completed after agent health check.", new { update_id = state.UpdateId, job_id = state.JobId, version = manifest.Version, previous_version = installed.Version });
                WriteJson(new { ok = true, updated = true, healthCheckConfirmed = true, update_id = state.UpdateId, version = manifest.Version, backupPath });
                if (interactive)
                {
                    ShowMessage($"NightOwl Agent atualizado para {manifest.Version}.", MessageBoxIcon.Information);
                }
                return 0;
            }

            string healthErrorCode = health == HealthCheckWaitResult.ServiceExitedEarly
                ? UpdateErrorCodes.UpdateProcessExitedEarly
                : health == HealthCheckWaitResult.FailedState && !string.IsNullOrWhiteSpace(ReloadUpdateState(state).ErrorCode)
                    ? ReloadUpdateState(state).ErrorCode
                    : UpdateErrorCodes.UpdateHealthcheckTimeout;
            MarkRollbackRequired(state, UpdateStages.WaitingHealthCheck, healthErrorCode, $"Health check failed or timed out after {healthTimeoutSeconds}s.");
            return ExecuteAutomaticRollback(state, installPath, interactive);
        }
        catch (Exception ex) when (state.RollbackRequired && state.RollbackAttempt < 1)
        {
            WriteLog("update.rollback.triggered", "Update failed after replacement point; starting automatic rollback.", new { update_id = state.UpdateId, job_id = state.JobId, stage = state.CurrentStage, error_code = state.ErrorCode, error = ex.Message });
            return ExecuteAutomaticRollback(state, installPath, interactive);
        }
        catch (Exception ex)
        {
            WriteLog("updater.apply.failed", "Falha ao aplicar atualizacao.", new { update_id = state.UpdateId, job_id = state.JobId, error = ex.Message, backupPath });
            if (state.IsActive)
            {
                MarkFailed(state, string.IsNullOrWhiteSpace(state.ErrorCode) ? UpdateErrorCodes.UpdateUnexpectedError : state.ErrorCode, SanitizeMessage(ex.Message), rollbackRequired: false);
            }
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, state, "failed", 20, installed.Version, installed.Version, SanitizeMessage(ex.Message), new { updated = false, backupPath, update_id = state.UpdateId, error_code = state.ErrorCode }, SanitizeMessage(ex.ToString()));
            }
            WriteJson(new { ok = false, updated = false, error = ex.Message, backupPath });
            if (interactive)
            {
                ShowMessage("Falha ao atualizar. Detalhe: " + ex.Message, MessageBoxIcon.Error);
            }
            return 1;
        }
    }

    private static int RunStatus(bool interactive)
    {
        AgentConfig config = LoadConfig();
        AgentVersionInfo installed = LoadInstalledVersion(config);
        string serviceStatus = GetServiceStatus();
        var result = new
        {
            ok = true,
            installPath = config.InstallPathOrDefault,
            installedVersion = installed.Version,
            installed.InstalledAt,
            installed.Channel,
            service = ServiceName,
            serviceStatus,
            server = config.ServerBaseUrlOrDefault
        };
        WriteJson(result);
        WriteLog("updater.status.completed", "Status coletado.", result);
        if (interactive)
        {
            ShowMessage($"NightOwl Agent\n\nVersao: {installed.Version}\nServico: {serviceStatus}\nServidor: {config.ServerBaseUrlOrDefault}", MessageBoxIcon.Information);
        }
        return 0;
    }

    private static int RunRollback(bool interactive)
    {
        if (!IsAdministrator())
        {
            WriteLog("updater.rollback.elevation_required", "Rollback requer elevacao administrativa.");
            RelaunchElevated("rollback --interactive");
            return 0;
        }

        AgentConfig config = LoadConfig();
        string installPath = config.InstallPathOrDefault;
        DirectoryInfo? latest = new DirectoryInfo(BackupsRoot)
            .GetDirectories()
            .OrderByDescending(d => d.CreationTimeUtc)
            .FirstOrDefault();
        if (latest is null)
        {
            WriteJson(new { ok = false, error = "no_backup_available" });
            if (interactive)
            {
                ShowMessage("Nenhum backup disponivel para rollback.", MessageBoxIcon.Warning);
            }
            return 1;
        }

        try
        {
            StopTray();
            StopService();
            RestoreBackup(latest.FullName, installPath);
            StartService();
            StartTrayIfPossible(installPath);
            WriteLog("updater.rollback.completed", "Rollback concluido.", new { backup = latest.FullName });
            WriteJson(new { ok = true, rollback = true, backup = latest.FullName });
            if (interactive)
            {
                ShowMessage("Rollback concluido.", MessageBoxIcon.Information);
            }
            return 0;
        }
        catch (Exception ex)
        {
            WriteLog("updater.rollback.failed", "Rollback falhou.", new { backup = latest.FullName, error = ex.Message });
            WriteJson(new { ok = false, error = ex.Message });
            if (interactive)
            {
                ShowMessage("Rollback falhou: " + ex.Message, MessageBoxIcon.Error);
            }
            return 1;
        }
    }

    private static int RunUsage(string command, bool interactive)
    {
        string message = $"Comando invalido: {command}. Use check, update, status ou rollback.";
        WriteJson(new { ok = false, error = message });
        if (interactive)
        {
            ShowMessage(message, MessageBoxIcon.Warning);
        }
        return 2;
    }

    private static AgentConfig LoadConfig()
    {
        string configPath = Paths.ResolveConfigPath();

        AgentConfig config = File.Exists(configPath)
            ? JsonSerializer.Deserialize<AgentConfig>(File.ReadAllText(configPath), JsonOptions) ?? new AgentConfig()
            : new AgentConfig();
        config.ConfigPath = configPath;
        if (string.IsNullOrWhiteSpace(config.InstallPath))
        {
            config.InstallPath = Paths.InstallDir;
        }
        return config;
    }

    private static AgentVersionInfo LoadInstalledVersion(AgentConfig config)
    {
        string versionPath = Path.Combine(config.InstallPathOrDefault, "agent.version.json");
        if (File.Exists(versionPath))
        {
            try
            {
                AgentVersionInfo? version = JsonSerializer.Deserialize<AgentVersionInfo>(File.ReadAllText(versionPath), JsonOptions);
                if (version is not null && !string.IsNullOrWhiteSpace(version.Version))
                {
                    return version;
                }
            }
            catch (Exception ex)
            {
                WriteLog("updater.version.read_failed", "Falha ao ler agent.version.json.", new { error = ex.Message });
            }
        }

        return new AgentVersionInfo
        {
            Version = string.IsNullOrWhiteSpace(config.AgentVersion) ? "0.0.0" : config.AgentVersion,
            InstalledAt = "",
            Channel = "stable",
            UpdatedBy = "unknown"
        };
    }

    private static async Task<UpdateManifest> DownloadManifestAsync(AgentConfig config)
    {
        string url = JoinUrl(config.ServerBaseUrlOrDefault, "/downloads/nightowl-agent/version.json");
        using HttpClient http = new() { Timeout = TimeSpan.FromSeconds(30) };
        string json = await http.GetStringAsync(url);
        UpdateManifest manifest = JsonSerializer.Deserialize<UpdateManifest>(json, JsonOptions)
            ?? throw new InvalidOperationException("version.json invalido.");
        if (string.IsNullOrWhiteSpace(manifest.PackageUrl))
        {
            manifest.PackageUrl = JoinUrl(config.ServerBaseUrlOrDefault, "/downloads/nightowl-agent/NightOwl.Agent.Windows.zip");
        }
        if (string.IsNullOrWhiteSpace(manifest.ChecksumUrl))
        {
            manifest.ChecksumUrl = JoinUrl(config.ServerBaseUrlOrDefault, "/downloads/nightowl-agent/checksums.json");
        }
        if (string.IsNullOrWhiteSpace(manifest.InstallerUrl))
        {
            manifest.InstallerUrl = JoinUrl(config.ServerBaseUrlOrDefault, "/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1");
        }
        return manifest;
    }

    private static UpdateManifest BuildManifestFromJobContext(AgentConfig config, JobContext jobContext)
    {
        if (!jobContext.HasExplicitReleaseMetadata)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.UpdateReleaseMetadataMissing}: release metadata is incomplete.");
        }
        if (!RegexSha256(jobContext.Sha256))
        {
            throw new InvalidOperationException("Invalid package sha256 in release metadata.");
        }
        if (jobContext.Size < 0)
        {
            throw new InvalidOperationException("Invalid package size in release metadata.");
        }
        EnsurePackageUrlAllowed(config, jobContext.PackageUrl);
        if (!string.IsNullOrWhiteSpace(jobContext.ChecksumUrl))
        {
            EnsurePackageUrlAllowed(config, jobContext.ChecksumUrl);
        }
        EnsureMinimumUpdaterVersion(jobContext.MinimumUpdaterVersion);

        return new UpdateManifest
        {
            Product = ProductName,
            Channel = string.IsNullOrWhiteSpace(jobContext.Channel) ? "stable" : jobContext.Channel,
            Version = jobContext.TargetVersion,
            PublishedAt = DateTimeOffset.UtcNow.ToString("O"),
            MinimumSupportedVersion = jobContext.MinimumUpdaterVersion,
            MinimumUpdaterVersion = jobContext.MinimumUpdaterVersion,
            PackageUrl = jobContext.PackageUrl,
            ChecksumUrl = jobContext.ChecksumUrl,
            ManifestUrl = string.IsNullOrWhiteSpace(jobContext.ManifestUrl) ? InferSiblingUrl(jobContext.PackageUrl, "release-manifest.json") : jobContext.ManifestUrl,
            SignatureUrl = string.IsNullOrWhiteSpace(jobContext.SignatureUrl) ? InferSiblingUrl(jobContext.PackageUrl, "release-manifest.sig") : jobContext.SignatureUrl,
            ManifestSha256 = jobContext.ManifestSha256,
            SignatureSha256 = jobContext.SignatureSha256,
            KeyId = jobContext.SignatureKeyId,
            Sha256 = jobContext.Sha256,
            Size = jobContext.Size,
            Notes = string.IsNullOrWhiteSpace(jobContext.ReleaseId) ? "Explicit update_agent release." : $"Explicit release {jobContext.ReleaseId}.",
            RequiresRestart = true,
            Force = jobContext.Force
        };
    }

    private static async Task ValidateReleaseManifestAsync(AgentConfig config, UpdateManifest manifest)
    {
        string manifestUrl = string.IsNullOrWhiteSpace(manifest.ManifestUrl)
            ? InferSiblingUrl(manifest.PackageUrl, "release-manifest.json")
            : manifest.ManifestUrl;
        string signatureUrl = string.IsNullOrWhiteSpace(manifest.SignatureUrl)
            ? InferSiblingUrl(manifest.PackageUrl, "release-manifest.sig")
            : manifest.SignatureUrl;
        bool legacyUnsigned = manifest.LegacyUnsigned || string.IsNullOrWhiteSpace(manifest.KeyId);
        if (legacyUnsigned && manifest.Channel.Equals("development", StringComparison.OrdinalIgnoreCase))
        {
            WriteLog("release.manifest.legacy_unsigned", "Legacy unsigned development release accepted with warning.", new { version = manifest.Version, channel = manifest.Channel });
            return;
        }
        if (legacyUnsigned && manifest.Channel.Equals("stable", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseSignatureMissing}: stable release requires signature.");
        }
        EnsurePackageUrlAllowed(config, manifestUrl);
        EnsurePackageUrlAllowed(config, signatureUrl);

        using HttpClient http = new() { Timeout = TimeSpan.FromSeconds(30) };
        byte[] manifestBytes;
        try
        {
            manifestBytes = await http.GetByteArrayAsync(manifestUrl);
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseManifestMissing}: {ex.Message}", ex);
        }
        if (!string.IsNullOrWhiteSpace(manifest.ManifestSha256))
        {
            string actualManifestSha = Convert.ToHexString(SHA256.HashData(manifestBytes)).ToLowerInvariant();
            if (!actualManifestSha.Equals(manifest.ManifestSha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseManifestInvalid}: manifest sha256 mismatch.");
            }
        }
        ReleaseManifest signedManifest;
        try
        {
            signedManifest = JsonSerializer.Deserialize<ReleaseManifest>(manifestBytes, JsonOptions)
                ?? throw new InvalidOperationException("manifest empty");
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseManifestInvalid}: {ex.Message}", ex);
        }
        if (!signedManifest.Version.Equals(manifest.Version, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseVersionMismatch}: manifest version {signedManifest.Version} != {manifest.Version}");
        }
        if (!signedManifest.Channel.Equals(manifest.Channel, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseChannelMismatch}: manifest channel {signedManifest.Channel} != {manifest.Channel}");
        }
        if (!signedManifest.Package.Sha256.Equals(GetManifestPackageSha(manifest), StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseHashMismatch}: manifest package hash mismatch.");
        }
        if (manifest.Size > 0 && signedManifest.Package.Size > 0 && signedManifest.Package.Size != manifest.Size)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseSizeMismatch}: manifest package size mismatch.");
        }
        byte[] signatureBytes;
        byte[] signature;
        try
        {
            signatureBytes = await http.GetByteArrayAsync(signatureUrl);
            string signatureText = System.Text.Encoding.UTF8.GetString(signatureBytes);
            signature = Convert.FromBase64String(signatureText.Trim());
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseSignatureMissing}: {ex.Message}", ex);
        }
        if (!string.IsNullOrWhiteSpace(manifest.SignatureSha256))
        {
            string actualSignatureSha = Convert.ToHexString(SHA256.HashData(signatureBytes)).ToLowerInvariant();
            if (!actualSignatureSha.Equals(manifest.SignatureSha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseSignatureInvalid}: signature sha256 mismatch.");
            }
        }
        TrustedReleaseKey key = LoadTrustedReleaseKey(signedManifest.KeyId);
        if (key.Revoked)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseKeyUnknown}: key {signedManifest.KeyId} is revoked.");
        }
        bool valid = VerifyReleaseManifestSignature(manifestBytes, signature, key.PublicKeyXml);
        if (!valid)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseSignatureInvalid}: manifest signature invalid.");
        }
        manifest.KeyId = signedManifest.KeyId;
        manifest.LegacyUnsigned = false;
        WriteLog("release.signature.valid", "Release manifest signature validated.", new { version = manifest.Version, channel = manifest.Channel, key_id = signedManifest.KeyId });
    }

    internal static bool VerifyReleaseManifestSignatureForTest(byte[] manifestBytes, byte[] signature, string publicKeyXml)
        => VerifyReleaseManifestSignature(manifestBytes, signature, publicKeyXml);

    private static bool VerifyReleaseManifestSignature(byte[] manifestBytes, byte[] signature, string publicKeyXml)
    {
        using RSA rsa = CreateRsaPssPublicKeyFromXml(publicKeyXml);
        return rsa.VerifyData(manifestBytes, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pss);
    }

    private static RSA CreateRsaPssPublicKeyFromXml(string publicKeyXml)
    {
        try
        {
            using RSACryptoServiceProvider legacyProvider = new();
            legacyProvider.PersistKeyInCsp = false;
            legacyProvider.FromXmlString(publicKeyXml);
            RSAParameters parameters = legacyProvider.ExportParameters(false);
            RSACng cng = new();
            cng.ImportParameters(parameters);
            return cng;
        }
        catch (PlatformNotSupportedException ex)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseKeyUnknown}: RSA-PSS provider unavailable.", ex);
        }
        catch (CryptographicException ex)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseKeyUnknown}: invalid trusted RSA public key.", ex);
        }
    }

    private static string GetManifestPackageSha(UpdateManifest manifest)
    {
        return string.IsNullOrWhiteSpace(manifest.Sha256) ? "" : manifest.Sha256;
    }

    private static TrustedReleaseKey LoadTrustedReleaseKey(string keyId)
    {
        if (string.IsNullOrWhiteSpace(keyId))
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseKeyUnknown}: key_id missing.");
        }
        string keyPath = Path.Combine(AppContext.BaseDirectory, "release-public-keys.json");
        if (!File.Exists(keyPath))
        {
            keyPath = Paths.TrustBundlePath;
        }
        if (!File.Exists(keyPath))
        {
            keyPath = Path.Combine(Paths.InstallDir, "release-public-keys.json");
        }
        if (!File.Exists(keyPath))
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseKeyUnknown}: trusted key store not found.");
        }
        TrustedReleaseKeySet? keySet = JsonSerializer.Deserialize<TrustedReleaseKeySet>(File.ReadAllText(keyPath), JsonOptions);
        TrustedReleaseKey? key = keySet?.Keys.FirstOrDefault(item =>
            item.KeyId.Equals(keyId, StringComparison.OrdinalIgnoreCase)
            && item.Status.Equals("active", StringComparison.OrdinalIgnoreCase));
        if (key is null)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.ReleaseKeyUnknown}: key {keyId} not trusted.");
        }
        return key;
    }

    private static string InferSiblingUrl(string url, string fileName)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out Uri? uri))
        {
            return "";
        }
        string left = uri.GetLeftPart(UriPartial.Path);
        int slash = left.LastIndexOf('/');
        return slash >= 0 ? left[..(slash + 1)] + fileName : "";
    }

    private static string ErrorCodeFromException(Exception ex, string fallback)
    {
        string message = ex.Message ?? "";
        foreach (string code in new[]
        {
            UpdateErrorCodes.UpdateMinimumUpdaterVersionNotMet,
            UpdateErrorCodes.ReleaseManifestMissing,
            UpdateErrorCodes.ReleaseManifestInvalid,
            UpdateErrorCodes.ReleaseSignatureMissing,
            UpdateErrorCodes.ReleaseSignatureInvalid,
            UpdateErrorCodes.ReleaseKeyUnknown,
            UpdateErrorCodes.ReleaseVersionMismatch,
            UpdateErrorCodes.ReleaseChannelMismatch,
            UpdateErrorCodes.ReleaseHashMismatch,
            UpdateErrorCodes.ReleaseSizeMismatch,
            UpdateErrorCodes.UpdateFileLockTimeout,
            UpdateErrorCodes.UpdateFileAccessDenied,
        })
        {
            if (message.Contains(code, StringComparison.OrdinalIgnoreCase))
            {
                return code;
            }
        }
        return fallback;
    }

    private static void EnsureMinimumUpdaterVersion(string minimumUpdaterVersion)
    {
        if (string.IsNullOrWhiteSpace(minimumUpdaterVersion))
        {
            return;
        }
        string current = GetCurrentUpdaterVersion();
        if (CompareVersions(current, minimumUpdaterVersion) < 0)
        {
            throw new InvalidOperationException($"{UpdateErrorCodes.UpdateMinimumUpdaterVersionNotMet}: updater {current} is lower than required {minimumUpdaterVersion}.");
        }
    }

    private static string GetCurrentUpdaterVersion()
    {
        try
        {
            string? informational = typeof(Program).Assembly
                .GetCustomAttributes(typeof(System.Reflection.AssemblyInformationalVersionAttribute), inherit: false)
                .OfType<System.Reflection.AssemblyInformationalVersionAttribute>()
                .FirstOrDefault()?
                .InformationalVersion;
            string version = (informational ?? typeof(Program).Assembly.GetName().Version?.ToString() ?? "").Split('+')[0];
            return string.IsNullOrWhiteSpace(version) ? "0.0.0" : version;
        }
        catch
        {
            return "0.0.0";
        }
    }

    private static bool RegexSha256(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length != 64)
        {
            return false;
        }
        return value.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'));
    }

    private static async Task<ChecksumsManifest> DownloadChecksumsAsync(AgentConfig config, UpdateManifest manifest)
    {
        string url = string.IsNullOrWhiteSpace(manifest.ChecksumUrl)
            ? JoinUrl(config.ServerBaseUrlOrDefault, "/downloads/nightowl-agent/checksums.json")
            : manifest.ChecksumUrl;
        EnsurePackageUrlAllowed(config, url);
        using HttpClient http = new() { Timeout = TimeSpan.FromSeconds(30) };
        string json = await http.GetStringAsync(url);
        return ChecksumsManifest.Parse(json);
    }

    private static async Task DownloadFileAsync(string url, string path)
    {
        WriteLog("updater.download.started", "Baixando pacote.", new { url = SanitizeUrl(url), path });
        using HttpClient http = new() { Timeout = TimeSpan.FromMinutes(5) };
        await using Stream input = await http.GetStreamAsync(url);
        await using FileStream output = File.Create(path);
        await input.CopyToAsync(output);
        WriteLog("updater.download.completed", "Download concluido.", new { path, bytes = new FileInfo(path).Length });
    }

    private static void ValidateFile(string path, FileChecksum checksum)
    {
        FileInfo file = new(path);
        if (!file.Exists)
        {
            throw new FileNotFoundException("Arquivo baixado nao encontrado.", path);
        }
        if (checksum.Size > 0 && file.Length != checksum.Size)
        {
            throw new InvalidOperationException($"Tamanho invalido para {file.Name}. Esperado {checksum.Size}, obtido {file.Length}.");
        }
        string actual = Sha256(path);
        if (!actual.Equals(checksum.Sha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException($"SHA256 invalido para {file.Name}.");
        }
        WriteLog("updater.package.validated", "Checksum do pacote validado.", new { file = file.Name, file.Length, sha256 = actual });
    }

    private static void CreateBackup(string installPath, string backupPath, string updateId, string previousVersion)
    {
        if (Directory.Exists(backupPath))
        {
            Directory.Delete(backupPath, recursive: true);
        }
        Directory.CreateDirectory(backupPath);

        List<BackupFileEntry> files = new();
        foreach (string sourceFile in EnumerateManagedFiles(installPath))
        {
            string relativePath = Path.GetRelativePath(installPath, sourceFile);
            string destination = Path.Combine(backupPath, relativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(sourceFile, destination, overwrite: true);
            FileInfo info = new(destination);
            files.Add(new BackupFileEntry
            {
                Path = NormalizeRelativePath(relativePath),
                Sha256 = Sha256(destination),
                Size = info.Length
            });
        }

        BackupManifest manifest = new()
        {
            Product = ProductName,
            UpdateId = updateId,
            PreviousVersion = previousVersion,
            CreatedAt = DateTimeOffset.UtcNow,
            Files = files.OrderBy(file => file.Path, StringComparer.OrdinalIgnoreCase).ToList()
        };
        File.WriteAllText(Path.Combine(backupPath, BackupManifestFileName), JsonSerializer.Serialize(manifest, JsonOptions));
        WriteLog("backup.created", "Managed install backup created.", new { update_id = updateId, backupPath, previousVersion, file_count = manifest.Files.Count });
    }

    private static BackupManifest ValidateBackup(string backupPath, string updateId, string previousVersion)
    {
        string manifestPath = Path.Combine(backupPath, BackupManifestFileName);
        if (!File.Exists(manifestPath))
        {
            throw new InvalidOperationException("Manifesto do backup ausente.");
        }

        BackupManifest manifest = JsonSerializer.Deserialize<BackupManifest>(File.ReadAllText(manifestPath), JsonOptions)
            ?? throw new InvalidOperationException("Manifesto do backup invalido.");
        if (!manifest.UpdateId.Equals(updateId, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Manifesto do backup pertence a outro update_id.");
        }
        if (!string.IsNullOrWhiteSpace(previousVersion) && !manifest.PreviousVersion.Equals(previousVersion, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Manifesto do backup possui versao anterior inesperada.");
        }
        foreach (string required in new[] { "NightOwl.Agent.Windows.exe", "NightOwl.Agent.Updater.exe", "NightOwl.Agent.Tray.exe", "agent.version.json" })
        {
            if (!manifest.Files.Any(file => file.Path.Equals(required, StringComparison.OrdinalIgnoreCase)))
            {
                throw new InvalidOperationException($"Backup sem arquivo obrigatorio: {required}");
            }
        }
        foreach (BackupFileEntry entry in manifest.Files)
        {
            if (string.IsNullOrWhiteSpace(entry.Path) || IsProtectedRelativePath(entry.Path))
            {
                throw new InvalidOperationException($"Backup contem caminho proibido: {entry.Path}");
            }
            string filePath = Path.Combine(backupPath, entry.Path.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(filePath))
            {
                throw new FileNotFoundException("Arquivo do backup ausente.", filePath);
            }
            FileInfo info = new(filePath);
            if (entry.Size >= 0 && info.Length != entry.Size)
            {
                throw new InvalidOperationException($"Tamanho invalido no backup: {entry.Path}");
            }
            string actual = Sha256(filePath);
            if (!actual.Equals(entry.Sha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"SHA256 invalido no backup: {entry.Path}");
            }
        }
        WriteLog("backup.validated", "Backup manifest validated.", new { update_id = updateId, backupPath, previousVersion, file_count = manifest.Files.Count });
        return manifest;
    }

    private static void ExtractZipSafe(string zipPath, string destination)
    {
        string destinationFull = Path.GetFullPath(destination);
        using ZipArchive archive = ZipFile.OpenRead(zipPath);
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            string target = Path.GetFullPath(Path.Combine(destinationFull, entry.FullName.Replace('\\', Path.DirectorySeparatorChar)));
            if (!target.StartsWith(destinationFull, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Pacote contem entrada invalida fora do staging.");
            }
            if (string.IsNullOrEmpty(entry.Name))
            {
                Directory.CreateDirectory(target);
                continue;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            entry.ExtractToFile(target, overwrite: true);
        }
    }

    private static int LaunchStagedUpdater(string stagedUpdater, string stagingPath, string manifestPath, string packageSha256, bool interactive, JobContext jobContext)
    {
        string args = $"update --apply-staged \"{stagingPath}\" --manifest \"{manifestPath}\" --package-sha256 \"{packageSha256}\"";
        if (interactive)
        {
            args += " --interactive";
        }
        if (jobContext.IsJob)
        {
            args += $" --source job --job-id \"{jobContext.JobId}\" --channel \"{jobContext.Channel}\" --target-version \"{jobContext.TargetVersion}\" --quiet --json-output";
        }

        ProcessStartInfo psi = new()
        {
            FileName = stagedUpdater,
            Arguments = args,
            WorkingDirectory = Path.GetDirectoryName(stagedUpdater)!,
            UseShellExecute = false
        };
        using Process process = Process.Start(psi) ?? throw new InvalidOperationException("Nao foi possivel iniciar updater staged.");
        process.WaitForExit();
        return process.ExitCode;
    }

    private static readonly string[] ProtectedInstallFileNames = { "agent.config.json", "agent-dotnet.state.json", "agent.state.json", "update-state.json" };
    private static readonly string[] ProtectedInstallDirectoryNames = { "Config", "Identity", "State", "Logs", "Diagnostics", "Updates", "Packages", "Cache" };
    private const string BackupManifestFileName = "backup-manifest.json";

    internal static void CopyStagedFilesWithRetryForTest(string stagedPath, string installPath, TimeSpan timeout)
    {
        CopyStagedFilesWithRetry(stagedPath, installPath, timeout);
    }

    internal static void WaitForInstallFilesReadyForTest(string stagedPath, string installPath, TimeSpan timeout)
    {
        WaitForInstallFilesReady(stagedPath, installPath, timeout);
    }

    internal static bool IsNightOwlRelatedProcessForTest(Process process, IEnumerable<string> allowedRoots, int currentProcessId)
    {
        return IsNightOwlRelatedProcess(process, NormalizeRoots(allowedRoots), currentProcessId, out _);
    }

    internal static void WaitForNightOwlProcessesToExitForTest(IEnumerable<string> allowedRoots, TimeSpan timeout)
    {
        WaitForNightOwlProcessesToExit(allowedRoots, timeout);
    }

    private static void CopyStagedFiles(string stagedPath, string installPath)
    {
        Directory.CreateDirectory(installPath);
        CopyDirectory(stagedPath, installPath, overwrite: true, excludeNames: ProtectedInstallFileNames);
        WriteLog("updater.files.copied", "Arquivos atualizados copiados para instalacao.", new { stagedPath, installPath });
    }

    private static void CopyStagedFilesWithRetry(string stagedPath, string installPath, TimeSpan timeout)
    {
        DateTimeOffset deadline = DateTimeOffset.UtcNow.Add(timeout);
        int attempts = 0;
        Exception? lastException = null;
        while (DateTimeOffset.UtcNow <= deadline)
        {
            attempts++;
            try
            {
                WaitForInstallFilesReady(stagedPath, installPath, Remaining(deadline));
                CopyStagedFiles(stagedPath, installPath);
                return;
            }
            catch (UnauthorizedAccessException ex)
            {
                lastException = ex;
                string lockedPath = ExtractPathFromException(ex);
                string message = $"Access denied while replacing NightOwl file '{lockedPath}'. {SanitizeMessage(ex.Message)}";
                WriteLog("update.file_lock.retry", "Access denied while replacing files; retrying.", new { error_code = UpdateErrorCodes.UpdateFileAccessDenied, file = lockedPath, attempts, error = SanitizeMessage(ex.Message) });
                if (Remaining(deadline) <= TimeSpan.Zero)
                {
                    WriteLog("update.file_lock.timeout", "Access denied persisted until timeout.", new { error_code = UpdateErrorCodes.UpdateFileAccessDenied, file = lockedPath, attempts });
                    throw new FileReplaceException(UpdateErrorCodes.UpdateFileAccessDenied, message, lockedPath, attempts, timeout, ex);
                }
            }
            catch (IOException ex)
            {
                lastException = ex;
                string lockedPath = ExtractPathFromException(ex);
                string message = $"Timed out waiting for NightOwl file '{lockedPath}' to be released. {SanitizeMessage(ex.Message)}";
                WriteLog("update.file_lock.retry", "File appears locked while replacing files; retrying.", new { error_code = UpdateErrorCodes.UpdateFileLockTimeout, file = lockedPath, attempts, error = SanitizeMessage(ex.Message) });
                if (Remaining(deadline) <= TimeSpan.Zero)
                {
                    WriteLog("update.file_lock.timeout", "File lock persisted until timeout.", new { error_code = UpdateErrorCodes.UpdateFileLockTimeout, file = lockedPath, attempts });
                    throw new FileReplaceException(UpdateErrorCodes.UpdateFileLockTimeout, message, lockedPath, attempts, timeout, ex);
                }
            }

            Thread.Sleep(DelayForAttempt(attempts, Remaining(deadline)));
        }

        string fallbackPath = ExtractPathFromException(lastException);
        throw new FileReplaceException(
            UpdateErrorCodes.UpdateFileReplaceFailed,
            $"Timed out replacing NightOwl files after {attempts} attempts. Last file: '{fallbackPath}'. {SanitizeMessage(lastException?.Message ?? "")}",
            fallbackPath,
            attempts,
            timeout,
            lastException);
    }

    private static void WaitForInstallFilesReady(string stagedPath, string installPath, TimeSpan timeout)
    {
        DateTimeOffset deadline = DateTimeOffset.UtcNow.Add(timeout);
        int attempts = 0;
        while (DateTimeOffset.UtcNow <= deadline)
        {
            attempts++;
            try
            {
                foreach (string stagedFile in EnumerateManagedFiles(stagedPath))
                {
                    string relative = Path.GetRelativePath(stagedPath, stagedFile);
                    string target = Path.Combine(installPath, relative);
                    if (!File.Exists(target))
                    {
                        continue;
                    }
                    EnsureFileReady(target);
                }
                WriteLog("update.files.ready", "Install files passed lock preflight.", new { stagedPath, installPath, attempts });
                return;
            }
            catch (UnauthorizedAccessException ex)
            {
                string path = ExtractPathFromException(ex);
                WriteLog("update.file_lock.detected", "Access denied during file readiness preflight.", new { error_code = UpdateErrorCodes.UpdateFileAccessDenied, file = path, attempts, error = SanitizeMessage(ex.Message) });
                if (Remaining(deadline) <= TimeSpan.Zero)
                {
                    WriteLog("update.file_lock.timeout", "Access denied during preflight persisted until timeout.", new { error_code = UpdateErrorCodes.UpdateFileAccessDenied, file = path, attempts });
                    throw new FileReplaceException(UpdateErrorCodes.UpdateFileAccessDenied, $"Access denied before replacing NightOwl file '{path}'. {SanitizeMessage(ex.Message)}", path, attempts, timeout, ex);
                }
            }
            catch (IOException ex)
            {
                string path = ExtractPathFromException(ex);
                WriteLog("update.file_lock.detected", "File lock detected during readiness preflight.", new { error_code = UpdateErrorCodes.UpdateFileLockTimeout, file = path, attempts, error = SanitizeMessage(ex.Message) });
                if (Remaining(deadline) <= TimeSpan.Zero)
                {
                    WriteLog("update.file_lock.timeout", "File lock during preflight persisted until timeout.", new { error_code = UpdateErrorCodes.UpdateFileLockTimeout, file = path, attempts });
                    throw new FileReplaceException(UpdateErrorCodes.UpdateFileLockTimeout, $"Timed out waiting for NightOwl file '{path}' before replacement. {SanitizeMessage(ex.Message)}", path, attempts, timeout, ex);
                }
            }

            Thread.Sleep(DelayForAttempt(attempts, Remaining(deadline)));
        }
    }

    private static void EnsureFileReady(string path)
    {
        try
        {
            using FileStream stream = new(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            stream.Flush(flushToDisk: false);
        }
        catch (IOException ex)
        {
            throw new IOException($"File is locked: {path}. {ex.Message}", ex);
        }
        catch (UnauthorizedAccessException ex)
        {
            throw new UnauthorizedAccessException($"Access denied: {path}. {ex.Message}", ex);
        }
    }

    private static void WaitForNightOwlProcessesToExit(IEnumerable<string> allowedRoots, TimeSpan timeout)
    {
        DateTimeOffset deadline = DateTimeOffset.UtcNow.Add(timeout);
        string[] roots = NormalizeRoots(allowedRoots);
        int currentProcessId = Environment.ProcessId;
        int attempts = 0;

        while (DateTimeOffset.UtcNow <= deadline)
        {
            attempts++;
            List<object> active = new();
            foreach (Process process in Process.GetProcesses())
            {
                try
                {
                    if (!IsNightOwlRelatedProcess(process, roots, currentProcessId, out string executablePath))
                    {
                        continue;
                    }
                    active.Add(new { process_id = process.Id, process_name = process.ProcessName, executable_path = executablePath });
                }
                catch
                {
                    // Process may exit while being inspected.
                }
                finally
                {
                    process.Dispose();
                }
            }

            if (active.Count == 0)
            {
                WriteLog("update.quiesce.processes_stopped", "NightOwl processes have exited.", new { attempts });
                return;
            }

            WriteLog("update.quiesce.waiting_processes", "Waiting for NightOwl processes to exit.", new { attempts, active_processes = active });
            Thread.Sleep(DelayForAttempt(attempts, Remaining(deadline)));
        }

        throw new System.TimeoutException($"Timed out waiting for NightOwl processes to exit after {timeout.TotalSeconds:0}s.");
    }

    private static bool IsNightOwlRelatedProcess(Process process, IReadOnlyCollection<string> allowedRoots, int currentProcessId, out string executablePath)
    {
        executablePath = "";
        if (process.Id == currentProcessId)
        {
            return false;
        }
        if (!process.ProcessName.StartsWith("NightOwl.Agent", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        try
        {
            executablePath = process.MainModule?.FileName ?? "";
        }
        catch
        {
            return false;
        }

        if (string.IsNullOrWhiteSpace(executablePath))
        {
            return false;
        }

        string normalized = NormalizeFullPath(executablePath);
        return allowedRoots.Any(root => normalized.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            || normalized.Equals(root, StringComparison.OrdinalIgnoreCase));
    }

    private static string[] NormalizeRoots(IEnumerable<string> roots)
    {
        return roots
            .Where(root => !string.IsNullOrWhiteSpace(root))
            .Select(NormalizeFullPath)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static string NormalizeFullPath(string path)
    {
        return Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static TimeSpan Remaining(DateTimeOffset deadline)
    {
        TimeSpan remaining = deadline - DateTimeOffset.UtcNow;
        return remaining > TimeSpan.Zero ? remaining : TimeSpan.Zero;
    }

    private static TimeSpan DelayForAttempt(int attempts, TimeSpan remaining)
    {
        int milliseconds = attempts switch
        {
            <= 1 => 250,
            2 => 500,
            _ => 1000
        };
        TimeSpan delay = TimeSpan.FromMilliseconds(milliseconds);
        return remaining > TimeSpan.Zero && remaining < delay ? remaining : delay;
    }

    private static string ExtractPathFromException(Exception? exception)
    {
        if (exception is null)
        {
            return "";
        }
        string message = exception.Message;
        int firstQuote = message.IndexOf('\'');
        int secondQuote = firstQuote >= 0 ? message.IndexOf('\'', firstQuote + 1) : -1;
        if (firstQuote >= 0 && secondQuote > firstQuote)
        {
            return message.Substring(firstQuote + 1, secondQuote - firstQuote - 1);
        }
        return "";
    }

    private static IEnumerable<string> EnumerateManagedFiles(string root)
    {
        if (!Directory.Exists(root))
        {
            yield break;
        }
        foreach (string file in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
        {
            string relative = Path.GetRelativePath(root, file);
            if (IsProtectedRelativePath(relative))
            {
                continue;
            }
            yield return file;
        }
    }

    private static bool IsProtectedRelativePath(string relativePath)
    {
        string normalized = NormalizeRelativePath(relativePath);
        string fileName = Path.GetFileName(normalized);
        if (ProtectedInstallFileNames.Contains(fileName, StringComparer.OrdinalIgnoreCase))
        {
            return true;
        }
        if (fileName.Contains("token", StringComparison.OrdinalIgnoreCase)
            || fileName.Contains("machine_id", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        string[] segments = normalized.Split('/', StringSplitOptions.RemoveEmptyEntries);
        return segments.Any(segment => ProtectedInstallDirectoryNames.Contains(segment, StringComparer.OrdinalIgnoreCase));
    }

    private static string NormalizeRelativePath(string path)
    {
        return path.Replace('\\', '/').TrimStart('/');
    }

    private static void CopyDirectory(string source, string destination, bool overwrite, IReadOnlyCollection<string> excludeNames)
    {
        Directory.CreateDirectory(destination);
        foreach (string dir in Directory.GetDirectories(source, "*", SearchOption.AllDirectories))
        {
            string relative = Path.GetRelativePath(source, dir);
            if (IsProtectedRelativePath(relative))
            {
                continue;
            }
            Directory.CreateDirectory(Path.Combine(destination, relative));
        }
        foreach (string file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
        {
            string relative = Path.GetRelativePath(source, file);
            string name = Path.GetFileName(file);
            if (excludeNames.Contains(name, StringComparer.OrdinalIgnoreCase) || IsProtectedRelativePath(relative))
            {
                continue;
            }
            string target = Path.Combine(destination, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite);
        }
    }

    private static void StopTray()
    {
        string[] allowedRoots = NormalizeRoots(new[] { Paths.InstallDir, RunnerRoot });
        int currentProcessId = Environment.ProcessId;
        foreach (Process process in Process.GetProcessesByName(TrayProcessName))
        {
            try
            {
                if (!IsNightOwlRelatedProcess(process, allowedRoots, currentProcessId, out string executablePath))
                {
                    WriteLog("updater.tray.stop_skipped", "Tray-like process ignored because it is outside NightOwl roots.", new { process_id = process.Id, process_name = process.ProcessName });
                    continue;
                }
                process.CloseMainWindow();
                if (!process.WaitForExit(3000))
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(5000);
                }
                WriteLog("updater.tray.stopped", "Tray process stopped.", new { process_id = process.Id, executable_path = executablePath });
            }
            catch (Exception ex)
            {
                WriteLog("updater.tray.stop_failed", "Falha ao encerrar tray.", new { error = ex.Message });
            }
            finally
            {
                process.Dispose();
            }
        }
    }

    private static void StopOtherUpdaterProcesses()
    {
        int currentId = Environment.ProcessId;
        foreach (Process process in Process.GetProcessesByName("NightOwl.Agent.Updater"))
        {
            try
            {
                if (process.Id == currentId)
                {
                    continue;
                }
                process.Kill(entireProcessTree: true);
                process.WaitForExit(5000);
            }
            catch (Exception ex)
            {
                WriteLog("updater.peer_stop_failed", "Falha ao encerrar outra instancia do updater.", new { process_id = process.Id, error = ex.Message });
            }
            finally
            {
                process.Dispose();
            }
        }
    }

    private static void StopService()
    {
        using ServiceController service = new(ServiceName);
        if (service.Status == ServiceControllerStatus.Stopped)
        {
            return;
        }
        service.Stop();
        service.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(45));
        WriteLog("updater.service.stopped", "Servico parado para atualizacao.");
    }

    private static void StartService()
    {
        using ServiceController service = new(ServiceName);
        if (service.Status != ServiceControllerStatus.Running)
        {
            service.Start();
            service.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(45));
        }
        WriteLog("updater.service.started", "Servico iniciado apos atualizacao.");
    }

    private static void StartTrayIfPossible(string installPath)
    {
        string tray = Path.Combine(installPath, "NightOwl.Agent.Tray.exe");
        if (!File.Exists(tray) || !Environment.UserInteractive)
        {
            return;
        }
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = tray,
                WorkingDirectory = installPath,
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            WriteLog("updater.tray.start_failed", "Falha ao reiniciar tray.", new { error = ex.Message });
        }
    }

    private static void ValidatePostUpdate(string installPath, string expectedVersion)
    {
        if (!File.Exists(Paths.ConfigPath))
        {
            throw new InvalidOperationException("agent.config.json ausente apos update.");
        }
        if (!File.Exists(Path.Combine(installPath, "NightOwl.Agent.Windows.exe")))
        {
            throw new InvalidOperationException("NightOwl.Agent.Windows.exe ausente apos update.");
        }
        string serviceStatus = GetServiceStatus();
        if (!serviceStatus.Equals("Running", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Servico nao voltou como Running.");
        }
        AgentVersionInfo version = LoadInstalledVersion(new AgentConfig { InstallPath = installPath });
        if (CompareVersions(version.Version, expectedVersion) != 0)
        {
            throw new InvalidOperationException($"Versao aplicada inesperada: {version.Version}, esperado {expectedVersion}.");
        }
    }

    private static HealthCheckWaitResult WaitForHealthCheck(UpdateState state, bool expectRollback, TimeSpan timeout)
    {
        DateTimeOffset deadline = DateTimeOffset.UtcNow.Add(timeout);
        while (DateTimeOffset.UtcNow < deadline)
        {
            UpdateState current = ReloadUpdateState(state);
            if (!expectRollback && current.CurrentStage.Equals(UpdateStages.Completed, StringComparison.OrdinalIgnoreCase))
            {
                return HealthCheckWaitResult.Completed;
            }
            if (expectRollback && current.CurrentStage.Equals(UpdateStages.RolledBack, StringComparison.OrdinalIgnoreCase))
            {
                return HealthCheckWaitResult.RolledBack;
            }
            if (current.CurrentStage.Equals(UpdateStages.RollbackFailed, StringComparison.OrdinalIgnoreCase)
                || current.CurrentStage.Equals(UpdateStages.Failed, StringComparison.OrdinalIgnoreCase)
                || current.RollbackRequired && !expectRollback)
            {
                return HealthCheckWaitResult.FailedState;
            }
            string serviceStatus = GetServiceStatus();
            if (serviceStatus.Equals("Stopped", StringComparison.OrdinalIgnoreCase)
                || serviceStatus.Equals("StopPending", StringComparison.OrdinalIgnoreCase)
                || serviceStatus.Equals("NotInstalled", StringComparison.OrdinalIgnoreCase))
            {
                return HealthCheckWaitResult.ServiceExitedEarly;
            }
            Thread.Sleep(TimeSpan.FromSeconds(3));
        }

        return HealthCheckWaitResult.Timeout;
    }

    private static int ExecuteAutomaticRollback(UpdateState state, string installPath, bool interactive)
    {
        state = ReloadUpdateState(state);
        if (state.RollbackAttempt >= 1)
        {
            state.MarkRollbackFailed(UpdateErrorCodes.RollbackFailed, "Automatic rollback already attempted for this update_id.");
            UpdateStateStore.Save(state);
            WriteCriticalRollbackResult(state, installPath, "Rollback already attempted.");
            return 1;
        }

        state.RollbackAttempt++;
        MarkStage(state, UpdateStages.RollbackStarting);
        WriteLog("rollback.start", "Automatic rollback started.", new { update_id = state.UpdateId, job_id = state.JobId, from_version = state.FromVersion, target_version = state.TargetVersion, reason = state.RollbackReason, original_error_code = state.ErrorCode });

        try
        {
            MarkStage(state, UpdateStages.RollbackStoppingService);
            try
            {
                StopService();
            }
            catch (Exception ex)
            {
                state.MarkRollbackFailed(UpdateErrorCodes.RollbackServiceStopFailed, SanitizeMessage(ex.Message));
                UpdateStateStore.Save(state);
                throw;
            }

            MarkStage(state, UpdateStages.RollbackRestoringFiles);
            BackupManifest manifest;
            try
            {
                manifest = ValidateBackup(state.BackupPath, state.UpdateId, state.FromVersion);
            }
            catch (Exception ex)
            {
                state.MarkRollbackFailed(UpdateErrorCodes.RollbackBackupInvalid, SanitizeMessage(ex.Message));
                UpdateStateStore.Save(state);
                throw;
            }

            try
            {
                state.RestoredFileCount = RestoreManagedFiles(installPath, state.BackupPath, manifest);
                UpdateStateStore.Save(state);
            }
            catch (Exception ex)
            {
                state.MarkRollbackFailed(UpdateErrorCodes.RollbackFileRestoreFailed, SanitizeMessage(ex.Message));
                UpdateStateStore.Save(state);
                throw;
            }

            MarkStage(state, UpdateStages.RollbackStartingService);
            try
            {
                StartService();
            }
            catch (Exception ex)
            {
                state.MarkRollbackFailed(UpdateErrorCodes.RollbackServiceStartFailed, SanitizeMessage(ex.Message));
                UpdateStateStore.Save(state);
                throw;
            }

            MarkStage(state, UpdateStages.RollbackWaitingHealthCheck);
            int healthTimeoutSeconds = GetOptionInt(Environment.GetCommandLineArgs(), "--health-timeout-seconds", DefaultHealthCheckTimeoutSeconds);
            HealthCheckWaitResult result = WaitForHealthCheck(state, expectRollback: true, TimeSpan.FromSeconds(healthTimeoutSeconds));
            if (result == HealthCheckWaitResult.RolledBack)
            {
                WriteLog("rollback.completed", "Rollback confirmed by agent health check.", new { update_id = state.UpdateId, job_id = state.JobId, restored_file_count = ReloadUpdateState(state).RestoredFileCount });
                WriteJson(new { ok = false, rolled_back = true, update_id = state.UpdateId, active_version = state.FromVersion, attempted_version = state.TargetVersion });
                if (interactive)
                {
                    ShowMessage($"Atualizacao falhou e rollback para {state.FromVersion} foi confirmado.", MessageBoxIcon.Warning);
                }
                return 0;
            }

            string errorCode = result == HealthCheckWaitResult.Timeout
                ? UpdateErrorCodes.RollbackHealthcheckTimeout
                : result == HealthCheckWaitResult.FailedState && !string.IsNullOrWhiteSpace(ReloadUpdateState(state).RollbackErrorCode)
                    ? ReloadUpdateState(state).RollbackErrorCode
                    : UpdateErrorCodes.RollbackFailed;
            state = ReloadUpdateState(state);
            state.MarkRollbackFailed(errorCode, $"Rollback health check did not confirm previous version. Result: {result}.");
            UpdateStateStore.Save(state);
            WriteCriticalRollbackResult(state, installPath, state.RollbackErrorMessage);
            return 1;
        }
        catch (Exception ex)
        {
            state = ReloadUpdateState(state);
            if (!state.CurrentStage.Equals(UpdateStages.RollbackFailed, StringComparison.OrdinalIgnoreCase))
            {
                state.MarkRollbackFailed(string.IsNullOrWhiteSpace(state.RollbackErrorCode) ? UpdateErrorCodes.RollbackFailed : state.RollbackErrorCode, SanitizeMessage(ex.Message));
                UpdateStateStore.Save(state);
            }
            WriteLog("rollback.failed", "Automatic rollback failed.", new { update_id = state.UpdateId, job_id = state.JobId, rollback_error_code = state.RollbackErrorCode, error = ex.Message });
            WriteCriticalRollbackResult(state, installPath, SanitizeMessage(ex.Message));
            WriteJson(new { ok = false, rollback_failed = true, update_id = state.UpdateId, error_code = state.RollbackErrorCode, error = ex.Message });
            return 1;
        }
    }

    private static int RestoreManagedFiles(string installPath, string backupPath, BackupManifest manifest)
    {
        foreach (string currentFile in EnumerateManagedFiles(installPath).ToList())
        {
            File.Delete(currentFile);
        }

        int count = 0;
        foreach (BackupFileEntry entry in manifest.Files)
        {
            string source = Path.Combine(backupPath, entry.Path.Replace('/', Path.DirectorySeparatorChar));
            string destination = Path.Combine(installPath, entry.Path.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(source, destination, overwrite: true);
            count++;
        }

        ValidateBackup(backupPath, manifest.UpdateId, manifest.PreviousVersion);
        WriteLog("rollback.files_restored", "Managed files restored from backup.", new { update_id = manifest.UpdateId, backupPath, restored_file_count = count });
        return count;
    }

    private static void WriteCriticalRollbackResult(UpdateState state, string installPath, string message)
    {
        if (string.IsNullOrWhiteSpace(state.JobId))
        {
            return;
        }

        string originalErrorMessage = SanitizeMessage(state.ErrorMessage);
        string rollbackErrorMessage = SanitizeMessage(state.RollbackErrorMessage);
        string finalMessage = string.IsNullOrWhiteSpace(rollbackErrorMessage) ? SanitizeMessage(message) : rollbackErrorMessage;
        Directory.CreateDirectory(PendingJobsRoot);
        var payload = new
        {
            job_id = state.JobId,
            status = "failed",
            started_at = state.StartedAt,
            finished_at = DateTimeOffset.UtcNow,
            duration_seconds = Math.Round((DateTimeOffset.UtcNow - state.StartedAt).TotalSeconds, 3),
            exit_code = 24,
            stdout = "",
            stderr = finalMessage,
            error_message = string.IsNullOrWhiteSpace(originalErrorMessage) ? finalMessage : originalErrorMessage,
            result = new
            {
                type = "update_agent",
                update_id = state.UpdateId,
                update_status = "rollback_failed",
                from_version = state.FromVersion,
                attempted_version = state.TargetVersion,
                active_version = LoadInstalledVersion(new AgentConfig { InstallPath = installPath }).Version,
                failure_stage = state.RollbackReason,
                original_error_code = state.ErrorCode,
                original_error_message = originalErrorMessage,
                error_code = state.ErrorCode,
                error_message = string.IsNullOrWhiteSpace(originalErrorMessage) ? finalMessage : originalErrorMessage,
                rollback_error_code = state.RollbackErrorCode,
                rollback_error_message = rollbackErrorMessage,
                rollback_confirmed = false,
                message = finalMessage
            }
        };
        File.WriteAllText(PendingUpdateResultPath, JsonSerializer.Serialize(payload, JsonOptions));
        WriteLog("rollback.pending_result.written", "Critical rollback result written.", new { update_id = state.UpdateId, job_id = state.JobId, rollback_error_code = state.RollbackErrorCode });
    }

    private static void CleanupStaging(string stagedPath)
    {
        try
        {
            if (Directory.Exists(stagedPath))
            {
                Directory.Delete(stagedPath, recursive: true);
                WriteLog("staging.cleaned", "Staging directory removed after completed update.", new { stagedPath });
            }
        }
        catch (Exception ex)
        {
            WriteLog("staging.cleanup_failed", "Failed to remove staging directory.", new { stagedPath, error = ex.Message });
        }
    }

    private static void TryRollbackFromBackup(string backupPath, string installPath)
    {
        try
        {
            if (!Directory.Exists(backupPath))
            {
                WriteLog("updater.rollback.skipped", "Backup ausente; rollback ignorado.", new { backupPath });
                return;
            }
            StopServiceSafe();
            RestoreBackup(backupPath, installPath);
            StartService();
            StartTrayIfPossible(installPath);
            WriteLog("updater.rollback.completed", "Rollback automatico concluido.", new { backupPath });
        }
        catch (Exception rollbackEx)
        {
            WriteLog("updater.rollback.failed", "Rollback automatico falhou.", new { backupPath, error = rollbackEx.Message });
        }
    }

    private static void RestoreBackup(string backupPath, string installPath)
    {
        Directory.CreateDirectory(installPath);
        CopyDirectory(backupPath, installPath, overwrite: true, excludeNames: Array.Empty<string>());
    }

    private static void StopServiceSafe()
    {
        try
        {
            StopService();
        }
        catch (Exception ex)
        {
            WriteLog("updater.service.stop_failed", "Falha ao parar servico.", new { error = ex.Message });
        }
    }

    private static void WriteLocalVersion(string installPath, UpdateManifest manifest, string packageSha256, string updatedBy)
    {
        AgentVersionInfo version = new()
        {
            Version = manifest.Version,
            InstalledAt = DateTimeOffset.UtcNow.ToString("O"),
            Channel = string.IsNullOrWhiteSpace(manifest.Channel) ? "stable" : manifest.Channel,
            PackageSha256 = packageSha256,
            UpdatedBy = updatedBy
        };
        File.WriteAllText(Path.Combine(installPath, "agent.version.json"), JsonSerializer.Serialize(version, JsonOptions));
    }

    private static void WritePendingUpdateResult(JobContext jobContext, UpdateState state, string status, int exitCode, string installedVersion, string previousVersion, string message, object result, string stderr)
    {
        if (!jobContext.IsJob)
        {
            return;
        }
        string originalErrorMessage = SanitizeMessage(state.ErrorMessage);
        string rollbackErrorMessage = SanitizeMessage(state.RollbackErrorMessage);
        string effectiveErrorMessage = status == "failed"
            ? (string.IsNullOrWhiteSpace(originalErrorMessage) ? SanitizeMessage(message) : originalErrorMessage)
            : status.Equals("rolled_back", StringComparison.OrdinalIgnoreCase)
                ? originalErrorMessage
                : "";
        Directory.CreateDirectory(PendingJobsRoot);
        var payload = new
        {
            job_id = jobContext.JobId,
            status,
            started_at = DateTimeOffset.UtcNow,
            finished_at = DateTimeOffset.UtcNow,
            duration_seconds = 0,
            exit_code = exitCode,
            stdout = message,
            stderr = Trim(stderr, 8000),
            error_message = effectiveErrorMessage,
            result = new
            {
                type = "update_agent",
                update_id = state.UpdateId,
                update_stage = state.CurrentStage,
                error_code = state.ErrorCode,
                error_message = effectiveErrorMessage,
                original_error_code = state.ErrorCode,
                original_error_message = originalErrorMessage,
                rollback_error_code = state.RollbackErrorCode,
                rollback_error_message = rollbackErrorMessage,
                failure_stage = state.RollbackReason,
                update_status = exitCode == 10 ? "no_update_available" : status == "completed" ? "success" : "failed",
                installed_version = installedVersion,
                previous_version = previousVersion,
                target_version = string.IsNullOrWhiteSpace(state.TargetVersion) ? jobContext.TargetVersion : state.TargetVersion,
                updated = status == "completed" && exitCode == 0,
                rollback_performed = state.CurrentStage.Equals(UpdateStages.RolledBack, StringComparison.OrdinalIgnoreCase)
                    || status.Equals("rolled_back", StringComparison.OrdinalIgnoreCase)
                    || state.RollbackAttempt > 0,
                health_check = new
                {
                    confirmed = state.HealthCheckConfirmed,
                    service_started = state.ServiceStarted,
                    stage = state.CurrentStage
                },
                exit_code = exitCode,
                message,
                completed_at = DateTimeOffset.UtcNow,
                details = result
            }
        };
        PendingResultQueue queue = new(PendingJobsRoot);
        PendingResultRecord queued = queue.Enqueue("update_agent", payload, critical: true, resultId: $"update-{jobContext.JobId}");
        WriteLog("updater.pending_result.written", "Resultado de update gravado para envio pelo agente.", new { jobContext.JobId, status, exitCode, result_id = queued.ResultId });
        WriteLog("pending_result.written", "Pending update result written.", new { jobContext.JobId, status, exitCode, result_id = queued.ResultId });
    }

    private static string GetServiceStatus()
    {
        try
        {
            using ServiceController service = new(ServiceName);
            return service.Status.ToString();
        }
        catch
        {
            return "NotInstalled";
        }
    }

    private static void RelaunchElevated(string arguments)
    {
        ProcessStartInfo psi = new()
        {
            FileName = Environment.ProcessPath ?? Path.Combine(AppContext.BaseDirectory, "NightOwl.Agent.Updater.exe"),
            Arguments = arguments,
            Verb = "runas",
            UseShellExecute = true,
            WorkingDirectory = AppContext.BaseDirectory
        };
        Process.Start(psi);
    }

    private static bool IsAdministrator()
    {
        using WindowsIdentity identity = WindowsIdentity.GetCurrent();
        WindowsPrincipal principal = new(identity);
        return principal.IsInRole(WindowsBuiltInRole.Administrator);
    }

    private static void EnsurePackageUrlAllowed(AgentConfig config, string url)
    {
        if (!Uri.TryCreate(config.ServerBaseUrlOrDefault, UriKind.Absolute, out Uri? server)
            || !Uri.TryCreate(url, UriKind.Absolute, out Uri? target)
            || !target.Scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("packageUrl/checksumUrl invalida ou sem HTTPS.");
        }
        if (!server.Host.Equals(target.Host, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("packageUrl/checksumUrl fora do ServerUrl oficial nao e permitido.");
        }
    }

    private static string ResolvePackageUrl(AgentConfig config, UpdateManifest manifest)
    {
        return string.IsNullOrWhiteSpace(manifest.PackageUrl)
            ? JoinUrl(config.ServerBaseUrlOrDefault, "/downloads/nightowl-agent/NightOwl.Agent.Windows.zip")
            : manifest.PackageUrl;
    }

    private static string Sha256(string path)
    {
        using SHA256 sha = SHA256.Create();
        using FileStream stream = File.OpenRead(path);
        return Convert.ToHexString(sha.ComputeHash(stream)).ToLowerInvariant();
    }

    internal static VersionUpdateAction DecideVersionAction(string installedVersion, string targetVersion, bool force)
    {
        int comparison = CompareVersions(targetVersion, installedVersion);
        if (comparison == 0 && !force)
        {
            return VersionUpdateAction.AlreadyCurrent;
        }
        if (comparison < 0 && !force)
        {
            return VersionUpdateAction.DowngradeBlocked;
        }
        return VersionUpdateAction.UpdateAllowed;
    }

    internal static int CompareVersions(string left, string right)
    {
        ParsedSemVersion l = ParseSemVersion(left);
        ParsedSemVersion r = ParseSemVersion(right);
        int core = l.Core.CompareTo(r.Core);
        if (core != 0)
        {
            return core;
        }
        if (string.IsNullOrWhiteSpace(l.Prerelease) && string.IsNullOrWhiteSpace(r.Prerelease))
        {
            return 0;
        }
        if (string.IsNullOrWhiteSpace(l.Prerelease))
        {
            return 1;
        }
        if (string.IsNullOrWhiteSpace(r.Prerelease))
        {
            return -1;
        }
        return ComparePrerelease(l.Prerelease, r.Prerelease);
    }

    private static ParsedSemVersion ParseSemVersion(string value)
    {
        string text = (value ?? "0.0.0").Split('+')[0].Trim();
        string[] split = text.Split('-', 2);
        string clean = new(split[0].TakeWhile(c => char.IsDigit(c) || c == '.').ToArray());
        if (Version.TryParse(string.IsNullOrWhiteSpace(clean) ? "0.0.0" : clean, out Version? version))
        {
            return new ParsedSemVersion(version, split.Length > 1 ? split[1] : "");
        }
        return new ParsedSemVersion(new Version(0, 0, 0), split.Length > 1 ? split[1] : "");
    }

    private static int ComparePrerelease(string left, string right)
    {
        string[] leftParts = left.Split('.', StringSplitOptions.RemoveEmptyEntries);
        string[] rightParts = right.Split('.', StringSplitOptions.RemoveEmptyEntries);
        int length = Math.Max(leftParts.Length, rightParts.Length);
        for (int index = 0; index < length; index++)
        {
            if (index >= leftParts.Length) return -1;
            if (index >= rightParts.Length) return 1;
            bool leftNumber = int.TryParse(leftParts[index], out int leftInt);
            bool rightNumber = int.TryParse(rightParts[index], out int rightInt);
            int comparison = leftNumber && rightNumber
                ? leftInt.CompareTo(rightInt)
                : leftNumber
                    ? -1
                    : rightNumber
                        ? 1
                        : CompareIdentifierNatural(leftParts[index], rightParts[index]);
            if (comparison != 0)
            {
                return comparison;
            }
        }
        return 0;
    }

    private static int CompareIdentifierNatural(string left, string right)
    {
        int leftIndex = 0;
        int rightIndex = 0;
        while (leftIndex < left.Length && rightIndex < right.Length)
        {
            bool leftDigit = char.IsDigit(left[leftIndex]);
            bool rightDigit = char.IsDigit(right[rightIndex]);
            int leftStart = leftIndex;
            int rightStart = rightIndex;
            while (leftIndex < left.Length && char.IsDigit(left[leftIndex]) == leftDigit)
            {
                leftIndex++;
            }
            while (rightIndex < right.Length && char.IsDigit(right[rightIndex]) == rightDigit)
            {
                rightIndex++;
            }
            string leftPart = left[leftStart..leftIndex];
            string rightPart = right[rightStart..rightIndex];
            int comparison;
            if (leftDigit && rightDigit && int.TryParse(leftPart, out int leftNumber) && int.TryParse(rightPart, out int rightNumber))
            {
                comparison = leftNumber.CompareTo(rightNumber);
            }
            else if (leftDigit != rightDigit)
            {
                comparison = leftDigit ? -1 : 1;
            }
            else
            {
                comparison = string.Compare(leftPart, rightPart, StringComparison.OrdinalIgnoreCase);
            }
            if (comparison != 0)
            {
                return comparison;
            }
        }
        return (left.Length - leftIndex).CompareTo(right.Length - rightIndex);
    }

    private sealed record ParsedSemVersion(Version Core, string Prerelease);

    internal enum VersionUpdateAction
    {
        AlreadyCurrent,
        UpdateAllowed,
        DowngradeBlocked
    }

    private static string JoinUrl(string baseUrl, string path)
    {
        return $"{baseUrl.TrimEnd('/')}/{path.TrimStart('/')}";
    }

    private static string SanitizePathSegment(string value)
    {
        foreach (char c in Path.GetInvalidFileNameChars())
        {
            value = value.Replace(c, '-');
        }
        return string.IsNullOrWhiteSpace(value) ? "unknown" : value;
    }

    private static string SanitizeUrl(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out Uri? uri))
        {
            return url;
        }
        return uri.GetLeftPart(UriPartial.Path);
    }

    private static string SanitizeMessage(string value)
    {
        string sanitized = value ?? "";
        sanitized = sanitized.Replace("\r", " ").Replace("\n", " ");
        return Trim(sanitized, 1000);
    }

    private static string Trim(string value, int max)
    {
        if (string.IsNullOrEmpty(value) || value.Length <= max)
        {
            return value;
        }
        return value[..max];
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

    private static string SanitizeCommandLine(string arguments)
    {
        string sanitized = arguments;
        string? jobId = GetOption(SplitCommandLineLight(arguments), "--job-id");
        if (!string.IsNullOrWhiteSpace(jobId))
        {
            sanitized = sanitized.Replace(jobId, "<job-id>", StringComparison.OrdinalIgnoreCase);
        }
        return sanitized;
    }

    private static string[] SplitCommandLineLight(string arguments)
    {
        return arguments.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    }

    private static bool HasFlag(string[] args, string flag)
    {
        return args.Any(arg => arg.Equals(flag, StringComparison.OrdinalIgnoreCase));
    }

    private static string? GetOption(string[] args, string name)
    {
        for (int i = 0; i < args.Length; i++)
        {
            if (!args[i].Equals(name, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            if (i + 1 < args.Length)
            {
                return args[i + 1];
            }
        }
        return null;
    }

    private static int GetOptionInt(string[] args, string name, int fallback)
    {
        string? value = GetOption(args, name);
        return int.TryParse(value, out int parsed) && parsed > 0 ? parsed : fallback;
    }

    private static long GetOptionLong(string[] args, string name, long fallback)
    {
        string? value = GetOption(args, name);
        return long.TryParse(value, out long parsed) && parsed >= 0 ? parsed : fallback;
    }

    private static void WriteJson(object value)
    {
        Console.WriteLine(JsonSerializer.Serialize(value, JsonOptions));
    }

    private static void WriteLog(string eventType, string message, object? metadata = null)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
            var entry = new
            {
                timestamp = DateTimeOffset.UtcNow.ToString("O"),
                event_type = eventType,
                message,
                metadata = metadata ?? new { }
            };
            File.AppendAllText(LogPath, JsonSerializer.Serialize(entry) + Environment.NewLine);
        }
        catch
        {
            // Updater logging must not break installation or recovery.
        }
    }

    private static void ShowMessage(string message, MessageBoxIcon icon)
    {
        ApplicationConfiguration.Initialize();
        MessageBox.Show(message, "NightOwl Agent Updater", MessageBoxButtons.OK, icon);
    }

    private sealed class FileReplaceException : IOException
    {
        public string ErrorCode { get; }
        public string FilePath { get; }
        public int Attempts { get; }
        public TimeSpan Timeout { get; }

        public FileReplaceException(string errorCode, string message, string filePath, int attempts, TimeSpan timeout, Exception? innerException)
            : base($"{errorCode}: {message}", innerException)
        {
            ErrorCode = errorCode;
            FilePath = filePath;
            Attempts = attempts;
            Timeout = timeout;
        }
    }

    private sealed class AgentConfig
    {
        [JsonPropertyName("agentVersion")]
        public string AgentVersion { get; set; } = "0.1.0.7";

        [JsonPropertyName("serverBaseUrl")]
        public string ServerBaseUrl { get; set; } = "";

        [JsonPropertyName("installPath")]
        public string InstallPath { get; set; } = Paths.InstallDir;

        [JsonIgnore]
        public string ConfigPath { get; set; } = "";

        [JsonIgnore]
        public string ServerBaseUrlOrDefault => string.IsNullOrWhiteSpace(ServerBaseUrl) ? DefaultServerUrl : ServerBaseUrl.TrimEnd('/');

        [JsonIgnore]
        public string InstallPathOrDefault => string.IsNullOrWhiteSpace(InstallPath) ? Paths.InstallDir : InstallPath;
    }

    private sealed class UpdateManifest
    {
        [JsonPropertyName("product")]
        public string Product { get; set; } = ProductName;

        [JsonPropertyName("agent")]
        public string Agent { get; set; } = "";

        [JsonPropertyName("channel")]
        public string Channel { get; set; } = "stable";

        [JsonPropertyName("version")]
        public string Version { get; set; } = "0.0.0";

        [JsonPropertyName("publishedAt")]
        public string PublishedAt { get; set; } = "";

        [JsonPropertyName("published_at")]
        public string PublishedAtLegacy
        {
            get => PublishedAt;
            set => PublishedAt = value;
        }

        [JsonPropertyName("minimumSupportedVersion")]
        public string MinimumSupportedVersion { get; set; } = "0.0.0";

        [JsonPropertyName("minimum_updater_version")]
        public string MinimumUpdaterVersion { get; set; } = "";

        [JsonPropertyName("packageUrl")]
        public string PackageUrl { get; set; } = "";

        [JsonPropertyName("checksumUrl")]
        public string ChecksumUrl { get; set; } = "";

        [JsonPropertyName("manifestUrl")]
        public string ManifestUrl { get; set; } = "";

        [JsonPropertyName("signatureUrl")]
        public string SignatureUrl { get; set; } = "";

        [JsonPropertyName("manifest_sha256")]
        public string ManifestSha256 { get; set; } = "";

        [JsonPropertyName("signature_sha256")]
        public string SignatureSha256 { get; set; } = "";

        [JsonPropertyName("key_id")]
        public string KeyId { get; set; } = "";

        [JsonPropertyName("signature_key_id")]
        public string SignatureKeyId
        {
            get => KeyId;
            set => KeyId = value;
        }

        [JsonPropertyName("sha256")]
        public string Sha256 { get; set; } = "";

        [JsonPropertyName("size")]
        public long Size { get; set; }

        [JsonPropertyName("legacyUnsigned")]
        public bool LegacyUnsigned { get; set; }

        [JsonPropertyName("installerUrl")]
        public string InstallerUrl { get; set; } = "";

        [JsonPropertyName("notes")]
        public string Notes { get; set; } = "";

        [JsonPropertyName("requiresRestart")]
        public bool RequiresRestart { get; set; } = true;

        [JsonPropertyName("force")]
        public bool Force { get; set; }
    }

    private sealed class ReleaseManifest
    {
        [JsonPropertyName("schema_version")]
        public int SchemaVersion { get; set; }

        [JsonPropertyName("version")]
        public string Version { get; set; } = "";

        [JsonPropertyName("channel")]
        public string Channel { get; set; } = "";

        [JsonPropertyName("key_id")]
        public string KeyId { get; set; } = "";

        [JsonPropertyName("package")]
        public ReleaseManifestPackage Package { get; set; } = new();
    }

    private sealed class ReleaseManifestPackage
    {
        [JsonPropertyName("filename")]
        public string Filename { get; set; } = "";

        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("sha256")]
        public string Sha256 { get; set; } = "";

        [JsonPropertyName("size")]
        public long Size { get; set; }
    }

    private sealed class TrustedReleaseKeySet
    {
        [JsonPropertyName("keys")]
        public List<TrustedReleaseKey> Keys { get; set; } = new();
    }

    private sealed class TrustedReleaseKey
    {
        [JsonPropertyName("key_id")]
        public string KeyId { get; set; } = "";

        [JsonPropertyName("public_key_xml")]
        public string PublicKeyXml { get; set; } = "";

        [JsonPropertyName("status")]
        public string Status { get; set; } = "active";

        [JsonPropertyName("revoked_at")]
        public string RevokedAt { get; set; } = "";

        [JsonIgnore]
        public bool Revoked => !string.IsNullOrWhiteSpace(RevokedAt) || Status.Equals("revoked", StringComparison.OrdinalIgnoreCase);
    }

    private sealed class AgentVersionInfo
    {
        [JsonPropertyName("version")]
        public string Version { get; set; } = "0.0.0";

        [JsonPropertyName("installedAt")]
        public string InstalledAt { get; set; } = "";

        [JsonPropertyName("channel")]
        public string Channel { get; set; } = "stable";

        [JsonPropertyName("packageSha256")]
        public string PackageSha256 { get; set; } = "";

        [JsonPropertyName("updatedBy")]
        public string UpdatedBy { get; set; } = "";
    }

    private sealed class ChecksumsManifest
    {
        private readonly Dictionary<string, FileChecksum> _files = new(StringComparer.OrdinalIgnoreCase);

        public static ChecksumsManifest Parse(string json)
        {
            ChecksumsManifest manifest = new();
            using JsonDocument document = JsonDocument.Parse(json);
            JsonElement root = document.RootElement;

            if (root.TryGetProperty("files", out JsonElement files) && files.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement item in files.EnumerateArray())
                {
                    string name = GetString(item, "name", GetString(item, "file", ""));
                    string sha = GetString(item, "sha256", "");
                    long size = GetInt64(item, "size", GetInt64(item, "bytes", 0));
                    if (!string.IsNullOrWhiteSpace(name) && !string.IsNullOrWhiteSpace(sha))
                    {
                        manifest._files[name] = new FileChecksum(name, sha, size);
                    }
                }
            }

            foreach (JsonProperty property in root.EnumerateObject())
            {
                if (property.NameEquals("files"))
                {
                    continue;
                }
                if (property.Value.ValueKind == JsonValueKind.String)
                {
                    string value = property.Value.GetString() ?? "";
                    if (value.Length >= 64)
                    {
                        manifest._files[property.Name] = new FileChecksum(property.Name, value, 0);
                    }
                }
            }

            return manifest;
        }

        public static ChecksumsManifest FromPackage(string name, string sha256, long size)
        {
            ChecksumsManifest manifest = new();
            manifest._files[name] = new FileChecksum(name, sha256, size);
            return manifest;
        }

        public FileChecksum GetRequired(string name)
        {
            if (_files.TryGetValue(name, out FileChecksum? checksum))
            {
                return checksum;
            }
            throw new InvalidOperationException($"checksums.json sem entrada obrigatoria: {name}");
        }

        private static string GetString(JsonElement element, string property, string fallback)
        {
            return element.TryGetProperty(property, out JsonElement value) && value.ValueKind == JsonValueKind.String
                ? value.GetString() ?? fallback
                : fallback;
        }

        private static long GetInt64(JsonElement element, string property, long fallback)
        {
            return element.TryGetProperty(property, out JsonElement value) && value.TryGetInt64(out long parsed)
                ? parsed
                : fallback;
        }
    }

    private sealed record FileChecksum(string Name, string Sha256, long Size);

    private enum HealthCheckWaitResult
    {
        Completed,
        RolledBack,
        Timeout,
        FailedState,
        ServiceExitedEarly
    }

    private sealed class BackupManifest
    {
        [JsonPropertyName("product")]
        public string Product { get; set; } = "";

        [JsonPropertyName("update_id")]
        public string UpdateId { get; set; } = "";

        [JsonPropertyName("previous_version")]
        public string PreviousVersion { get; set; } = "";

        [JsonPropertyName("created_at")]
        public DateTimeOffset CreatedAt { get; set; }

        [JsonPropertyName("files")]
        public List<BackupFileEntry> Files { get; set; } = new();
    }

    private sealed class BackupFileEntry
    {
        [JsonPropertyName("path")]
        public string Path { get; set; } = "";

        [JsonPropertyName("sha256")]
        public string Sha256 { get; set; } = "";

        [JsonPropertyName("size")]
        public long Size { get; set; }
    }

    private sealed class JobContext
    {
        public bool IsJob { get; init; }
        public string JobId { get; init; } = "";
        public string Channel { get; init; } = "stable";
        public string TargetVersion { get; init; } = "latest";
        public string ReleaseId { get; init; } = "";
        public string PackageUrl { get; init; } = "";
        public string ChecksumUrl { get; init; } = "";
        public string Sha256 { get; init; } = "";
        public long Size { get; init; }
        public string ManifestUrl { get; init; } = "";
        public string ManifestSha256 { get; init; } = "";
        public string SignatureUrl { get; init; } = "";
        public string SignatureSha256 { get; init; } = "";
        public string SignatureKeyId { get; init; } = "";
        public string MinimumUpdaterVersion { get; init; } = "";
        public bool Mandatory { get; init; }
        public bool Force { get; init; }

        public bool HasExplicitTarget => IsJob
            && !string.IsNullOrWhiteSpace(TargetVersion)
            && !TargetVersion.Equals("latest", StringComparison.OrdinalIgnoreCase);

        public bool HasExplicitReleaseMetadata => HasExplicitTarget
            && !string.IsNullOrWhiteSpace(PackageUrl)
            && !string.IsNullOrWhiteSpace(Sha256);

        public static JobContext FromArgs(string[] args)
        {
            string source = GetOption(args, "--source") ?? "";
            string jobId = GetOption(args, "--job-id") ?? "";
            return new JobContext
            {
                IsJob = source.Equals("job", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(jobId),
                JobId = jobId,
                Channel = GetOption(args, "--channel") ?? "stable",
                TargetVersion = GetOption(args, "--target-version") ?? "latest",
                ReleaseId = GetOption(args, "--release-id") ?? "",
                PackageUrl = GetOption(args, "--package-url") ?? "",
                ChecksumUrl = GetOption(args, "--checksum-url") ?? "",
                Sha256 = GetOption(args, "--sha256") ?? "",
                Size = GetOptionLong(args, "--size", 0),
                ManifestUrl = GetOption(args, "--manifest-url") ?? "",
                ManifestSha256 = GetOption(args, "--manifest-sha256") ?? "",
                SignatureUrl = GetOption(args, "--signature-url") ?? "",
                SignatureSha256 = GetOption(args, "--signature-sha256") ?? "",
                SignatureKeyId = GetOption(args, "--signature-key-id") ?? "",
                MinimumUpdaterVersion = GetOption(args, "--minimum-updater-version") ?? "",
                Mandatory = HasFlag(args, "--mandatory"),
                Force = HasFlag(args, "--force"),
            };
        }
    }
}
