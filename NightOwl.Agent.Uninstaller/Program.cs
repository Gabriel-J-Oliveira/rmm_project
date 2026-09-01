using System.Diagnostics;
using System.Net.Http.Headers;
using System.ServiceProcess;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Uninstaller;

public static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private static readonly TimeSpan TrayStopTimeout = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan AgentProcessStopTimeout = TimeSpan.FromSeconds(15);
    private static readonly TimeSpan BinaryRemoveTimeout = TimeSpan.FromSeconds(25);

    public static async Task<int> Main(string[] args)
    {
        DateTimeOffset started = DateTimeOffset.UtcNow;
        UninstallOptions options;
        try
        {
            options = UninstallOptions.Parse(args);
            ValidateOptions(options);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"UNINSTALL_INVALID_PARAMETERS: {ex.Message}");
            return 2;
        }

        AgentConfigFile config = AgentConfigFile.Load(options.ConfigPath);
        string rootPath = string.IsNullOrWhiteSpace(options.RootPath)
            ? NightOwlPaths.Current.Root
            : options.RootPath;
        string installPath = string.IsNullOrWhiteSpace(options.InstallPath)
            ? Path.Combine(rootPath, "AgentDotNet")
            : options.InstallPath;
        string diagnosticsDir = Path.Combine(rootPath, "Diagnostics");
        string logPath = Path.Combine(rootPath, "Logs", "agent-uninstaller.jsonl");

        UninstallReceipt receipt = new()
        {
            JobId = options.JobId,
            Status = "completed",
            StartedAt = started,
            FinishedAt = DateTimeOffset.UtcNow,
            ExitCode = 0,
            Result = new
            {
                type = "uninstall_agent",
                mode = options.Mode,
                machine_id = config.MachineId,
                uninstall_status = "started"
            }
        };

        try
        {
            Directory.CreateDirectory(diagnosticsDir);
            Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
            WriteLog(logPath, "uninstall.start", new { options.JobId, options.Mode, install_path = installPath });
            await ConsumeLocalAuthorizationAsync(options.AuthorizationFile);
            EnsureNoActiveUpdate(rootPath);
            StopTray(logPath);
            StopService(options.ServiceName, installPath, logPath);
            RemoveServiceRegistration(options.ServiceName);
            RemoveTrayTask();
            RemoveDirectoryWithRetry(installPath, installPath, logPath, BinaryRemoveTimeout);
            if (Directory.Exists(installPath))
            {
                throw new InvalidOperationException("UNINSTALL_BINARY_REMOVE_FAILED: install path still exists after delete.");
            }

            if (options.Mode.Equals("purge", StringComparison.OrdinalIgnoreCase))
            {
                foreach (string relative in new[] { "Config", "Identity", "State", "Trust", "Packages", "Cache" })
                {
                    RemoveDirectoryWithRetry(Path.Combine(rootPath, relative), installPath, logPath, BinaryRemoveTimeout);
                }
            }
            else
            {
                WriteUninstalledState(rootPath);
            }

            receipt.FinishedAt = DateTimeOffset.UtcNow;
            receipt.DurationSeconds = Math.Round((receipt.FinishedAt - started).TotalSeconds, 3);
            receipt.Result = new
            {
                type = "uninstall_agent",
                mode = options.Mode,
                machine_id = config.MachineId,
                uninstall_status = "completed",
                completed_at = receipt.FinishedAt,
                binary_removed = !Directory.Exists(installPath),
                persistent_data_preserved = !options.Mode.Equals("purge", StringComparison.OrdinalIgnoreCase)
            };
            WriteReport(diagnosticsDir, options.Mode, receipt);
            await SendReceiptAsync(config, receipt, options.JobId);
            WriteLog(logPath, "uninstall.completed", new { options.JobId, options.Mode });
            return 0;
        }
        catch (Exception ex)
        {
            receipt.Status = "failed";
            receipt.ExitCode = 1;
            receipt.ErrorMessage = Sanitize(ex.Message);
            receipt.Stderr = Sanitize(ex.ToString());
            receipt.FinishedAt = DateTimeOffset.UtcNow;
            receipt.DurationSeconds = Math.Round((receipt.FinishedAt - started).TotalSeconds, 3);
            receipt.Result = new
            {
                type = "uninstall_agent",
                mode = options.Mode,
                error_code = ErrorCodeFrom(ex),
                error_message = Sanitize(ex.Message),
                machine_id = config.MachineId
            };
            WriteReport(diagnosticsDir, options.Mode, receipt);
            try { await SendReceiptAsync(config, receipt, options.JobId); } catch { }
            WriteLog(logPath, "uninstall.failed", new { options.JobId, options.Mode, error_code = ErrorCodeFrom(ex), error = Sanitize(ex.Message) });
            Console.Error.WriteLine($"{ErrorCodeFrom(ex)}: {Sanitize(ex.Message)}");
            return 1;
        }
    }

    private static void ValidateOptions(UninstallOptions options)
    {
        if (string.IsNullOrWhiteSpace(options.JobId) || !Guid.TryParse(options.JobId, out _))
        {
            throw new InvalidOperationException("job_id must be UUID.");
        }
        if (!options.Mode.Equals("uninstall", StringComparison.OrdinalIgnoreCase) && !options.Mode.Equals("purge", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("mode must be uninstall or purge.");
        }
        if (options.Mode.Equals("purge", StringComparison.OrdinalIgnoreCase) && !options.PurgeAuthorized)
        {
            throw new InvalidOperationException("REMOTE_PURGE_AUTHORIZATION_REQUIRED");
        }
    }

    private static void EnsureNoActiveUpdate(string rootPath)
    {
        using Mutex mutex = new(false, "Global\\NightOwl.Agent.Update");
        if (!mutex.WaitOne(TimeSpan.Zero))
        {
            throw new InvalidOperationException("UNINSTALL_UPDATE_IN_PROGRESS");
        }
        mutex.ReleaseMutex();

        string updateStatePath = Path.Combine(rootPath, "State", "update-state.json");
        if (!File.Exists(updateStatePath))
        {
            return;
        }
        try
        {
            using JsonDocument doc = JsonDocument.Parse(File.ReadAllText(updateStatePath));
            string status = doc.RootElement.TryGetProperty("status", out JsonElement statusElement) ? statusElement.GetString() ?? "" : "";
            string stage = doc.RootElement.TryGetProperty("current_stage", out JsonElement stageElement) ? stageElement.GetString() ?? "" : "";
            string[] activeStages =
            [
                "downloading", "validating", "staging", "creating_backup", "stopping_service",
                "replacing_files", "starting_service", "waiting_health_check",
                "rollback_required", "rollback_starting", "rollback_restoring_files",
                "rollback_stopping_service", "rollback_starting_service", "rollback_waiting_health_check"
            ];
            if (status.Equals("running", StringComparison.OrdinalIgnoreCase)
                || activeStages.Contains(stage, StringComparer.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("UNINSTALL_UPDATE_IN_PROGRESS");
            }
        }
        catch (JsonException ex)
        {
            throw new InvalidOperationException("UNINSTALL_UPDATE_STATE_INVALID", ex);
        }
    }

    private static void StopTray(string logPath)
    {
        Process[] processes = Process.GetProcessesByName("NightOwl.Agent.Tray");
        foreach (Process process in processes)
        {
            try
            {
                WriteLog(logPath, "uninstall.tray.stop_requested", new { pid = process.Id });
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: false);
                }
            }
            catch (Exception ex)
            {
                WriteLog(logPath, "uninstall.tray.stop_timeout", new { pid = SafeProcessId(process), error = Sanitize(ex.Message) });
            }
        }

        foreach (Process process in processes)
        {
            try
            {
                if (process.WaitForExit((int)TrayStopTimeout.TotalMilliseconds))
                {
                    WriteLog(logPath, "uninstall.tray.stopped", new { pid = process.Id });
                }
                else
                {
                    WriteLog(logPath, "uninstall.tray.stop_timeout", new { pid = process.Id, timeout_seconds = TrayStopTimeout.TotalSeconds });
                }
            }
            catch (Exception ex)
            {
                WriteLog(logPath, "uninstall.tray.stop_timeout", new { pid = SafeProcessId(process), error = Sanitize(ex.Message) });
            }
            finally
            {
                process.Dispose();
            }
        }
    }

    private static void StopService(string serviceName, string installPath, string logPath)
    {
        try
        {
            using ServiceController service = new(serviceName);
            if (service.Status != ServiceControllerStatus.Stopped && service.Status != ServiceControllerStatus.StopPending)
            {
                service.Stop();
            }
            service.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(30));
        }
        catch (InvalidOperationException)
        {
            // Service absent is idempotent.
        }
        WaitForNightOwlProcessesToExit(installPath, logPath, AgentProcessStopTimeout);
    }

    private static void RemoveServiceRegistration(string serviceName)
    {
        if (!ServiceExists(serviceName))
        {
            return;
        }

        using Process process = Process.Start(new ProcessStartInfo
        {
            FileName = "sc.exe",
            Arguments = $"delete \"{serviceName}\"",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        }) ?? throw new InvalidOperationException("UNINSTALL_SERVICE_REMOVE_FAILED");
        string stdout = process.StandardOutput.ReadToEnd();
        string stderr = process.StandardError.ReadToEnd();
        if (!process.WaitForExit(15000) || process.ExitCode != 0)
        {
            throw new InvalidOperationException($"UNINSTALL_SERVICE_REMOVE_FAILED: {Sanitize(stdout + " " + stderr).Trim()}");
        }

        DateTimeOffset deadline = DateTimeOffset.UtcNow.AddSeconds(30);
        while (DateTimeOffset.UtcNow < deadline)
        {
            if (!ServiceExists(serviceName))
            {
                return;
            }
            Thread.Sleep(500);
        }
        throw new InvalidOperationException("UNINSTALL_SERVICE_REMOVE_FAILED: service registration still exists after delete.");
    }

    private static bool ServiceExists(string serviceName)
    {
        try
        {
            using ServiceController service = new(serviceName);
            _ = service.Status;
            return true;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
    }

    private static void RemoveTrayTask()
    {
        RunBestEffort("schtasks.exe", "/Delete /TN \"NightOwl Agent Tray\" /F");
    }

    private static void RemoveDirectory(string path)
    {
        if (!Directory.Exists(path))
        {
            return;
        }
        Directory.Delete(path, recursive: true);
    }

    private static int RemoveDirectoryWithRetry(
        string path,
        string installPath,
        string logPath,
        TimeSpan timeout,
        Action<string>? deleteDirectory = null,
        Action? waitForProcesses = null)
    {
        if (!Directory.Exists(path))
        {
            return 0;
        }

        WriteLog(logPath, "uninstall.binary_remove.started", new { path });
        DateTimeOffset started = DateTimeOffset.UtcNow;
        DateTimeOffset deadline = started.Add(timeout);
        int attempt = 0;
        Exception? lastError = null;
        deleteDirectory ??= RemoveDirectory;
        waitForProcesses ??= () => WaitForNightOwlProcessesToExit(installPath, logPath, TimeSpan.FromSeconds(3));

        while (DateTimeOffset.UtcNow <= deadline)
        {
            attempt++;
            try
            {
                deleteDirectory(path);
                if (!Directory.Exists(path))
                {
                    WriteLog(logPath, "uninstall.binary_remove.completed", new
                    {
                        path,
                        attempts = attempt,
                        elapsed_ms = (int)(DateTimeOffset.UtcNow - started).TotalMilliseconds
                    });
                    return attempt;
                }
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                lastError = ex;
                WriteLog(logPath, "uninstall.binary_remove.retry", new
                {
                    path,
                    attempt,
                    error = Sanitize(ex.Message),
                    active_process_scope = "install_path_only",
                    active_processes = GetNightOwlProcessSummary(installPath)
                });
                waitForProcesses();
                Thread.Sleep(attempt == 1 ? 500 : 1000);
                continue;
            }

            Thread.Sleep(250);
        }

        WriteLog(logPath, "uninstall.binary_remove.failed", new
        {
            path,
            attempts = attempt,
            error = Sanitize(lastError?.Message ?? "install path still exists after retry timeout"),
            active_process_scope = "install_path_only",
            active_processes = GetNightOwlProcessSummary(installPath)
        });
        throw new InvalidOperationException($"UNINSTALL_BINARY_REMOVE_FAILED: {Sanitize(lastError?.Message ?? "install path still exists after retry timeout")}");
    }

    public static int RemoveDirectoryWithRetryForTest(
        string path,
        TimeSpan timeout,
        Action<string> deleteDirectory,
        Action? waitForProcesses = null)
    {
        string logPath = Path.Combine(Path.GetTempPath(), "NightOwlUninstallerTests", "agent-uninstaller-test.jsonl");
        return RemoveDirectoryWithRetry(path, path, logPath, timeout, deleteDirectory, waitForProcesses);
    }

    private static void WaitForNightOwlProcessesToExit(string installPath, string logPath, TimeSpan timeout)
    {
        DateTimeOffset deadline = DateTimeOffset.UtcNow.Add(timeout);
        while (DateTimeOffset.UtcNow < deadline)
        {
            Process[] processes = GetNightOwlProcessesUnderInstallPath(installPath);
            if (processes.Length == 0)
            {
                return;
            }
            foreach (Process process in processes)
            {
                try { process.WaitForExit(500); } catch { }
                finally { process.Dispose(); }
            }
        }

        WriteLog(logPath, "uninstall.service.process_stop_timeout", new
        {
            install_path = installPath,
            active_processes = GetNightOwlProcessSummary(installPath)
        });
    }

    private static Process[] GetNightOwlProcessesUnderInstallPath(string installPath)
    {
        int currentPid = Environment.ProcessId;
        return Process.GetProcesses()
            .Where(process =>
            {
                try
                {
                    if (process.Id == currentPid || process.HasExited) { return false; }
                    string name = process.ProcessName;
                    if (!name.StartsWith("NightOwl.Agent.", StringComparison.OrdinalIgnoreCase)) { return false; }
                    string path = process.MainModule?.FileName ?? "";
                    return IsPathUnder(path, installPath);
                }
                catch
                {
                    return false;
                }
            })
            .ToArray();
    }

    private static object[] GetNightOwlProcessSummary(string installPath)
    {
        return GetNightOwlProcessesUnderInstallPath(installPath)
            .Select(process =>
            {
                try
                {
                    return new { pid = process.Id, name = process.ProcessName } as object;
                }
                finally
                {
                    process.Dispose();
                }
            })
            .ToArray();
    }

    private static bool IsPathUnder(string path, string root)
    {
        if (string.IsNullOrWhiteSpace(path) || string.IsNullOrWhiteSpace(root)) { return false; }
        try
        {
            string fullPath = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return fullPath.Equals(fullRoot, StringComparison.OrdinalIgnoreCase)
                || fullPath.StartsWith(fullRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                || fullPath.StartsWith(fullRoot + Path.AltDirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }

    private static int SafeProcessId(Process process)
    {
        try { return process.Id; } catch { return 0; }
    }

    private static void WriteUninstalledState(string rootPath)
    {
        string stateDir = Path.Combine(rootPath, "State");
        Directory.CreateDirectory(stateDir);
        string statePath = Path.Combine(stateDir, "agent.state.json");
        Dictionary<string, object?> state = new()
        {
            ["install_status"] = "uninstalled",
            ["uninstalled_at"] = DateTimeOffset.UtcNow
        };
        NightOwlFileStore.WriteAllText(statePath, JsonSerializer.Serialize(state, JsonOptions));
    }

    private static void RunBestEffort(string fileName, string arguments)
    {
        try
        {
            using Process process = Process.Start(new ProcessStartInfo
            {
                FileName = fileName,
                Arguments = arguments,
                UseShellExecute = false,
                CreateNoWindow = true
            })!;
            process.WaitForExit(15000);
        }
        catch { }
    }

    private static async Task SendReceiptAsync(AgentConfigFile config, UninstallReceipt receipt, string jobId)
    {
        if (string.IsNullOrWhiteSpace(config.JobsResultUrl) || string.IsNullOrWhiteSpace(config.AgentToken))
        {
            return;
        }
        using HttpClient client = new() { Timeout = TimeSpan.FromSeconds(30) };
        using HttpRequestMessage request = new(HttpMethod.Post, config.JobsResultUrl);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", config.AgentToken);
        request.Headers.TryAddWithoutValidation("Idempotency-Key", $"uninstall-{jobId}");
        request.Content = new StringContent(JsonSerializer.Serialize(receipt, JsonOptions), Encoding.UTF8, "application/json");
        using HttpResponseMessage response = await client.SendAsync(request);
        response.EnsureSuccessStatusCode();
    }

    private static async Task ConsumeLocalAuthorizationAsync(string authorizationFile)
    {
        if (string.IsNullOrWhiteSpace(authorizationFile))
        {
            return;
        }
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(authorizationFile));
            JsonElement root = document.RootElement;
            string consumeUrl = root.TryGetProperty("consume_url", out JsonElement consumeElement) ? consumeElement.GetString() ?? "" : "";
            string machineId = root.TryGetProperty("machine_id", out JsonElement machineElement) ? machineElement.GetString() ?? "" : "";
            string token = root.TryGetProperty("authorization_token", out JsonElement tokenElement) ? tokenElement.GetString() ?? "" : "";
            if (string.IsNullOrWhiteSpace(consumeUrl) || string.IsNullOrWhiteSpace(machineId) || string.IsNullOrWhiteSpace(token))
            {
                throw new InvalidOperationException("UNINSTALL_AUTHORIZATION_INVALID");
            }
            using HttpClient client = new() { Timeout = TimeSpan.FromSeconds(30) };
            string body = JsonSerializer.Serialize(new { machine_id = machineId, authorization_token = token });
            using HttpResponseMessage response = await client.PostAsync(consumeUrl, new StringContent(body, Encoding.UTF8, "application/json"));
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException("UNINSTALL_AUTHORIZATION_REJECTED");
            }
        }
        finally
        {
            try { File.Delete(authorizationFile); } catch { }
        }
    }

    private static void WriteReport(string diagnosticsDir, string mode, UninstallReceipt receipt)
    {
        Directory.CreateDirectory(diagnosticsDir);
        string path = Path.Combine(diagnosticsDir, $"{mode}-job-{receipt.JobId}-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}.json");
        NightOwlFileStore.WriteAllText(path, JsonSerializer.Serialize(receipt, JsonOptions));
    }

    private static void WriteLog(string path, string eventType, object metadata)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            string line = JsonSerializer.Serialize(new { timestamp = DateTimeOffset.UtcNow, event_type = eventType, metadata }, JsonOptions);
            File.AppendAllText(path, line + Environment.NewLine, Encoding.UTF8);
        }
        catch { }
    }

    private static string ErrorCodeFrom(Exception ex)
    {
        string message = ex.Message;
        if (message.Contains("UPDATE_IN_PROGRESS", StringComparison.OrdinalIgnoreCase)) { return "UNINSTALL_UPDATE_IN_PROGRESS"; }
        if (message.Contains("AUTHORIZATION_INVALID", StringComparison.OrdinalIgnoreCase) || message.Contains("AUTHORIZATION_REJECTED", StringComparison.OrdinalIgnoreCase)) { return "UNINSTALL_AUTHORIZATION_FAILED"; }
        if (message.Contains("AUTHORIZATION_REQUIRED", StringComparison.OrdinalIgnoreCase)) { return "REMOTE_PURGE_AUTHORIZATION_REQUIRED"; }
        if (message.Contains("UPDATE_STATE_INVALID", StringComparison.OrdinalIgnoreCase)) { return "UNINSTALL_UPDATE_STATE_INVALID"; }
        if (message.Contains("BINARY_REMOVE_FAILED", StringComparison.OrdinalIgnoreCase)) { return "UNINSTALL_BINARY_REMOVE_FAILED"; }
        return "UNINSTALL_AGENT_FAILED";
    }

    private static string Sanitize(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) { return ""; }
        foreach (string marker in new[] { "agentToken", "agent_token", "Authorization", "Bearer " })
        {
            value = value.Replace(marker, "[redacted]", StringComparison.OrdinalIgnoreCase);
        }
        return value.Length > 8000 ? value[..8000] : value;
    }
}

internal sealed class UninstallOptions
{
    public string JobId { get; set; } = "";
    public string Mode { get; set; } = "uninstall";
    public string ConfigPath { get; set; } = Path.Combine(NightOwlPaths.Current.ConfigDir, "agent.config.json");
    public string RootPath { get; set; } = NightOwlPaths.Current.Root;
    public string InstallPath { get; set; } = NightOwlPaths.Current.InstallDir;
    public string ServiceName { get; set; } = NightOwlPaths.ServiceName;
    public bool PurgeAuthorized { get; set; }
    public string AuthorizationFile { get; set; } = "";

    public static UninstallOptions Parse(string[] args)
    {
        UninstallOptions options = new();
        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];
            string Next() => i + 1 < args.Length ? args[++i] : throw new InvalidOperationException($"Missing value for {arg}.");
            switch (arg)
            {
                case "uninstall":
                    break;
                case "--job-id":
                    options.JobId = Next();
                    break;
                case "--mode":
                    options.Mode = Next();
                    break;
                case "--config-path":
                    options.ConfigPath = Next();
                    break;
                case "--root-path":
                    options.RootPath = Next();
                    break;
                case "--install-path":
                    options.InstallPath = Next();
                    break;
                case "--service-name":
                    options.ServiceName = Next();
                    break;
                case "--purge-authorized":
                    options.PurgeAuthorized = true;
                    break;
                case "--authorization-file":
                    options.AuthorizationFile = Next();
                    break;
                case "--json-output":
                case "--quiet":
                    break;
                default:
                    throw new InvalidOperationException($"Unexpected argument: {arg}");
            }
        }
        return options;
    }
}

internal sealed class AgentConfigFile
{
    [JsonPropertyName("agentToken")]
    public string AgentToken { get; set; } = "";

    [JsonPropertyName("machineId")]
    public string MachineId { get; set; } = "";

    [JsonPropertyName("jobsResultUrl")]
    public string JobsResultUrl { get; set; } = "";

    public static AgentConfigFile Load(string path)
    {
        if (!File.Exists(path))
        {
            return new AgentConfigFile();
        }
        return JsonSerializer.Deserialize<AgentConfigFile>(File.ReadAllText(path), new JsonSerializerOptions(JsonSerializerDefaults.Web))
            ?? new AgentConfigFile();
    }
}

internal sealed class UninstallReceipt
{
    [JsonPropertyName("job_id")]
    public string JobId { get; set; } = "";
    [JsonPropertyName("status")]
    public string Status { get; set; } = "completed";
    [JsonPropertyName("started_at")]
    public DateTimeOffset StartedAt { get; set; }
    [JsonPropertyName("finished_at")]
    public DateTimeOffset FinishedAt { get; set; }
    [JsonPropertyName("duration_seconds")]
    public double DurationSeconds { get; set; }
    [JsonPropertyName("exit_code")]
    public int ExitCode { get; set; }
    [JsonPropertyName("stdout")]
    public string Stdout { get; set; } = "uninstall runner completed";
    [JsonPropertyName("stderr")]
    public string Stderr { get; set; } = "";
    [JsonPropertyName("error_message")]
    public string ErrorMessage { get; set; } = "";
    [JsonPropertyName("result")]
    public object? Result { get; set; }
}
