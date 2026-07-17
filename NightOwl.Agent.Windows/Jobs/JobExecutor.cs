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

    public JobExecutor(WindowsInventoryCollector collector, JsonlLogger logger)
    {
        _collector = collector;
        _logger = logger;
    }

    public async Task<JobExecutionResult> ExecuteAsync(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        DateTimeOffset started = DateTimeOffset.UtcNow;
        Stopwatch stopwatch = Stopwatch.StartNew();
        await _logger.LogAsync("job.started", "Job started.", new { job.Id, job.Type }, ct);

        try
        {
            if (!config.AllowedJobTypes.Contains(job.Type))
            {
                throw new NotSupportedException(job.Type == "update_agent"
                    ? "unsupported_job_type: este agente/config ainda nao permite update_agent; reinstale ou atualize o bootstrap do agente."
                    : "unsupported_job_type");
            }

            if (job.Type == "update_agent")
            {
                return await StartUpdateAgentAsync(config, job, started, stopwatch, ct);
            }
            if (job.Type == "restart_agent")
            {
                return await StartRestartAgentAsync(config, job, started, stopwatch, ct);
            }

            object result = job.Type switch
            {
                "force_inventory" => _collector.BuildCollectPayload(config),
                "collect_disks" => new { disks = await RunCollectionAsync("disks", _collector.GetDisks, ct), collected_at = DateTimeOffset.UtcNow },
                "collect_software" => new { installed_software = await RunCollectionAsync("software", _collector.GetSoftware, ct), collected_at = DateTimeOffset.UtcNow },
                "collect_security" => await RunCollectionAsync("security", _collector.GetSecurity, ct),
                "windows_update_scan" => await RunCollectionAsync("patches", _collector.GetPatchStatus, ct),
                "collect_logs" => CollectLogs(config),
                "ping" => await RunPingAsync(config, job, ct),
                _ => throw new NotSupportedException("unsupported_job_type")
            };

            stopwatch.Stop();
            await _logger.LogAsync("job.completed", "Job completed.", new { job.Id, job.Type, duration = stopwatch.Elapsed.TotalSeconds }, ct);
            return new JobExecutionResult
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
        }
        catch (Exception ex)
        {
            stopwatch.Stop();
            await _logger.LogAsync("job.failed", ex.Message, new { job.Id, job.Type, exception = ex.ToString() }, ct, "error");
            return new JobExecutionResult
            {
                JobId = job.Id,
                Status = "failed",
                StartedAt = started,
                FinishedAt = DateTimeOffset.UtcNow,
                DurationSeconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
                ExitCode = 1,
                Stderr = Trim(ex.ToString(), 8000),
                ErrorMessage = ex.Message
            };
        }
    }

    private static object CollectLogs(AgentConfig config)
    {
        if (!File.Exists(config.LogPath))
        {
            return new { lines = Array.Empty<string>(), log_path = config.LogPath };
        }

        string[] lines = File.ReadLines(config.LogPath).TakeLast(200).ToArray();
        return new { lines, log_path = config.LogPath };
    }

    private async Task<T> RunCollectionAsync<T>(string section, Func<T> collect, CancellationToken ct)
    {
        await _logger.LogAsync($"collection.{section}.started", $"{section} collection started by job.", null, ct);
        try
        {
            T result = collect();
            await _logger.LogAsync($"collection.{section}.completed", $"{section} collection completed by job.", null, ct);
            return result;
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
        string arguments = $"update --source job --job-id \"{job.Id}\" --channel \"{channel}\" --target-version \"{targetVersion}\" --quiet --json-output";
        await _logger.LogAsync("job.update_agent.started", "Starting updater for update_agent job.", new { job.Id, channel, targetVersion }, ct);

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
                target_version = targetVersion
            }
        };
    }

    private static string GetPayloadString(AgentJobRequest job, string key, string fallback)
    {
        return job.Payload.TryGetValue(key, out object? value) && value is not null && !string.IsNullOrWhiteSpace(value.ToString())
            ? value.ToString()!
            : fallback;
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
