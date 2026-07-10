using System.Diagnostics;
using System.Net.NetworkInformation;
using System.Text.Json;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

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
                throw new NotSupportedException("unsupported_job_type");
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
                "update_agent" => await RunUpdateAgentAsync(config, job, ct),
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

    private async Task<object> RunUpdateAgentAsync(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        await _logger.LogAsync("job.update_agent.received", "Update agent job received.", new { job.Id }, ct);
        string updater = Path.Combine(config.InstallPath, "NightOwl.Agent.Updater.exe");
        if (!File.Exists(updater))
        {
            throw new FileNotFoundException("Updater nao encontrado no endpoint.", updater);
        }

        string channel = GetPayloadString(job, "channel", "stable");
        string targetVersion = GetPayloadString(job, "target_version", "latest");
        string arguments = $"update --source job --job-id \"{job.Id}\" --channel \"{channel}\" --target-version \"{targetVersion}\" --quiet --json-output";

        using Process process = new()
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = updater,
                Arguments = arguments,
                WorkingDirectory = config.InstallPath,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            }
        };

        DateTimeOffset started = DateTimeOffset.UtcNow;
        process.Start();
        Task<string> stdoutTask = process.StandardOutput.ReadToEndAsync(ct);
        Task<string> stderrTask = process.StandardError.ReadToEndAsync(ct);
        await process.WaitForExitAsync(ct);
        string stdout = Trim(await stdoutTask, 8000);
        string stderr = Trim(await stderrTask, 8000);
        int exitCode = process.ExitCode;
        DateTimeOffset finished = DateTimeOffset.UtcNow;
        bool alreadyCurrent = exitCode == 10 || stdout.Contains("already_current", StringComparison.OrdinalIgnoreCase);
        bool success = exitCode == 0 || alreadyCurrent;

        await _logger.LogAsync(
            success ? "job.update_agent.completed" : "job.update_agent.failed",
            "Update agent process finished.",
            new { job.Id, exitCode, alreadyCurrent },
            ct,
            success ? "info" : "error");

        if (!success)
        {
            throw new InvalidOperationException($"Updater failed with exit code {exitCode}. {Trim(stderr, 500)}");
        }

        Dictionary<string, object?> parsed = TryParseJson(stdout);
        parsed["exit_code"] = exitCode;
        parsed["already_up_to_date"] = alreadyCurrent;
        parsed["started_at"] = started;
        parsed["finished_at"] = finished;
        ClearPendingUpdateResultIfCurrentJob(config, job.Id);
        return parsed;
    }

    private static void ClearPendingUpdateResultIfCurrentJob(AgentConfig config, string jobId)
    {
        try
        {
            string path = Path.Combine(config.JobsPath, "pending-update-result.json");
            if (!File.Exists(path))
            {
                return;
            }
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
            if (document.RootElement.TryGetProperty("job_id", out JsonElement value)
                && value.ValueKind == JsonValueKind.String
                && value.GetString() == jobId)
            {
                File.Delete(path);
            }
        }
        catch
        {
            // A stale pending result is retried by the service loop.
        }
    }

    private static string GetPayloadString(AgentJobRequest job, string key, string fallback)
    {
        return job.Payload.TryGetValue(key, out object? value) && value is not null && !string.IsNullOrWhiteSpace(value.ToString())
            ? value.ToString()!
            : fallback;
    }

    private static Dictionary<string, object?> TryParseJson(string value)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(value);
            return JsonSerializer.Deserialize<Dictionary<string, object?>>(document.RootElement.GetRawText()) ?? new Dictionary<string, object?>();
        }
        catch
        {
            return new Dictionary<string, object?> { ["output"] = Trim(value, 2000) };
        }
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
