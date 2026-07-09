using System.Diagnostics;
using System.Net.NetworkInformation;
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

    private static string Trim(string value, int max)
    {
        if (string.IsNullOrEmpty(value) || value.Length <= max)
        {
            return value;
        }

        return value[..max];
    }
}
