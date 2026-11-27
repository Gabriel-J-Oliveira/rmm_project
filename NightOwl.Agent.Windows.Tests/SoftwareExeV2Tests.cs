using System.Security.Cryptography;
using System.Text.Json;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;

internal static class SoftwareExeV2Tests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    internal static async Task RunAsync()
    {
        Check(SoftwareInstallation.ExeCapabilities(new AgentConfig()).Count() == 0,
            "EXE capabilities were announced while local policy is disabled.");
        AgentConfig capabilityConfig = new()
        {
            SoftwareExeOperationsEnabled = true,
            SoftwareMsiTrustedPublisherSha256 = new() { new string('A', 64) },
        };
        capabilityConfig.AllowedJobTypes.Add("install_software");
        Check(SoftwareInstallation.ExeCapabilities(capabilityConfig).SequenceEqual(SoftwareInstallation.ExeOperationCapabilities)
            && !SoftwareInstallation.Capabilities(capabilityConfig).Any(),
            "EXE capability gate is not independent from MSI or did not require its local policy.");
        AgentConfig missingPublisherPin = new() { SoftwareExeOperationsEnabled = true };
        missingPublisherPin.AllowedJobTypes.Add("install_software");
        Check(!SoftwareInstallation.ExeCapabilities(missingPublisherPin).Any(),
            "EXE capability was announced without a local publisher trust pin.");

        await ExecutesEachTypedOperationAndRebootIsPending();
        await RejectsUntrustedPublisherAndArtifactHashMismatch();
        await ExistingExpectedVersionIsNotReexecuted();
        await UncertainResultBlocksASecondRequest();
        await ExplicitUninstallerIsCopiedAndPublisherChecked();
        await UninstallAlreadyAbsentIsIdempotent();
        ArgumentQuotingAndCanonicalUnicodeDigest();
    }

    private static async Task RejectsUntrustedPublisherAndArtifactHashMismatch()
    {
        foreach (bool corruptHash in new[] { false, true })
        {
            string root = NewRoot();
            try
            {
                byte[] artifact = "synthetic EXE bytes; never executed"u8.ToArray();
                AgentConfig config = ReadyConfig(root);
                AgentJobRequest job = Job("install", Guid.NewGuid(), artifact, "4.2.0", out JsonElement profile);
                ExeTransport transport = new(job, config, artifact, profile, corruptHash);
                int runs = 0;
                SoftwareInstallation executor = new(authentic: (_, _) => corruptHash,
                    detectExe: _ => new(false, "", "absent", RegistryIdentity),
                    runExe: (_, _, _) => { runs++; return Task.FromResult(0); });
                JobExecutionResult result = await executor.ExecuteV2Async(config, job, transport,
                    DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
                string expected = corruptHash ? "SOFTWARE_ARTIFACT_HASH_INVALID" : "SOFTWARE_SIGNATURE_UNTRUSTED";
                Check(result.Status == "failed" && ErrorCode(result) == expected && runs == 0,
                    $"EXE {expected} did not fail closed before execution.");
            }
            finally { Directory.Delete(root, recursive: true); }
        }
    }

    private static async Task ExecutesEachTypedOperationAndRebootIsPending()
    {
        foreach (string operation in new[] { "install", "update", "uninstall" })
        {
            string root = NewRoot();
            try
            {
                byte[] artifact = "synthetic EXE bytes; never executed"u8.ToArray();
                AgentConfig config = ReadyConfig(root);
                Guid package = Guid.NewGuid();
                AgentJobRequest job = Job(operation, package, artifact,
                    expectedVersion: operation == "uninstall" ? "" : "4.2.0", out JsonElement profile);
                ExeTransport transport = new(job, config, artifact, profile);
                int processRuns = 0;
                string version = operation == "install" ? "" : "3.1.0";
                string configuredUninstaller = Path.Combine(root, "vendor-uninstaller.exe");
                await File.WriteAllTextAsync(configuredUninstaller, "synthetic signed fixture");
                ExeDetectionEvidence Detect(JsonElement _) => string.IsNullOrEmpty(version)
                    ? new(false, "", "absent", RegistryIdentity)
                    : new(true, version, "present", RegistryIdentity);
                SoftwareInstallation executor = new(authentic: (_, _) => true, detectExe: Detect,
                    resolveConfiguredExecutable: _ => configuredUninstaller,
                    runExe: (_, arguments, _) =>
                    {
                        processRuns++;
                        string[] expectedArguments = operation switch
                        {
                            "install" => new[] { "/vendor switch", "argument with spaces" },
                            "update" => new[] { "/upgrade" },
                            _ => new[] { "/remove", "product" },
                        };
                        Check(arguments.SequenceEqual(expectedArguments),
                            "EXE arguments were not preserved as structured arguments.");
                        if (operation != "uninstall") version = "4.2.0";
                        else version = "";
                        return Task.FromResult(operation == "install" ? 3010 : 0);
                    });
                JobExecutionResult result = await executor.ExecuteV2Async(config, job, transport,
                    DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
                using JsonDocument output = JsonDocument.Parse(JsonSerializer.Serialize(result.Result));
                Check(result.Status == "completed" && processRuns == 1,
                    $"EXE {operation} was not verified after one controlled execution: status={result.Status}, error={ErrorCode(result)}, result={output.RootElement}, profile={profile.GetRawText()}");
                Check(output.RootElement.GetProperty("verified").GetBoolean(), "EXE completion omitted verified evidence.");
                Check(output.RootElement.GetProperty("reboot_pending").GetBoolean() == (operation == "install"),
                    "Configured reboot-pending exit code was not reported without restarting.");
                Check(transport.Downloads == (operation == "uninstall" ? 0 : 1),
                    "Only install/update should download the approved package.");
                Check(!Directory.Exists(Path.Combine(root, "software-operation-staging", job.Id)),
                    "Verified EXE operation did not clean its private staging directory.");
            }
            finally { Directory.Delete(root, recursive: true); }
        }
    }

    private static async Task ExistingExpectedVersionIsNotReexecuted()
    {
        string root = NewRoot();
        try
        {
            byte[] artifact = "synthetic EXE bytes"u8.ToArray();
            AgentConfig config = ReadyConfig(root);
            AgentJobRequest job = Job("update", Guid.NewGuid(), artifact, expectedVersion: "4.2.0", out JsonElement profile);
            ExeTransport transport = new(job, config, artifact, profile);
            int runs = 0;
            SoftwareInstallation executor = new(authentic: (_, _) => true,
                detectExe: _ => new(true, "4.2.0", "present", RegistryIdentity),
                runExe: (_, _, _) => { runs++; return Task.FromResult(0); });
            JobExecutionResult result = await executor.ExecuteV2Async(config, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(result.Status == "completed" && runs == 0 && transport.Downloads == 0,
                "An update already at the expected version was executed again.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    private static async Task UncertainResultBlocksASecondRequest()
    {
        string root = NewRoot();
        try
        {
            byte[] artifact = "synthetic EXE bytes"u8.ToArray();
            AgentConfig config = ReadyConfig(root);
            Guid package = Guid.NewGuid();
            AgentJobRequest firstJob = Job("update", package, artifact, expectedVersion: "4.2.0", out JsonElement firstProfile);
            ExeTransport firstTransport = new(firstJob, config, artifact, firstProfile);
            int runs = 0;
            string currentVersion = "3.1.0";
            SoftwareInstallation executor = new(authentic: (_, _) => true,
                detectExe: _ => new(true, currentVersion, "present", RegistryIdentity),
                runExe: (_, _, _) => { runs++; return Task.FromResult(0); });
            JobExecutionResult uncertain = await executor.ExecuteV2Async(config, firstJob, firstTransport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(uncertain.Status == "failed" && ErrorCode(uncertain) == "SOFTWARE_INSTALLED_STATE_UNCONFIRMED"
                && runs == 1, "An unverified update did not retain its recovery journal.");

            AgentJobRequest replayJob = Job("update", package, artifact, expectedVersion: "4.2.0", out JsonElement replayProfile);
            ExeTransport replayTransport = new(replayJob, config, artifact, replayProfile);
            JobExecutionResult blocked = await executor.ExecuteV2Async(config, replayJob, replayTransport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(blocked.Status == "failed" && ErrorCode(blocked) == "SOFTWARE_OPERATION_RECOVERY_REQUIRES_REVIEW"
                && runs == 1 && replayTransport.Downloads == 0,
                "A new request repeated an operation with uncertain outcome.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    private static async Task ExplicitUninstallerIsCopiedAndPublisherChecked()
    {
        string root = NewRoot();
        try
        {
            string configured = Path.Combine(root, "vendor-uninstaller.exe");
            await File.WriteAllTextAsync(configured, "synthetic signed fixture");
            byte[] artifact = "synthetic EXE bytes"u8.ToArray();
            AgentConfig config = ReadyConfig(root);
            AgentJobRequest job = Job("uninstall", Guid.NewGuid(), artifact, expectedVersion: "", out JsonElement profile);
            ExeTransport transport = new(job, config, artifact, profile);
            string? executed = null;
            bool verifiedStagedCopy = false;
            int detects = 0;
            SoftwareInstallation executor = new(authentic: (path, _) =>
                {
                    if (path.EndsWith("approved-uninstaller.exe", StringComparison.OrdinalIgnoreCase))
                        verifiedStagedCopy = File.Exists(path);
                    return true;
                },
                resolveConfiguredExecutable: _ => configured,
                detectExe: _ => ++detects == 1
                    ? new(true, "3.1.0", "present", RegistryIdentity)
                    : new(false, "", "absent", RegistryIdentity),
                runExe: (path, arguments, _) =>
                {
                    executed = path;
                    Check(arguments.SequenceEqual(new[] { "/remove", "product" }),
                        "Uninstall arguments were not passed as a typed array.");
                    return Task.FromResult(0);
                });
            JobExecutionResult result = await executor.ExecuteV2Async(config, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(result.Status == "completed" && executed is not null && executed != configured
                && verifiedStagedCopy && transport.Downloads == 0,
                "The explicitly configured signed uninstaller was not copied to protected staging first.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    private static async Task UninstallAlreadyAbsentIsIdempotent()
    {
        string root = NewRoot();
        try
        {
            AgentConfig config = ReadyConfig(root);
            AgentJobRequest job = Job("uninstall", Guid.NewGuid(), "synthetic"u8.ToArray(), expectedVersion: "", out JsonElement profile);
            int runs = 0;
            SoftwareInstallation executor = new(detectExe: _ => new(false, "", "absent", RegistryIdentity),
                runExe: (_, _, _) => { runs++; return Task.FromResult(0); });
            JobExecutionResult result = await executor.ExecuteV2Async(config, job,
                new ExeTransport(job, config, "synthetic"u8.ToArray(), profile), DateTimeOffset.UtcNow,
                CancellationToken.None, CancellationToken.None);
            Check(result.Status == "completed" && runs == 0, "Already absent EXE was not treated idempotently.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    private static void ArgumentQuotingAndCanonicalUnicodeDigest()
    {
        Check(OwnedProcess.JoinWindowsArguments(new[] { "plain", "arg with spaces", "a\\\"b" })
                == "plain \"arg with spaces\" \"a\\\\\\\"b\"",
            "Windows argument quoting changed structured EXE argument boundaries.");
        string json = "{\"schema_version\":2,\"install\":{\"method\":\"exe\",\"arguments\":[\"éditeur\",\"🦉\"],\"timeout_seconds\":120,\"accepted_exit_codes\":[0],\"reboot_exit_codes\":[]},\"update\":null,\"uninstall\":null,\"detection\":{\"method\":\"registry_value\",\"hive\":\"HKLM\",\"view\":\"registry64\",\"key\":\"SOFTWARE\\\\Vendor\\\\Product\",\"value_name\":\"DisplayVersion\"},\"expected_version\":\"1.0\",\"reboot_behavior\":\"report_only\"}";
        using JsonDocument profile = JsonDocument.Parse(json);
        Check(SoftwareInstallation.ProfileDigest(profile.RootElement) == "80848116ff260a28689cc69ac9d85cb5fb40e80ff5dbb4f6b3923feba8a560af",
            "Unicode EXE profile digest differs from Python's ensure_ascii canonical JSON.");
    }

    private const string RegistryIdentity = "HKLM/registry64/SOFTWARE\\Vendor\\Product/DisplayVersion";

    private static string NewRoot()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-exe-v2-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }

    private static AgentConfig ReadyConfig(string root)
    {
        AgentConfig config = new()
        {
            MachineId = Guid.NewGuid().ToString("D"), StatePath = Path.Combine(root, "state.json"),
            SoftwareExeOperationsEnabled = true,
            SoftwareMsiTrustedPublisherSha256 = new() { new string('A', 64) },
        };
        config.AllowedJobTypes.Add("install_software");
        return config;
    }

    private static AgentJobRequest Job(string operation, Guid package, byte[] artifact, string expectedVersion,
        out JsonElement profileElement)
    {
        Dictionary<string, object?> profile = new()
        {
            ["schema_version"] = 2,
            ["install"] = operation == "install" ? Operation("exe", new[] { "/vendor switch", "argument with spaces" }, 1800, new[] { 0, 3010 }, new[] { 3010 }) : null,
            ["update"] = operation == "update" ? Operation("vendor_exe", new[] { "/upgrade" }, 120, new[] { 0 }, Array.Empty<int>()) : null,
            ["uninstall"] = operation == "uninstall" ? Operation("vendor_exe", new[] { "/remove", "product" }, 120, new[] { 0 }, Array.Empty<int>(),
                @"%ProgramFiles%\Vendor\App\uninstall.exe") : null,
            ["detection"] = operation == "uninstall"
                ? new { method = "registry_value", hive = "HKLM", view = "registry64", key = @"SOFTWARE\Vendor\Product", value_name = "DisplayVersion" }
                : new { method = "registry_value", hive = "HKLM", view = "registry64", key = @"SOFTWARE\Vendor\Product", value_name = "DisplayVersion" },
            ["expected_version"] = expectedVersion,
            ["reboot_behavior"] = "defer_to_policy",
        };
        profileElement = JsonSerializer.SerializeToElement(profile);
        string profileHash = SoftwareInstallation.ProfileDigest(profileElement);
        string artifactHash = Convert.ToHexString(SHA256.HashData(artifact)).ToLowerInvariant();
        Guid operationId = Guid.NewGuid(), requestId = Guid.NewGuid();
        int timeout = operation == "install" ? 1800 : 120;
        return new AgentJobRequest
        {
            Id = Guid.NewGuid().ToString("D"), Type = "install_software", TimeoutSeconds = timeout,
            Payload = new Dictionary<string, object?>
            {
                ["schema_version"] = 2, ["operation_id"] = operationId.ToString("D"),
                ["operation"] = operation, ["request_id"] = requestId.ToString("D"),
                ["package_uuid"] = package.ToString("D"), ["artifact_sha256"] = artifactHash,
                ["artifact_revision"] = 3, ["profile_revision"] = 7, ["profile_sha256"] = profileHash,
            },
        };
    }

    private static object Operation(string method, string[] arguments, int timeout, int[] accepted, int[] reboot, string? executablePath = null)
    {
        Dictionary<string, object?> result = new()
        {
            ["method"] = method, ["arguments"] = arguments, ["timeout_seconds"] = timeout,
            ["accepted_exit_codes"] = accepted, ["reboot_exit_codes"] = reboot,
        };
        if (executablePath is not null) result["executable_path"] = executablePath;
        return result;
    }

    private static string ErrorCode(JobExecutionResult result)
    {
        using JsonDocument output = JsonDocument.Parse(JsonSerializer.Serialize(result.Result));
        JsonElement root = output.RootElement;
        if (root.TryGetProperty("error_code", out JsonElement errorCode)) return errorCode.GetString() ?? "";
        if (root.TryGetProperty("output", out JsonElement nested)
            && nested.TryGetProperty("error_code", out errorCode)) return errorCode.GetString() ?? "";
        return "<none>";
    }

    private sealed class ExeTransport(AgentJobRequest job, AgentConfig config, byte[] artifact,
        JsonElement approvedProfile, bool corruptHash = false) : ISoftwareOperationTransport
    {
        internal int Downloads { get; private set; }
        public Task<JsonElement> GetProfileAsync(AgentConfig _, string jobId, CancellationToken ct)
        {
            Check(jobId == job.Id, "EXE profile used another job id.");
            string operation = (string)job.Payload["operation"]!;
            string expected = operation == "uninstall" ? "" : "4.2.0";
            return Task.FromResult(JsonSerializer.SerializeToElement(new
            {
                schema_version = 2, operation_id = job.Payload["operation_id"], operation,
                request_id = job.Payload["request_id"], package_uuid = job.Payload["package_uuid"], package_type = "exe",
                artifact_sha256 = job.Payload["artifact_sha256"], artifact_revision = 3, profile_revision = 7,
                profile_sha256 = job.Payload["profile_sha256"], size_bytes = artifact.LongLength, product_code = "",
                expected_version = expected, machine_id = config.MachineId, profile = approvedProfile,
            }));
        }
        public async Task<(long Size, string Sha256)> DownloadArtifactAsync(AgentConfig _, string jobId, string sha256,
            int revision, long expectedSize, long maximumBytes, string destination, CancellationToken ct)
        {
            Check(jobId == job.Id && revision == 3 && expectedSize == artifact.LongLength && expectedSize <= maximumBytes,
                "EXE artifact download lost its job-bound limits.");
            Downloads++;
            await File.WriteAllBytesAsync(destination, artifact, ct);
            string digest = Convert.ToHexString(SHA256.HashData(artifact)).ToLowerInvariant();
            return (artifact.LongLength, corruptHash ? new string('0', 64) : digest);
        }
    }
}
