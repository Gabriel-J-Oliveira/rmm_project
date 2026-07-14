using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace NightOwl.Agent.Updater;

internal static class Program
{
    private const string ServiceName = "NightOwlAgentDotNet";
    private const string TrayProcessName = "NightOwl.Agent.Tray";
    private const string ProductName = "NightOwl Agent Windows";
    private const string DefaultInstallPath = @"C:\ProgramData\NightOwl\AgentDotNet";
    private const string DefaultServerUrl = "https://nightowl.controlsul.com.br";
    private const string LogPath = @"C:\ProgramData\NightOwl\Logs\agent-updater.jsonl";
    private static readonly string UpdatesRoot = @"C:\ProgramData\NightOwl\Updates";
    private static readonly string DownloadsRoot = Path.Combine(UpdatesRoot, "Downloads");
    private static readonly string StagingRoot = Path.Combine(UpdatesRoot, "Staging");
    private static readonly string RunnerRoot = Path.Combine(UpdatesRoot, "Runner");
    private static readonly string BackupsRoot = @"C:\ProgramData\NightOwl\Backups";
    private static readonly string JobsRoot = @"C:\ProgramData\NightOwl\Jobs";
    private static readonly string PendingJobsRoot = Path.Combine(JobsRoot, "Pending");
    private static readonly string PendingUpdateResultPath = Path.Combine(PendingJobsRoot, "pending-update-result.json");
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
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
            Directory.CreateDirectory(DownloadsRoot);
            Directory.CreateDirectory(StagingRoot);
            Directory.CreateDirectory(RunnerRoot);
            Directory.CreateDirectory(BackupsRoot);
            Directory.CreateDirectory(JobsRoot);
            Directory.CreateDirectory(PendingJobsRoot);

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
        WriteLog("update.start", "Update requested.", new { runner = HasFlag(args, "--runner"), source = GetOption(args, "--source") ?? "" });

        string? stagedPath = GetOption(args, "--apply-staged");
        if (!string.IsNullOrWhiteSpace(stagedPath))
        {
            string stagedManifestPath = GetOption(args, "--manifest") ?? throw new InvalidOperationException("Manifesto ausente para aplicacao staged.");
            string packageSha256 = GetOption(args, "--package-sha256") ?? "";
            return ApplyStagedUpdate(stagedPath, stagedManifestPath, packageSha256, interactive);
        }

        if (!HasFlag(args, "--runner"))
        {
            return LaunchIndependentRunner(args, interactive);
        }

        AgentConfig config = LoadConfig();
        UpdateManifest manifest = await DownloadManifestAsync(config);
        AgentVersionInfo installed = LoadInstalledVersion(config);
        JobContext jobContext = JobContext.FromArgs(args);
        if (CompareVersions(manifest.Version, installed.Version) <= 0 && !manifest.Force)
        {
            WriteLog("updater.update.skipped", "Nenhuma atualizacao disponivel.", new { installed = installed.Version, available = manifest.Version });
            var skipped = new { ok = true, updated = false, reason = "already_current", installedVersion = installed.Version, availableVersion = manifest.Version };
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, "completed", 10, installed.Version, installed.Version, "Agent already up to date.", skipped, "");
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
            RelaunchElevated(BuildRunnerArguments(args));
            WriteJson(new { ok = true, elevated = true, message = "Updater relancado como administrador." });
            return 0;
        }

        ChecksumsManifest checksums = await DownloadChecksumsAsync(config, manifest);
        string packageUrl = ResolvePackageUrl(config, manifest);
        EnsurePackageUrlAllowed(config, packageUrl);

        string downloadPath = Path.Combine(DownloadsRoot, "NightOwl.Agent.Windows.zip");
        WriteLog("package.download", "Downloading update package.", new { url = SanitizeUrl(packageUrl), downloadPath });
        await DownloadFileAsync(packageUrl, downloadPath);
        FileChecksum packageChecksum = checksums.GetRequired("NightOwl.Agent.Windows.zip");
        ValidateFile(downloadPath, packageChecksum);
        WriteLog("checksum.ok", "Package checksum validated.", new { packageChecksum.Sha256, packageChecksum.Size });

        string stagingPath = Path.Combine(StagingRoot, SanitizePathSegment(manifest.Version));
        if (Directory.Exists(stagingPath))
        {
            Directory.Delete(stagingPath, recursive: true);
        }
        Directory.CreateDirectory(stagingPath);
        ExtractZipSafe(downloadPath, stagingPath);
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

    private static int LaunchIndependentRunner(string[] args, bool interactive)
    {
        string runnerExe = CopyRunnerFiles();
        string arguments = BuildRunnerArguments(args);
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

    private static string BuildRunnerArguments(string[] args)
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
        return string.Join(" ", filtered.Select(QuoteArg));
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
        string backupPath = Path.Combine(BackupsRoot, $"{SanitizePathSegment(installed.Version)}-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}");

        WriteLog("updater.apply.started", "Aplicando atualizacao staged.", new { stagedPath, installPath, backupPath, from = installed.Version, to = manifest.Version });

        try
        {
            BackupInstall(installPath, backupPath);
            WriteLog("backup.created", "Install backup created.", new { backupPath });
            StopTray();
            WriteLog("tray.stop.done", "Tray process stopped.");
            WriteLog("service.stop.start", "Stopping service for update.");
            StopService();
            WriteLog("service.stop.done", "Service stopped for update.");
            WriteLog("files.copy.start", "Copying staged files to install path.", new { stagedPath, installPath });
            CopyStagedFiles(stagedPath, installPath);
            WriteLog("files.copy.done", "Staged files copied to install path.", new { stagedPath, installPath });
            WriteLocalVersion(installPath, manifest, packageSha256, "updater");
            StartService();
            WriteLog("service.start.done", "Service started after update.");
            StartTrayIfPossible(installPath);
            ValidatePostUpdate(installPath, manifest.Version);

            WriteLog("updater.apply.completed", "Atualizacao concluida.", new { version = manifest.Version });
            WriteLog("update.completed", "Update completed.", new { version = manifest.Version, previous_version = installed.Version });
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, "completed", 0, manifest.Version, installed.Version, "Agent updated successfully.", new { updated = true, version = manifest.Version, previous_version = installed.Version }, "");
            }
            WriteJson(new { ok = true, updated = true, version = manifest.Version, backupPath });
            if (interactive)
            {
                ShowMessage($"NightOwl Agent atualizado para {manifest.Version}.", MessageBoxIcon.Information);
            }
            return 0;
        }
        catch (Exception ex)
        {
            WriteLog("updater.apply.failed", "Falha ao aplicar atualizacao; tentando rollback.", new { error = ex.Message, backupPath });
            WriteLog("update.failed", "Update failed; attempting rollback.", new { error = ex.Message, backupPath });
            TryRollbackFromBackup(backupPath, installPath);
            if (jobContext.IsJob)
            {
                WritePendingUpdateResult(jobContext, "failed", 20, installed.Version, installed.Version, SanitizeMessage(ex.Message), new { updated = false, rollback_attempted = true, backupPath }, SanitizeMessage(ex.ToString()));
            }
            WriteJson(new { ok = false, updated = false, rollbackAttempted = true, error = ex.Message, backupPath });
            if (interactive)
            {
                ShowMessage("Falha ao atualizar. Rollback foi tentado. Detalhe: " + ex.Message, MessageBoxIcon.Error);
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
        string configPath = Path.Combine(DefaultInstallPath, "agent.config.json");
        if (!File.Exists(configPath))
        {
            configPath = Path.Combine(AppContext.BaseDirectory, "agent.config.json");
        }

        AgentConfig config = File.Exists(configPath)
            ? JsonSerializer.Deserialize<AgentConfig>(File.ReadAllText(configPath), JsonOptions) ?? new AgentConfig()
            : new AgentConfig();
        config.ConfigPath = configPath;
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

    private static void BackupInstall(string installPath, string backupPath)
    {
        Directory.CreateDirectory(backupPath);
        CopyDirectory(installPath, backupPath, overwrite: true, excludeNames: Array.Empty<string>());
        WriteLog("updater.backup.completed", "Backup da instalacao atual criado.", new { backupPath });
    }

    private static readonly string[] ProtectedInstallFileNames =
    {
        "agent.config.json",
        "agent-dotnet.state.json"
    };

    private static void CopyStagedFiles(string stagedPath, string installPath)
    {
        Directory.CreateDirectory(installPath);
        CopyDirectory(stagedPath, installPath, overwrite: true, excludeNames: ProtectedInstallFileNames);
        WriteLog("updater.files.copied", "Arquivos atualizados copiados para instalacao.", new { stagedPath, installPath });
    }

    private static void CopyDirectory(string source, string destination, bool overwrite, IReadOnlyCollection<string> excludeNames)
    {
        Directory.CreateDirectory(destination);
        foreach (string dir in Directory.GetDirectories(source, "*", SearchOption.AllDirectories))
        {
            string relative = Path.GetRelativePath(source, dir);
            Directory.CreateDirectory(Path.Combine(destination, relative));
        }
        foreach (string file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
        {
            string relative = Path.GetRelativePath(source, file);
            string name = Path.GetFileName(file);
            if (excludeNames.Contains(name, StringComparer.OrdinalIgnoreCase))
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
        foreach (Process process in Process.GetProcessesByName(TrayProcessName))
        {
            try
            {
                process.CloseMainWindow();
                if (!process.WaitForExit(3000))
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(5000);
                }
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
        if (!File.Exists(Path.Combine(installPath, "agent.config.json")))
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

    private static void WritePendingUpdateResult(JobContext jobContext, string status, int exitCode, string installedVersion, string previousVersion, string message, object result, string stderr)
    {
        if (!jobContext.IsJob)
        {
            return;
        }
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
            error_message = status == "failed" ? message : "",
            result = new
            {
                type = "update_agent",
                update_status = exitCode == 10 ? "no_update_available" : status == "completed" ? "success" : "failed",
                installed_version = installedVersion,
                previous_version = previousVersion,
                message,
                completed_at = DateTimeOffset.UtcNow,
                details = result
            }
        };
        File.WriteAllText(PendingUpdateResultPath, JsonSerializer.Serialize(payload, JsonOptions));
        WriteLog("updater.pending_result.written", "Resultado de update gravado para envio pelo agente.", new { jobContext.JobId, status, exitCode });
        WriteLog("pending_result.written", "Pending update result written.", new { jobContext.JobId, status, exitCode });
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
        Uri server = new(config.ServerBaseUrlOrDefault);
        Uri target = new(url);
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

    private static int CompareVersions(string left, string right)
    {
        Version l = ParseVersion(left);
        Version r = ParseVersion(right);
        return l.CompareTo(r);
    }

    private static Version ParseVersion(string value)
    {
        string clean = new((value ?? "0.0.0").TakeWhile(c => char.IsDigit(c) || c == '.').ToArray());
        if (Version.TryParse(string.IsNullOrWhiteSpace(clean) ? "0.0.0" : clean, out Version? version))
        {
            return version;
        }
        return new Version(0, 0, 0);
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

    private sealed class AgentConfig
    {
        [JsonPropertyName("agentVersion")]
        public string AgentVersion { get; set; } = "0.1.0.6";

        [JsonPropertyName("serverBaseUrl")]
        public string ServerBaseUrl { get; set; } = "";

        [JsonPropertyName("installPath")]
        public string InstallPath { get; set; } = DefaultInstallPath;

        [JsonIgnore]
        public string ConfigPath { get; set; } = "";

        [JsonIgnore]
        public string ServerBaseUrlOrDefault => string.IsNullOrWhiteSpace(ServerBaseUrl) ? DefaultServerUrl : ServerBaseUrl.TrimEnd('/');

        [JsonIgnore]
        public string InstallPathOrDefault => string.IsNullOrWhiteSpace(InstallPath) ? DefaultInstallPath : InstallPath;
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

        [JsonPropertyName("packageUrl")]
        public string PackageUrl { get; set; } = "";

        [JsonPropertyName("checksumUrl")]
        public string ChecksumUrl { get; set; } = "";

        [JsonPropertyName("installerUrl")]
        public string InstallerUrl { get; set; } = "";

        [JsonPropertyName("notes")]
        public string Notes { get; set; } = "";

        [JsonPropertyName("requiresRestart")]
        public bool RequiresRestart { get; set; } = true;

        [JsonPropertyName("force")]
        public bool Force { get; set; }
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

    private sealed class JobContext
    {
        public bool IsJob { get; init; }
        public string JobId { get; init; } = "";
        public string Channel { get; init; } = "stable";
        public string TargetVersion { get; init; } = "latest";

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
            };
        }
    }
}
