using System.Diagnostics;
using System.IO.Compression;
using System.Net.Security;
using System.Net.Sockets;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Diagnostics;

internal static class Program
{
    private const string DiagnosticsVersion = "0.1";
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };

    private static async Task<int> Main(string[] args)
    {
        DiagnosticOptions options;
        try
        {
            options = DiagnosticOptions.Parse(args);
        }
        catch (ArgumentException ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 2;
        }

        if (!options.Command.Equals("collect", StringComparison.OrdinalIgnoreCase))
        {
            Console.Error.WriteLine("Uso: NightOwl.Agent.Diagnostics.exe collect [-OutputPath <dir>] [-IncludeWindowsEvents] [-NoNetworkTests]");
            return 2;
        }

        NightOwlPaths paths = NightOwlPaths.Current;
        string outputDir = string.IsNullOrWhiteSpace(options.OutputPath) ? paths.DiagnosticsDir : Path.GetFullPath(options.OutputPath);
        List<DiagnosticWarning> warnings = new();
        try
        {
            Directory.CreateDirectory(outputDir);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"DIAG_PERMISSION_DENIED: nao foi possivel criar OutputPath. {ex.Message}");
            return 3;
        }

        string stamp = DateTimeOffset.UtcNow.ToString("yyyyMMddTHHmmssZ");
        string hostname = Environment.MachineName;
        string staging = Path.Combine(Path.GetTempPath(), $"NightOwlDiagnostics-{Guid.NewGuid():N}");
        string archivePath = Path.Combine(outputDir, $"NightOwl-Diagnostics-{SanitizeFileName(hostname)}-{stamp}.zip");

        try
        {
            Directory.CreateDirectory(staging);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"DIAG_ARCHIVE_FAILED: nao foi possivel criar staging. {ex.Message}");
            return 4;
        }

        try
        {
            Collector collector = new(paths, options, staging, warnings);
            await collector.CollectAsync();
            Manifest manifest = collector.BuildManifest(hostname);
            collector.WriteJson("warnings.json", warnings);
            collector.WriteJson("manifest.json", manifest);

            if (!collector.ValidateStagingRedaction())
            {
                warnings.Add(new("DIAG_REDACTION_FAILED", "Um ou mais arquivos foram omitidos por conterem padroes sensiveis apos sanitizacao."));
                collector.WriteJson("warnings.json", warnings);
                manifest = collector.BuildManifest(hostname);
                collector.WriteJson("manifest.json", manifest);
            }

            long stagingSize = Directory.EnumerateFiles(staging, "*", SearchOption.AllDirectories).Sum(file => new FileInfo(file).Length);
            if (stagingSize > options.MaxArchiveSizeBytes)
            {
                warnings.Add(new("DIAG_LIMIT_REACHED", "Limite maximo do pacote atingido; logs foram reduzidos/omitidos.", new { size = stagingSize, limit = options.MaxArchiveSizeBytes }));
                collector.TrimLogsToFit();
                collector.WriteJson("warnings.json", warnings);
                collector.WriteJson("manifest.json", collector.BuildManifest(hostname));
            }

            if (File.Exists(archivePath))
            {
                File.Delete(archivePath);
            }
            ZipFile.CreateFromDirectory(staging, archivePath, CompressionLevel.Optimal, includeBaseDirectory: false);
            Console.WriteLine(archivePath);
            return warnings.Count == 0 ? 0 : 1;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"DIAG_ARCHIVE_FAILED: {ex.Message}");
            return 5;
        }
        finally
        {
            try { Directory.Delete(staging, recursive: true); } catch { }
        }
    }

    private static string SanitizeFileName(string value)
    {
        foreach (char c in Path.GetInvalidFileNameChars())
        {
            value = value.Replace(c, '-');
        }
        return value;
    }

    private sealed class Collector
    {
        private readonly NightOwlPaths _paths;
        private readonly DiagnosticOptions _options;
        private readonly string _staging;
        private readonly List<DiagnosticWarning> _warnings;
        private readonly List<string> _included = new();
        private readonly List<string> _omitted = new();
        private bool _redactionApplied;

        public Collector(NightOwlPaths paths, DiagnosticOptions options, string staging, List<DiagnosticWarning> warnings)
        {
            _paths = paths;
            _options = options;
            _staging = staging;
            _warnings = warnings;
        }

        public async Task CollectAsync()
        {
            WriteText("summary.txt", BuildSummaryText());
            WriteJson("system.json", CollectSystem());
            WriteJson("versions.json", CollectVersions());
            WriteJson("service.json", CollectService());
            WriteJson("filesystem.json", CollectFilesystem());
            WriteJson("permissions.json", CollectPermissions());
            WriteJson("config-summary.json", CollectConfigSummary());
            WriteJson("identity-summary.json", CollectIdentitySummary());
            WriteSanitizedJsonFile("update-state.sanitized.json", _paths.UpdateStatePath, "DIAG_STATE_INVALID", UpdateStateProjection);
            WriteJson("updates-summary.json", CollectUpdatesSummary());
            WriteJson("jobs-summary.json", CollectJobsSummary());
            WriteJson("pending-results-summary.json", CollectPendingResultsSummary());
            if (!_options.NoNetworkTests)
            {
                WriteJson("connectivity.json", await CollectConnectivityAsync());
            }
            else
            {
                WriteJson("connectivity.json", new { skipped = true });
            }
            CollectLogs();
            if (_options.IncludeWindowsEvents)
            {
                CollectWindowsEvents();
            }
            WriteJson("summary.json", CollectSummary());
        }

        public Manifest BuildManifest(string hostname)
        {
            List<ManifestFile> files = new();
            foreach (string file in Directory.EnumerateFiles(_staging, "*", SearchOption.AllDirectories).OrderBy(x => x, StringComparer.OrdinalIgnoreCase))
            {
                string relative = Path.GetRelativePath(_staging, file).Replace("\\", "/");
                files.Add(new ManifestFile(relative, new FileInfo(file).Length, Sha256File(file)));
            }
            return new Manifest
            {
                DiagnosticsVersion = DiagnosticsVersion,
                CreatedAt = DateTimeOffset.UtcNow,
                Hostname = hostname,
                Files = files,
                Omitted = _omitted,
                Warnings = _warnings,
                RedactionApplied = _redactionApplied
            };
        }

        public bool ValidateStagingRedaction()
        {
            bool ok = true;
            foreach (string file in Directory.EnumerateFiles(_staging, "*", SearchOption.AllDirectories).ToArray())
            {
                if (IsBinary(file))
                {
                    continue;
                }
                string text;
                try { text = File.ReadAllText(file); }
                catch { continue; }
                if (NightOwlSanitizer.ContainsSecretLikeContent(text))
                {
                    ok = false;
                    _omitted.Add(Path.GetRelativePath(_staging, file).Replace("\\", "/"));
                    File.Delete(file);
                }
            }
            return ok;
        }

        public void TrimLogsToFit()
        {
            string logsDir = Path.Combine(_staging, "logs");
            if (!Directory.Exists(logsDir))
            {
                return;
            }
            foreach (string file in Directory.EnumerateFiles(logsDir, "*", SearchOption.AllDirectories).OrderByDescending(file => new FileInfo(file).Length))
            {
                if (Directory.EnumerateFiles(_staging, "*", SearchOption.AllDirectories).Sum(path => new FileInfo(path).Length) <= _options.MaxArchiveSizeBytes)
                {
                    break;
                }
                _omitted.Add(Path.GetRelativePath(_staging, file).Replace("\\", "/"));
                File.Delete(file);
            }
        }

        public void WriteJson(string relativePath, object value)
        {
            WriteText(relativePath, JsonSerializer.Serialize(value, JsonOptions));
        }

        private void WriteText(string relativePath, string value)
        {
            SanitizationResult sanitized = NightOwlSanitizer.SanitizeText(value);
            _redactionApplied |= sanitized.RedactionApplied;
            string destination = Path.Combine(_staging, relativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.WriteAllText(destination, sanitized.Value, Encoding.UTF8);
            _included.Add(relativePath.Replace("\\", "/"));
        }

        private void WriteSanitizedJsonFile(string relativePath, string sourcePath, string warningCode, Func<JsonNode?, object?>? projector = null)
        {
            if (!File.Exists(sourcePath))
            {
                WriteJson(relativePath, new { exists = false });
                return;
            }
            try
            {
                string raw = File.ReadAllText(sourcePath);
                JsonNode? node = JsonNode.Parse(raw);
                object? projected = projector is null ? node : projector(node);
                string json = JsonSerializer.Serialize(projected, JsonOptions);
                SanitizationResult result = NightOwlSanitizer.SanitizeJson(json);
                _redactionApplied |= result.RedactionApplied;
                WriteText(relativePath, result.Value);
            }
            catch (Exception ex)
            {
                _warnings.Add(new(warningCode, $"Falha ao ler JSON: {sourcePath}", new { error = ex.Message }));
                WriteJson(relativePath, new { exists = true, valid_json = false, error_code = warningCode });
            }
        }

        private string BuildSummaryText()
        {
            return $"""
            NightOwl Agent Diagnostics
            Hostname: {Environment.MachineName}
            Created UTC: {DateTimeOffset.UtcNow:o}
            Root: {_paths.Root}
            Read-only: true
            """;
        }

        private object CollectSummary() => new
        {
            hostname = Environment.MachineName,
            created_at = DateTimeOffset.UtcNow,
            diagnostics_version = DiagnosticsVersion,
            service_name = NightOwlPaths.ServiceName,
            root = _paths.Root,
            read_only = true,
            warnings = _warnings.Count
        };

        private object CollectSystem()
        {
            return Safe("system", () =>
            {
                using WindowsIdentity identity = WindowsIdentity.GetCurrent();
                bool isAdmin = new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
                return new
                {
                    hostname = Environment.MachineName,
                    collected_at = DateTimeOffset.UtcNow,
                    os = Environment.OSVersion.VersionString,
                    os_version = Environment.OSVersion.Version.ToString(),
                    os_build = Environment.OSVersion.Version.Build,
                    architecture = RuntimeInformationSafe(),
                    uptime_seconds = Environment.TickCount64 / 1000,
                    user = identity.Name,
                    is_administrator = isAdmin,
                    volumes = DriveInfo.GetDrives()
                        .Where(drive => drive.IsReady)
                        .Select(drive => new { name = drive.Name, type = drive.DriveType.ToString(), total_bytes = drive.TotalSize, free_bytes = drive.AvailableFreeSpace })
                        .ToArray()
                };
            }, "DIAG_PERMISSION_DENIED");
        }

        private object CollectVersions()
        {
            return new
            {
                agent = FileVersion(Path.Combine(_paths.InstallDir, "NightOwl.Agent.Windows.exe")),
                updater = FileVersion(Path.Combine(_paths.InstallDir, "NightOwl.Agent.Updater.exe")),
                tray = FileVersion(Path.Combine(_paths.InstallDir, "NightOwl.Agent.Tray.exe")),
                diagnostics = Assembly.GetExecutingAssembly().GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion?.Split('+')[0] ?? Assembly.GetExecutingAssembly().GetName().Version?.ToString(),
                agent_version_json = ReadJsonProjection(_paths.VersionPath, node => node),
                hashes = new
                {
                    agent = FileHashIfExists(Path.Combine(_paths.InstallDir, "NightOwl.Agent.Windows.exe")),
                    updater = FileHashIfExists(Path.Combine(_paths.InstallDir, "NightOwl.Agent.Updater.exe")),
                    tray = FileHashIfExists(Path.Combine(_paths.InstallDir, "NightOwl.Agent.Tray.exe")),
                    diagnostics = FileHashIfExists(Environment.ProcessPath ?? "")
                }
            };
        }

        private object CollectService()
        {
            return Safe("service", () =>
            {
                ServiceController? service = ServiceController.GetServices().FirstOrDefault(s => s.ServiceName.Equals(NightOwlPaths.ServiceName, StringComparison.OrdinalIgnoreCase));
                Process[] nightOwlProcesses = Process.GetProcesses()
                    .Where(process => process.ProcessName.Contains("NightOwl", StringComparison.OrdinalIgnoreCase))
                    .Select(process => process)
                    .ToArray();
                return new
                {
                    exists = service is not null,
                    status = service?.Status.ToString() ?? "",
                    service_name = NightOwlPaths.ServiceName,
                    process = nightOwlProcesses.Select(process => new
                    {
                        process.ProcessName,
                        process.Id,
                        start_time = TryGet(() => (DateTimeOffset?)process.StartTime.ToUniversalTime()),
                        uptime_seconds = TryGet(() => (double?)Math.Round((DateTime.Now - process.StartTime).TotalSeconds, 0))
                    }).ToArray(),
                    windows_service = QueryWin32Service()
                };
            }, "DIAG_PERMISSION_DENIED");
        }

        private object QueryWin32Service()
        {
            try
            {
                using Process process = Process.Start(new ProcessStartInfo
                {
                    FileName = "sc.exe",
                    Arguments = $"qc {NightOwlPaths.ServiceName}",
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true
                })!;
                string output = process.StandardOutput.ReadToEnd();
                process.WaitForExit(5000);
                return new { raw = NightOwlSanitizer.SanitizeText(output).Value };
            }
            catch (Exception ex)
            {
                _warnings.Add(new("DIAG_SERVICE_READ_FAILED", "Falha ao consultar sc.exe.", new { error = ex.Message }));
                return new { error = ex.Message };
            }
        }

        private object CollectFilesystem()
        {
            string[] dirs = { _paths.InstallDir, _paths.ConfigDir, _paths.IdentityDir, _paths.StateDir, _paths.LogsDir, _paths.UpdatesDir, _paths.DiagnosticsDir };
            return new
            {
                directories = dirs.Select(dir => new
                {
                    path = dir,
                    exists = Directory.Exists(dir),
                    size_bytes = Directory.Exists(dir) ? DirectorySize(dir) : 0,
                    preserved_files = Directory.Exists(dir) ? Directory.EnumerateFiles(dir, "*.preserved-*", SearchOption.AllDirectories).Take(50).Select(Path.GetFileName).ToArray() : Array.Empty<string>()
                }),
                required_files = new[]
                {
                    Path.Combine(_paths.InstallDir, "NightOwl.Agent.Windows.exe"),
                    Path.Combine(_paths.InstallDir, "NightOwl.Agent.Updater.exe"),
                    Path.Combine(_paths.InstallDir, "NightOwl.Agent.Tray.exe"),
                    Path.Combine(_paths.InstallDir, "NightOwl.Agent.Diagnostics.exe"),
                    _paths.ConfigPath,
                    _paths.IdentityPath,
                    _paths.StatePath
                }.Select(file => new { path = file, exists = File.Exists(file), size_bytes = File.Exists(file) ? new FileInfo(file).Length : 0 })
            };
        }

        private object CollectPermissions()
        {
            string[] dirs = { _paths.InstallDir, _paths.ConfigDir, _paths.IdentityDir, _paths.StateDir, _paths.UpdatesDir, _paths.DiagnosticsDir };
            return dirs.Select(dir => Safe($"acl:{dir}", () =>
            {
                if (!Directory.Exists(dir))
                {
                    return new { path = dir, exists = false };
                }
                DirectoryInfo info = new(dir);
                DirectorySecurity security = info.GetAccessControl();
                IdentityReference? owner = security.GetOwner(typeof(NTAccount));
                var rules = security.GetAccessRules(true, true, typeof(NTAccount))
                    .Cast<FileSystemAccessRule>()
                    .Select(rule => new { identity = rule.IdentityReference.Value, rights = rule.FileSystemRights.ToString(), type = rule.AccessControlType.ToString(), inherited = rule.IsInherited })
                    .ToArray();
                return new { path = dir, exists = true, owner = owner?.Value ?? "", rules };
            }, "DIAG_PERMISSION_DENIED")).ToArray();
        }

        private object CollectConfigSummary()
        {
            return Safe("config", () =>
            {
                if (!File.Exists(_paths.ConfigPath))
                {
                    return new { exists = false };
                }
                using JsonDocument document = JsonDocument.Parse(File.ReadAllText(_paths.ConfigPath));
                JsonElement root = document.RootElement;
                string token = JsonString(root, "agentToken", "agent_token");
                string machineId = JsonString(root, "machineId", "machine_id");
                string serverUrl = JsonString(root, "serverBaseUrl", "server_base_url");
                return new
                {
                    exists = true,
                    valid_json = true,
                    server_url = NightOwlSanitizer.SanitizeUrl(serverUrl),
                    schema_version = JsonString(root, "schemaVersion", "schema_version"),
                    token_present = !string.IsNullOrWhiteSpace(token),
                    token_hash = NightOwlSanitizer.MaskIdentifier(token),
                    machine_id_present = !string.IsNullOrWhiteSpace(machineId),
                    machine_id = NightOwlSanitizer.MaskIdentifier(machineId),
                    missing_required = new[] { ("serverBaseUrl", serverUrl), ("agentToken", token), ("machineId", machineId) }.Where(item => string.IsNullOrWhiteSpace(item.Item2)).Select(item => item.Item1).ToArray()
                };
            }, "DIAG_CONFIG_INVALID");
        }

        private object CollectIdentitySummary()
        {
            return Safe("identity", () =>
            {
                if (!File.Exists(_paths.IdentityPath))
                {
                    return new { exists = false };
                }
                using JsonDocument document = JsonDocument.Parse(File.ReadAllText(_paths.IdentityPath));
                string identityMachineId = JsonString(document.RootElement, "machine_id", "machineId");
                string configMachineId = "";
                try
                {
                    using JsonDocument config = JsonDocument.Parse(File.ReadAllText(_paths.ConfigPath));
                    configMachineId = JsonString(config.RootElement, "machineId", "machine_id");
                }
                catch { }
                return new
                {
                    exists = true,
                    valid_json = true,
                    machine_id_present = !string.IsNullOrWhiteSpace(identityMachineId),
                    machine_id = NightOwlSanitizer.MaskIdentifier(identityMachineId),
                    config_identity_match = string.IsNullOrWhiteSpace(configMachineId) || string.IsNullOrWhiteSpace(identityMachineId) || configMachineId == identityMachineId
                };
            }, "DIAG_IDENTITY_INVALID");
        }

        private object? UpdateStateProjection(JsonNode? node)
        {
            JsonObject? obj = node as JsonObject;
            if (obj is null)
            {
                return new { exists = true, valid_json = false };
            }
            string[] keep =
            {
                "update_id", "job_id", "from_version", "target_version", "current_stage", "status", "attempt",
                "started_at", "updated_at", "completed_at", "health_check_confirmed", "rollback_attempt",
                "rollback_required", "rollback_started_at", "rollback_completed_at", "error_code",
                "rollback_error_code", "rollback_error_message", "error_message"
            };
            Dictionary<string, object?> projected = new(StringComparer.OrdinalIgnoreCase);
            foreach (string key in keep)
            {
                if (obj.TryGetPropertyValue(key, out JsonNode? value))
                {
                    projected[key] = value?.Deserialize<object>();
                }
            }
            if (obj.TryGetPropertyValue("package_url", out JsonNode? packageUrl))
            {
                projected["package_url"] = NightOwlSanitizer.SanitizeUrl(packageUrl?.GetValue<string>());
            }
            return projected;
        }

        private object CollectUpdatesSummary()
        {
            string[] backups = Directory.Exists(_paths.UpdatesBackupDir) ? Directory.GetDirectories(_paths.UpdatesBackupDir) : Array.Empty<string>();
            string[] stagings = Directory.Exists(_paths.UpdatesStagingDir) ? Directory.GetDirectories(_paths.UpdatesStagingDir) : Array.Empty<string>();
            string? latestBackup = backups.OrderByDescending(Directory.GetCreationTimeUtc).FirstOrDefault();
            return new
            {
                backups_count = backups.Length,
                staging_count = stagings.Length,
                latest_backup = latestBackup is null ? null : new
                {
                    path = Path.GetFileName(latestBackup),
                    size_bytes = DirectorySize(latestBackup),
                    manifest = ReadJsonProjection(Path.Combine(latestBackup, "backup-manifest.json"), node => node),
                    hashes_valid_basic = ValidateBackupHashes(latestBackup)
                },
                update_state_exists = File.Exists(_paths.UpdateStatePath)
            };
        }

        private object CollectJobsSummary()
        {
            string jobsDir = Path.Combine(_paths.StateDir, "jobs");
            if (!Directory.Exists(jobsDir))
            {
                return new { exists = false, jobs = Array.Empty<object>() };
            }
            List<object> jobs = new();
            foreach (string file in Directory.EnumerateFiles(jobsDir, "*.json").OrderByDescending(File.GetLastWriteTimeUtc).Take(_options.MaxJobs))
            {
                try
                {
                    using JsonDocument document = JsonDocument.Parse(File.ReadAllText(file));
                    JsonElement root = document.RootElement;
                    JsonElement result = root.TryGetProperty("result", out JsonElement r) ? r : default;
                    jobs.Add(new
                    {
                        job_id = JsonString(root, "job_id", "jobId", "id"),
                        job_type = JsonString(root, "job_type", "jobType", "type"),
                        status = JsonString(root, "status"),
                        created_at = JsonString(root, "created_at", "createdAt", "updated_at"),
                        started_at = result.ValueKind == JsonValueKind.Object ? JsonString(result, "started_at", "startedAt") : "",
                        completed_at = result.ValueKind == JsonValueKind.Object ? JsonString(result, "completed_at", "finished_at", "completedAt") : "",
                        duration_ms = result.ValueKind == JsonValueKind.Object ? JsonString(result, "duration_ms", "durationMs") : "",
                        attempt = JsonString(root, "attempt"),
                        error_code = result.ValueKind == JsonValueKind.Object ? JsonString(result, "error_code", "errorCode") : "",
                        output_truncated = result.ValueKind == JsonValueKind.Object ? JsonString(result, "output_truncated", "outputTruncated") : ""
                    });
                }
                catch (Exception ex)
                {
                    _warnings.Add(new("DIAG_STATE_INVALID", "Falha ao ler estado de job.", new { file = Path.GetFileName(file), error = ex.Message }));
                }
            }
            return new { exists = true, count = jobs.Count, jobs };
        }

        private object CollectPendingResultsSummary()
        {
            return SummarizePendingDirectory(_paths.PendingResultsDir);
        }

        private object SummarizePendingDirectory(string dir)
        {
            if (!Directory.Exists(dir))
            {
                return new { exists = false, pending_count = 0 };
            }
            List<object> records = new();
            int retrying = 0;
            int quarantined = Directory.Exists(Path.Combine(dir, "quarantine")) ? Directory.EnumerateFiles(Path.Combine(dir, "quarantine"), "*.json").Count() : 0;
            DateTimeOffset? oldest = null;
            int maxAttempts = 0;
            long totalSize = 0;
            foreach (string file in Directory.EnumerateFiles(dir, "*.json").OrderByDescending(File.GetLastWriteTimeUtc).Take(_options.MaxPendingResults))
            {
                totalSize += new FileInfo(file).Length;
                try
                {
                    using JsonDocument document = JsonDocument.Parse(File.ReadAllText(file));
                    JsonElement root = document.RootElement;
                    int attempts = JsonInt(root, "attempt_count", "attemptCount");
                    maxAttempts = Math.Max(maxAttempts, attempts);
                    string next = JsonString(root, "next_attempt_at", "nextAttemptAt");
                    if (!string.IsNullOrWhiteSpace(next) && DateTimeOffset.TryParse(next, out DateTimeOffset nextAt) && nextAt > DateTimeOffset.UtcNow)
                    {
                        retrying++;
                    }
                    string created = JsonString(root, "created_at", "createdAt");
                    if (DateTimeOffset.TryParse(created, out DateTimeOffset createdAt))
                    {
                        oldest = oldest is null || createdAt < oldest ? createdAt : oldest;
                    }
                    records.Add(new
                    {
                        result_id = JsonString(root, "result_id", "resultId"),
                        job_id = JsonString(root, "job_id", "jobId"),
                        job_type = JsonString(root, "job_type", "jobType"),
                        status = JsonString(root, "status"),
                        created_at = created,
                        attempt_count = attempts,
                        next_attempt_at = next,
                        last_error_code = JsonString(root, "last_error_code", "lastErrorCode"),
                        payload_size_bytes = JsonRawPayloadSize(root)
                    });
                }
                catch (Exception ex)
                {
                    _warnings.Add(new("RESULT_QUEUE_CORRUPTED", "Falha ao ler pending-result.", new { file = Path.GetFileName(file), error = ex.Message }));
                }
            }
            return new { exists = true, pending_count = records.Count, retrying_count = retrying, quarantined_count = quarantined, oldest_pending_at = oldest, max_attempt_count = maxAttempts, total_size_bytes = totalSize, records };
        }

        private async Task<object> CollectConnectivityAsync()
        {
            string serverUrl = "";
            try
            {
                using JsonDocument config = JsonDocument.Parse(File.ReadAllText(_paths.ConfigPath));
                serverUrl = JsonString(config.RootElement, "serverBaseUrl", "server_base_url");
            }
            catch { }
            if (string.IsNullOrWhiteSpace(serverUrl) || !Uri.TryCreate(serverUrl, UriKind.Absolute, out Uri? uri))
            {
                _warnings.Add(new("DIAG_CONNECTIVITY_FAILED", "ServerUrl ausente ou invalido."));
                return new { tested = false, reason = "server_url_missing" };
            }

            Stopwatch sw = Stopwatch.StartNew();
            try
            {
                using TcpClient tcp = new();
                using CancellationTokenSource cts = new(_options.NetworkTimeout);
                await tcp.ConnectAsync(uri.Host, uri.Port > 0 ? uri.Port : 443, cts.Token);
                using SslStream ssl = new(tcp.GetStream(), false, (_, certificate, chain, errors) => true);
                await ssl.AuthenticateAsClientAsync(uri.Host);
                using HttpClient client = new() { Timeout = _options.NetworkTimeout };
                Uri versionUri = new(new Uri(serverUrl.TrimEnd('/') + "/"), "downloads/nightowl-agent/version.json");
                using HttpResponseMessage response = await client.GetAsync(versionUri);
                sw.Stop();
                return new
                {
                    tested = true,
                    server_url = NightOwlSanitizer.SanitizeUrl(serverUrl),
                    dns_host = uri.Host,
                    tcp_443 = true,
                    tls = true,
                    certificate_subject = ssl.RemoteCertificate?.Subject,
                    certificate_issuer = ssl.RemoteCertificate?.Issuer,
                    http_status = (int)response.StatusCode,
                    latency_ms = sw.ElapsedMilliseconds
                };
            }
            catch (Exception ex)
            {
                _warnings.Add(new("DIAG_CONNECTIVITY_FAILED", "Falha no teste de conectividade.", new { error = ex.Message }));
                return new { tested = true, server_url = NightOwlSanitizer.SanitizeUrl(serverUrl), success = false, error = ex.Message, latency_ms = sw.ElapsedMilliseconds };
            }
        }

        private void CollectLogs()
        {
            string logsOut = Path.Combine(_staging, "logs");
            Directory.CreateDirectory(logsOut);
            long total = 0;
            foreach (string path in new[] { _paths.AgentLogPath, _paths.UpdaterLogPath, _paths.TrayLogPath })
            {
                CopyLogTail(path, Path.Combine("logs", Path.GetFileName(path)), ref total);
            }
            if (Directory.Exists(_paths.DiagnosticsDir))
            {
                foreach (string report in Directory.EnumerateFiles(_paths.DiagnosticsDir, "*-report-*.json").OrderByDescending(File.GetLastWriteTimeUtc).Take(10))
                {
                    CopyLogTail(report, Path.Combine("logs", "reports", Path.GetFileName(report)), ref total);
                }
            }
        }

        private void CopyLogTail(string source, string relativeDestination, ref long totalBytes)
        {
            if (!File.Exists(source))
            {
                _omitted.Add(relativeDestination.Replace("\\", "/"));
                return;
            }
            try
            {
                FileInfo info = new(source);
                long take = Math.Min(info.Length, _options.MaxLogSizeBytes);
                if (totalBytes + take > _options.MaxTotalLogsBytes)
                {
                    _warnings.Add(new("DIAG_LIMIT_REACHED", "Limite total de logs atingido.", new { file = source }));
                    _omitted.Add(relativeDestination.Replace("\\", "/"));
                    return;
                }
                using FileStream fs = File.Open(source, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                if (fs.Length > take)
                {
                    fs.Seek(-take, SeekOrigin.End);
                }
                using StreamReader reader = new(fs, Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
                string text = reader.ReadToEnd();
                SanitizationResult sanitized = NightOwlSanitizer.SanitizeText(text);
                _redactionApplied |= sanitized.RedactionApplied;
                WriteText(relativeDestination, (fs.Length > take ? "[TRUNCATED]\n" : "") + sanitized.Value);
                totalBytes += take;
            }
            catch (Exception ex)
            {
                _warnings.Add(new("DIAG_LOG_READ_FAILED", "Falha ao ler log.", new { file = source, error = ex.Message }));
            }
        }

        private void CollectWindowsEvents()
        {
            try
            {
                List<object> events = new();
                DateTime cutoff = DateTime.UtcNow.AddHours(-_options.EventHours);
                foreach (string logName in new[] { "Application", "System" })
                {
                    using EventLog log = new(logName);
                    foreach (EventLogEntry entry in log.Entries.Cast<EventLogEntry>().OfType<EventLogEntry>().Reverse())
                    {
                        if (events.Count >= _options.MaxEvents) { break; }
                        if (entry.TimeGenerated.ToUniversalTime() < cutoff) { break; }
                        string source = entry.Source ?? "";
                        string message = entry.Message ?? "";
                        if (source.Contains("NightOwl", StringComparison.OrdinalIgnoreCase)
                            || source.Contains(".NET Runtime", StringComparison.OrdinalIgnoreCase)
                            || source.Contains("Application Error", StringComparison.OrdinalIgnoreCase)
                            || source.Contains("Service Control Manager", StringComparison.OrdinalIgnoreCase)
                            || message.Contains("NightOwl", StringComparison.OrdinalIgnoreCase)
                            || message.Contains("NightOwlAgentDotNet", StringComparison.OrdinalIgnoreCase))
                        {
                            events.Add(new
                            {
                                log = logName,
                                source,
                                entry_type = entry.EntryType.ToString(),
                                event_id = entry.InstanceId,
                                time_utc = entry.TimeGenerated.ToUniversalTime(),
                                message = NightOwlSanitizer.SanitizeText(message).Value
                            });
                        }
                    }
                }
                WriteJson("events/windows-events.json", events);
            }
            catch (Exception ex)
            {
                _warnings.Add(new("DIAG_EVENT_READ_FAILED", "Falha ao coletar eventos do Windows.", new { error = ex.Message }));
            }
        }

        private object Safe(string component, Func<object> action, string warningCode)
        {
            try { return action(); }
            catch (Exception ex)
            {
                _warnings.Add(new(warningCode, $"Falha ao coletar {component}.", new { error = ex.Message }));
                return new { error_code = warningCode, error = ex.Message };
            }
        }

        private static string RuntimeInformationSafe() => System.Runtime.InteropServices.RuntimeInformation.OSArchitecture.ToString();

        private static T? TryGet<T>(Func<T> action)
        {
            try { return action(); } catch { return default; }
        }

        private static string FileVersion(string path)
        {
            try
            {
                if (!File.Exists(path)) { return ""; }
                FileVersionInfo info = FileVersionInfo.GetVersionInfo(path);
                return info.ProductVersion ?? info.FileVersion ?? "";
            }
            catch { return ""; }
        }

        private static string FileHashIfExists(string path) => File.Exists(path) ? Sha256File(path) : "";

        private static string Sha256File(string path)
        {
            using FileStream stream = File.OpenRead(path);
            return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
        }

        private static bool IsBinary(string path)
        {
            string ext = Path.GetExtension(path).ToLowerInvariant();
            return ext is ".zip" or ".exe" or ".dll" or ".ico";
        }

        private static long DirectorySize(string dir)
        {
            try { return Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories).Sum(file => new FileInfo(file).Length); }
            catch { return 0; }
        }

        private static object? ReadJsonProjection(string path, Func<JsonNode?, object?> projector)
        {
            try
            {
                if (!File.Exists(path)) { return null; }
                JsonNode? node = JsonNode.Parse(File.ReadAllText(path));
                return projector(node);
            }
            catch { return new { valid_json = false }; }
        }

        private static bool ValidateBackupHashes(string backupDir)
        {
            try
            {
                string manifestPath = Path.Combine(backupDir, "backup-manifest.json");
                if (!File.Exists(manifestPath)) { return false; }
                using JsonDocument document = JsonDocument.Parse(File.ReadAllText(manifestPath));
                if (!document.RootElement.TryGetProperty("files", out JsonElement files) || files.ValueKind != JsonValueKind.Array) { return false; }
                foreach (JsonElement item in files.EnumerateArray().Take(20))
                {
                    string relative = JsonString(item, "relative_path", "path", "name");
                    string expected = JsonString(item, "sha256");
                    string file = Path.Combine(backupDir, relative);
                    if (string.IsNullOrWhiteSpace(relative) || string.IsNullOrWhiteSpace(expected) || !File.Exists(file)) { return false; }
                    if (!Sha256File(file).Equals(expected, StringComparison.OrdinalIgnoreCase)) { return false; }
                }
                return true;
            }
            catch { return false; }
        }

        private static string JsonString(JsonElement element, params string[] names)
        {
            foreach (string name in names)
            {
                if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out JsonElement value))
                {
                    return value.ValueKind switch
                    {
                        JsonValueKind.String => value.GetString() ?? "",
                        JsonValueKind.Number => value.ToString(),
                        JsonValueKind.True => "true",
                        JsonValueKind.False => "false",
                        _ => value.ToString()
                    };
                }
            }
            return "";
        }

        private static int JsonInt(JsonElement element, params string[] names)
        {
            foreach (string name in names)
            {
                if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out JsonElement value))
                {
                    if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out int result)) { return result; }
                    if (value.ValueKind == JsonValueKind.String && int.TryParse(value.GetString(), out result)) { return result; }
                }
            }
            return 0;
        }

        private static long JsonRawPayloadSize(JsonElement root)
        {
            if (root.TryGetProperty("payload", out JsonElement payload))
            {
                return Encoding.UTF8.GetByteCount(payload.GetRawText());
            }
            return 0;
        }
    }

    private sealed class DiagnosticOptions
    {
        public string Command { get; init; } = "";
        public string OutputPath { get; init; } = "";
        public bool IncludeWindowsEvents { get; init; }
        public bool NoNetworkTests { get; init; }
        public bool NonInteractive { get; init; }
        public int MaxJobs { get; init; } = 50;
        public int MaxPendingResults { get; init; } = 100;
        public int MaxEvents { get; init; } = 100;
        public int EventHours { get; init; } = 24;
        public TimeSpan NetworkTimeout { get; init; } = TimeSpan.FromSeconds(10);
        public long MaxLogSizeBytes { get; init; } = 2 * 1024 * 1024;
        public long MaxTotalLogsBytes { get; init; } = 10 * 1024 * 1024;
        public long MaxArchiveSizeBytes { get; init; } = 25 * 1024 * 1024;

        public static DiagnosticOptions Parse(string[] args)
        {
            if (args.Length == 0) { throw new ArgumentException("Comando obrigatorio: collect."); }
            string outputPath = "";
            bool includeEvents = false;
            bool noNetwork = false;
            bool nonInteractive = false;
            long maxLogMb = 2;
            long maxArchiveMb = 25;
            for (int i = 1; i < args.Length; i++)
            {
                string arg = args[i];
                switch (arg.ToLowerInvariant())
                {
                    case "-outputpath":
                        outputPath = RequireValue(args, ref i, arg);
                        break;
                    case "-includewindowsevents":
                        includeEvents = true;
                        break;
                    case "-maxlogsizemb":
                        maxLogMb = ParsePositive(RequireValue(args, ref i, arg), arg);
                        break;
                    case "-maxarchivesizemb":
                        maxArchiveMb = ParsePositive(RequireValue(args, ref i, arg), arg);
                        break;
                    case "-nonetworktests":
                        noNetwork = true;
                        break;
                    case "-noninteractive":
                        nonInteractive = true;
                        break;
                    default:
                        throw new ArgumentException($"Argumento invalido: {arg}");
                }
            }
            return new DiagnosticOptions
            {
                Command = args[0],
                OutputPath = outputPath,
                IncludeWindowsEvents = includeEvents,
                NoNetworkTests = noNetwork,
                NonInteractive = nonInteractive,
                MaxLogSizeBytes = maxLogMb * 1024 * 1024,
                MaxArchiveSizeBytes = maxArchiveMb * 1024 * 1024
            };
        }

        private static string RequireValue(string[] args, ref int i, string name)
        {
            if (i + 1 >= args.Length) { throw new ArgumentException($"Valor ausente para {name}."); }
            i++;
            return args[i];
        }

        private static long ParsePositive(string value, string name)
        {
            if (!long.TryParse(value, out long parsed) || parsed <= 0) { throw new ArgumentException($"Valor invalido para {name}."); }
            return parsed;
        }
    }

    private sealed record DiagnosticWarning(string Code, string Message, object? Metadata = null);

    private sealed class Manifest
    {
        public string DiagnosticsVersion { get; init; } = Program.DiagnosticsVersion;
        public DateTimeOffset CreatedAt { get; init; }
        public string Hostname { get; init; } = "";
        public IReadOnlyList<ManifestFile> Files { get; init; } = Array.Empty<ManifestFile>();
        public IReadOnlyList<string> Omitted { get; init; } = Array.Empty<string>();
        public IReadOnlyList<DiagnosticWarning> Warnings { get; init; } = Array.Empty<DiagnosticWarning>();
        public bool RedactionApplied { get; init; }
    }

    private sealed record ManifestFile(string Path, long Size, string Sha256);
}
