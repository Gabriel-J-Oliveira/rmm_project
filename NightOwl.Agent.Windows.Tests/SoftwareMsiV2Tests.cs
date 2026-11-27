using System.Security.Cryptography;
using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class SoftwareMsiV2Tests
{
    private static void Check(bool value, string message)
    { if (!value) throw new InvalidOperationException(message); }

    internal static async Task RunAsync()
    {
        await InstallAndRebootEvidence();
        await UpdateUsesApprovedUpgradeProfile();
        await UninstallAbsenceEvidence();
        await FailClosedAndRecovery();
        await RejectsProfileForDifferentMachine();
        await AuthenticatedStreamingTransport();
        await ArtifactTransferUsesJobTimeout();
        MachineContextDetectionFailsClosed();
        JsonElement digestFixture = JsonSerializer.SerializeToElement(new
        {
            schema_version = 1,
            install = new { method = "msi", arguments = Array.Empty<string>(), product_code = "{11111111-2222-3333-4444-555555555555}" },
            update = (object?)null, uninstall = (object?)null,
            detection = new { method = "msi_product_code", product_code = "{11111111-2222-3333-4444-555555555555}" },
            expected_version = "4.2.1", reboot_behavior = "report_only"
        });
        Check(SoftwareInstallation.ProfileDigest(digestFixture) == "97b6e5073002d0ebd9ca1fd6ada97e61572c06f212d50adb3d6c13599591a569",
            "MSI profile digest differs from the backend's canonical JSON contract.");
        Check(SoftwareInstallation.Capabilities(new AgentConfig()).Count() == 0,
            "MSI capabilities were advertised without a local trust policy.");
        AgentConfig ready = ReadyConfig(Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N")));
        Check(SoftwareInstallation.Capabilities(ready).SequenceEqual(SoftwareInstallation.OperationCapabilities),
            "MSI capabilities are not gated by the local executor policy.");
        ready.SoftwareMsiTrustedPublisherSha256 = new() { "not-a-thumbprint" };
        Check(!SoftwareInstallation.Capabilities(ready).Any(), "Invalid publisher pin opened MSI capabilities.");
    }

    private static async Task InstallAndRebootEvidence()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-msi-v2-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            byte[] artifact = "synthetic MSI bytes; never an executable"u8.ToArray();
            AgentConfig config = ReadyConfig(root);
            Guid product = Guid.NewGuid();
            AgentJobRequest job = Job("install", product, artifact);
            FakeTransport transport = new(job, config, product, artifact);
            bool installed = false;
            int processRuns = 0;
            SoftwareInstallation executor = new(authentic: (_, _) => true,
                run: (exe, args, _) =>
                {
                    processRuns++;
                    Check(exe.EndsWith("msiexec.exe", StringComparison.OrdinalIgnoreCase), "Unexpected MSI executable.");
                    Check(args.Contains("/i \"") && args.Contains("/qn /norestart ALLUSERS=1")
                        && !args.Contains("/x "), "Install arguments were not fixed and silent.");
                    installed = true;
                    return Task.FromResult(3010);
                }, detect: _ => installed ? (true, "4.2.1") : (false, ""));
            JobExecutionResult result = await executor.ExecuteV2Async(config, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            using JsonDocument data = JsonDocument.Parse(JsonSerializer.Serialize(result.Result));
            Check(result.Status == "completed" && result.ExitCode == 3010 && processRuns == 1,
                "MSI install did not complete as reboot-pending.");
            Check(data.RootElement.GetProperty("reboot_pending").GetBoolean()
                && data.RootElement.GetProperty("evidence").GetProperty("installed").GetBoolean()
                && data.RootElement.GetProperty("evidence").GetProperty("version").GetString() == "4.2.1",
                "Install did not report live ProductCode/version evidence.");
            Check(transport.Downloads == 1 && !Directory.Exists(Path.Combine(root, "software-operation-staging", job.Id)),
                "Artifact streaming or operation staging cleanup failed.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static async Task UninstallAbsenceEvidence()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-msi-v2-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            byte[] artifact = "synthetic MSI bytes"u8.ToArray();
            AgentConfig config = ReadyConfig(root);
            Guid product = Guid.NewGuid();
            AgentJobRequest job = Job("uninstall", product, artifact);
            FakeTransport transport = new(job, config, product, artifact);
            bool present = true;
            int runs = 0;
            SoftwareInstallation executor = new(authentic: (_, _) => true,
                run: (exe, args, _) =>
                {
                    runs++;
                    Check(args == "/x " + product.ToString("B").ToUpperInvariant() + " /qn /norestart",
                        "Uninstall did not use the fixed ProductCode command.");
                    present = false;
                    return Task.FromResult(0);
                }, detect: _ => present ? (true, "4.2.1") : (false, ""));
            JobExecutionResult result = await executor.ExecuteV2Async(config, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            using JsonDocument data = JsonDocument.Parse(JsonSerializer.Serialize(result.Result));
            Check(result.Status == "completed" && runs == 1 && transport.Downloads == 0,
                "MSI uninstall unexpectedly downloaded an artifact or did not run once.");
            Check(!data.RootElement.GetProperty("evidence").GetProperty("installed").GetBoolean()
                && data.RootElement.GetProperty("evidence").GetProperty("version").GetString() == "",
                "Uninstall completion did not prove absence.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static async Task UpdateUsesApprovedUpgradeProfile()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-msi-v2-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            byte[] artifact = "synthetic MSI upgrade bytes"u8.ToArray();
            AgentConfig config = ReadyConfig(root);
            Guid product = Guid.NewGuid();
            AgentJobRequest job = Job("update", product, artifact);
            FakeTransport transport = new(job, config, product, artifact);
            bool installed = true;
            int runs = 0;
            SoftwareInstallation executor = new(authentic: (_, _) => true,
                run: (exe, args, _) =>
                {
                    runs++;
                    Check(args.Contains("/i \"") && args.Contains("ALLUSERS=1") && !args.Contains("TRANSFORMS")
                        && !args.Contains("/x "), "MSI update escaped the controlled upgrade invocation.");
                    installed = true;
                    return Task.FromResult(0);
                }, detect: _ => installed ? (true, "4.2.1") : (false, ""));
            JobExecutionResult result = await executor.ExecuteV2Async(config, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(result.Status == "completed" && runs == 1 && transport.Downloads == 1,
                "MSI update did not use the approved artifact/profile exactly once.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static async Task FailClosedAndRecovery()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-msi-v2-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            byte[] artifact = "synthetic MSI bytes"u8.ToArray();
            AgentConfig config = ReadyConfig(root);
            Guid product = Guid.NewGuid();
            AgentJobRequest job = Job("install", product, artifact);
            FakeTransport transport = new(job, config, product, artifact);
            int runs = 0;
            SoftwareInstallation badSignature = new(authentic: (_, _) => false,
                run: (_, _, _) => { runs++; return Task.FromResult(0); }, detect: _ => (false, ""));
            JobExecutionResult rejected = await badSignature.ExecuteV2Async(config, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(rejected.Status == "failed" && runs == 0 && ErrorCode(rejected) == "SOFTWARE_SIGNATURE_UNTRUSTED",
                "Untrusted MSI reached msiexec or returned an uncontrolled error.");

            AgentJobRequest hashJob = Job("install", product, artifact);
            FakeTransport wrongDigest = new(hashJob, config, product, artifact, invalidDigest: true);
            JobExecutionResult hashRejected = await badSignature.ExecuteV2Async(config, hashJob, wrongDigest,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(hashRejected.Status == "failed" && ErrorCode(hashRejected) == "SOFTWARE_ARTIFACT_HASH_INVALID" && runs == 0,
                "Artifact hash mismatch did not fail closed: status=" + hashRejected.Status + ", code=" + ErrorCode(hashRejected) + ", runs=" + runs);

            string staging = Path.Combine(root, "software-operation-staging", job.Id);
            Directory.CreateDirectory(staging);
            string marker = Path.Combine(staging, "preserve.txt");
            File.WriteAllText(marker, "pre-existing marker");
            JobExecutionResult uncertain = await badSignature.ExecuteV2Async(config, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(uncertain.Status == "failed" && ErrorCode(uncertain) == "SOFTWARE_OPERATION_RECOVERY_REQUIRES_REVIEW"
                && File.Exists(marker) && runs == 0,
                "Recovery retried a prior operation or removed a directory it did not create.");

            AgentConfig noTrust = ReadyConfig(root);
            noTrust.SoftwareMsiTrustedPublisherSha256.Clear();
            JobExecutionResult blocked = await badSignature.ExecuteV2Async(noTrust, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(blocked.Status == "failed" && ErrorCode(blocked) == "SOFTWARE_LOCAL_TRUST_POLICY_UNAVAILABLE" && runs == 0,
                "Missing local trust policy did not fail closed.");

            AgentJobRequest timeoutJob = Job("install", product, artifact);
            FakeTransport cancelling = new(timeoutJob, config, product, artifact, cancelDownload: true);
            using CancellationTokenSource timeout = new();
            timeout.Cancel();
            JobExecutionResult timedOut = await badSignature.ExecuteV2Async(config, timeoutJob, cancelling,
                DateTimeOffset.UtcNow, timeout.Token, CancellationToken.None);
            Check(timedOut.Status == "timed_out" && ErrorCode(timedOut) == "SOFTWARE_OPERATION_TIMEOUT" && runs == 0,
                "Software operation timeout did not remain a timed_out result.");

            using CancellationTokenSource shutdown = new();
            shutdown.Cancel();
            AgentJobRequest interruptedJob = Job("install", product, artifact);
            FakeTransport interruption = new(interruptedJob, config, product, artifact, cancelDownload: true);
            JobExecutionResult interrupted = await badSignature.ExecuteV2Async(config, interruptedJob, interruption,
                DateTimeOffset.UtcNow, shutdown.Token, shutdown.Token);
            Check(interrupted.Status == "interrupted" && ErrorCode(interrupted) == "SOFTWARE_OPERATION_INTERRUPTED" && runs == 0,
                "Agent shutdown was not recorded as an interruption.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static async Task AuthenticatedStreamingTransport()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-msi-http-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            byte[] artifact = Enumerable.Range(0, 256 * 1024).Select(i => (byte)(i % 251)).ToArray();
            string hash = Convert.ToHexString(SHA256.HashData(artifact)).ToLowerInvariant();
            Guid jobId = Guid.NewGuid();
            AgentConfig config = new() { AgentToken = "synthetic-bearer", ServerBaseUrl = "https://nightowl.invalid" };
            string profileJson = "{\"schema_version\":2}";
            FakeHttpFactory factory = new(request =>
            {
                Check(request.Headers.Authorization?.Scheme == "Bearer"
                    && request.Headers.Authorization.Parameter == "synthetic-bearer", "Software request omitted agent bearer auth.");
                Check(!request.RequestUri!.Query.Contains("token", StringComparison.OrdinalIgnoreCase), "Software request put credentials in the URL.");
                if (request.RequestUri.AbsolutePath.EndsWith("/profile/", StringComparison.Ordinal))
                {
                    Check(request.Method == HttpMethod.Get && request.RequestUri.AbsolutePath.Contains(jobId.ToString("D")),
                        "Profile request was not scoped to the exact job.");
                    return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(profileJson) };
                }
                Check(request.Method == HttpMethod.Get && request.RequestUri.AbsolutePath.EndsWith("/artifact/", StringComparison.Ordinal),
                    "Artifact request used an unexpected endpoint.");
                HttpResponseMessage response = new(HttpStatusCode.OK) { Content = new ByteArrayContent(artifact) };
                response.Headers.Add("X-Software-Artifact-SHA256", hash);
                response.Headers.Add("X-Software-Artifact-Revision", "4");
                return response;
            });
            ISoftwareOperationTransport transport = new AgentApiClient(factory);
            JsonElement profile = await transport.GetProfileAsync(config, jobId.ToString("D"), CancellationToken.None);
            Check(profile.GetProperty("schema_version").GetInt32() == 2, "Authenticated profile could not be parsed.");
            string target = Path.Combine(root, "download.msi");
            (long size, string digest) = await transport.DownloadArtifactAsync(config, jobId.ToString("D"), hash,
                4, artifact.LongLength, artifact.LongLength, target, CancellationToken.None);
            Check(size == artifact.LongLength && digest == hash && File.ReadAllBytes(target).SequenceEqual(artifact),
                "Private artifact was not streamed with exact size and digest.");

            byte[] tampered = artifact.ToArray();
            tampered[^1] ^= 0x5A;
            FakeHttpFactory tamperedFactory = new(request =>
            {
                HttpResponseMessage response = new(HttpStatusCode.OK) { Content = new ByteArrayContent(tampered) };
                response.Headers.Add("X-Software-Artifact-SHA256", hash);
                response.Headers.Add("X-Software-Artifact-Revision", "4");
                return response;
            });
            string tamperedTarget = Path.Combine(root, "tampered.msi");
            bool hashRejected = false;
            try
            {
                await new AgentApiClient(tamperedFactory).DownloadSoftwareArtifactAsync(config, jobId.ToString("D"),
                    hash, 4, artifact.LongLength, artifact.LongLength, tamperedTarget, CancellationToken.None);
            }
            catch (InvalidDataException error) when (error.Message == "SOFTWARE_ARTIFACT_HASH_INVALID")
            { hashRejected = true; }
            Check(hashRejected, "HTTP artifact bytes were not independently rehashed after streaming.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static async Task ArtifactTransferUsesJobTimeout()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-msi-slow-http-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            byte[] artifact = "synthetic MSI after a slow response"u8.ToArray();
            string hash = Convert.ToHexString(SHA256.HashData(artifact)).ToLowerInvariant();
            Guid jobId = Guid.NewGuid();
            AgentConfig config = new() { AgentToken = "synthetic-bearer", ServerBaseUrl = "https://nightowl.invalid" };
            var factory = new SlowHttpFactory(TimeSpan.FromMilliseconds(150), artifact, hash);
            string path = Path.Combine(root, "slow.msi");
            using CancellationTokenSource jobBudget = new(TimeSpan.FromSeconds(3));
            (long size, string digest) = await new AgentApiClient(factory).DownloadSoftwareArtifactAsync(
                config, jobId.ToString("D"), hash, 1, artifact.LongLength, artifact.LongLength, path, jobBudget.Token);
            Check(size == artifact.LongLength && digest == hash && File.ReadAllBytes(path).SequenceEqual(artifact),
                "Artifact transfer used HttpClient's short default timeout instead of the bounded job token.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static void MachineContextDetectionFailsClosed()
    {
        Check(SoftwareInstallation.TryGetMachineProductInfoCapacity(0, 5, out uint capacity) && capacity == 6,
            "A successful size query did not reserve space for the terminating NUL.");
        Check(!SoftwareInstallation.TryGetMachineProductInfoCapacity(234, 5, out _),
            "An unexpected ERROR_MORE_DATA response from a null-buffer size query was accepted.");
        Check(!SoftwareInstallation.TryGetMachineProductInfoCapacity(0, 0, out _),
            "An empty MSI version was treated as a usable size query.");
        Check(!SoftwareInstallation.TryGetMachineProductInfoCapacity(0, 121, out _),
            "An excessive MSI version size was accepted.");
        Check(SoftwareInstallation.InterpretMachineProductInfo(0, "4.2.1") == (true, "4.2.1"),
            "Machine-context MSI version was not recognized as installed.");
        Check(SoftwareInstallation.InterpretMachineProductInfo(1605, "") == (false, ""),
            "A product absent from machine context was not recognized as absent.");
        Check(SoftwareInstallation.InterpretMachineProductInfo(5, "") == (null, ""),
            "An indeterminate Windows Installer error was mistaken for absence.");
        Check(SoftwareInstallation.InterpretMachineProductInfo(0, "") == (null, ""),
            "An installed product with missing version evidence was accepted.");
    }

    private static async Task RejectsProfileForDifferentMachine()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-msi-identity-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            byte[] artifact = "synthetic MSI bytes"u8.ToArray();
            AgentConfig config = ReadyConfig(root);
            Guid product = Guid.NewGuid();
            AgentJobRequest job = Job("install", product, artifact);
            FakeTransport transport = new(job, config, product, artifact, wrongMachine: true);
            int runs = 0;
            SoftwareInstallation executor = new(authentic: (_, _) => true,
                run: (_, _, _) => { runs++; return Task.FromResult(0); }, detect: _ => (false, ""));
            JobExecutionResult result = await executor.ExecuteV2Async(config, job, transport,
                DateTimeOffset.UtcNow, CancellationToken.None, CancellationToken.None);
            Check(result.Status == "failed" && ErrorCode(result) == "SOFTWARE_PROFILE_SNAPSHOT_MISMATCH"
                && transport.Downloads == 0 && runs == 0,
                "A profile for another machine reached download or msiexec.");
        }
        finally { Directory.Delete(root, true); }
    }

    private static string ErrorCode(JobExecutionResult result)
    {
        using JsonDocument data = JsonDocument.Parse(JsonSerializer.Serialize(result.Result));
        return data.RootElement.GetProperty("error_code").GetString() ?? "";
    }

    private static AgentConfig ReadyConfig(string root)
    {
        Directory.CreateDirectory(root);
        AgentConfig config = new() { MachineId = Guid.NewGuid().ToString("D"), StatePath = Path.Combine(root, "state.json"),
            SoftwareMsiOperationsEnabled = true, SoftwareMsiTrustedPublisherSha256 = new() { new string('A', 64) } };
        config.AllowedJobTypes.Add("install_software");
        return config;
    }

    private static AgentJobRequest Job(string operation, Guid product, byte[] artifact)
    {
        string hash = Convert.ToHexString(SHA256.HashData(artifact)).ToLowerInvariant();
        Guid package = Guid.NewGuid();
        Guid request = Guid.NewGuid();
        Guid operationId = Guid.NewGuid();
        object profile = ProfileBody(operation, product);
        string profileHash = SoftwareInstallation.ProfileDigest(JsonSerializer.SerializeToElement(profile));
        return new AgentJobRequest { Id = Guid.NewGuid().ToString("D"), Type = "install_software", TimeoutSeconds = 1800,
            Payload = new Dictionary<string, object?> { ["schema_version"] = 2, ["operation_id"] = operationId.ToString("D"),
                ["operation"] = operation, ["request_id"] = request.ToString("D"), ["package_uuid"] = package.ToString("D"),
                ["artifact_sha256"] = hash, ["artifact_revision"] = 3, ["profile_revision"] = 7,
                ["profile_sha256"] = profileHash } };
    }

    private static object ProfileBody(string operation, Guid product)
    {
        string productCode = product.ToString("B").ToUpperInvariant();
        object? update = operation == "update" ? new { method = "msi_upgrade", arguments = Array.Empty<string>() } : null;
        object? uninstall = operation == "uninstall" ? new { method = "msi_product_code", product_code = productCode } : null;
        return new { schema_version = 1,
            install = new { method = "msi", arguments = Array.Empty<string>(), product_code = productCode },
            update, uninstall, detection = new { method = "msi_product_code", product_code = productCode },
            expected_version = "4.2.1", reboot_behavior = "report_only" };
    }

    private sealed class FakeTransport(AgentJobRequest job, AgentConfig config, Guid product, byte[] artifact,
        bool invalidDigest = false, bool cancelDownload = false, bool wrongMachine = false) : ISoftwareOperationTransport
    {
        internal int Downloads { get; private set; }
        public Task<JsonElement> GetProfileAsync(AgentConfig _, string jobId, CancellationToken ct)
        {
            Check(jobId == job.Id, "Profile request used a different job identity.");
            string operation = (string)job.Payload["operation"]!;
            string productCode = product.ToString("B").ToUpperInvariant();
            object profileBody = ProfileBody(operation, product);
            var body = new
            {
                schema_version = 2, operation_id = job.Payload["operation_id"], operation,
                request_id = job.Payload["request_id"], package_uuid = job.Payload["package_uuid"],
                artifact_sha256 = job.Payload["artifact_sha256"], artifact_revision = 3, profile_revision = 7,
                profile_sha256 = job.Payload["profile_sha256"], size_bytes = artifact.LongLength, product_code = productCode,
                expected_version = "4.2.1", machine_id = wrongMachine ? Guid.NewGuid().ToString("D") : config.MachineId,
                profile = profileBody
            };
            return Task.FromResult(JsonSerializer.SerializeToElement(body));
        }
        public async Task<(long Size, string Sha256)> DownloadArtifactAsync(AgentConfig _, string jobId, string sha256,
            int revision, long expectedSize, long maximumBytes, string destination, CancellationToken ct)
        {
            Check(jobId == job.Id && revision == 3 && expectedSize == artifact.LongLength && expectedSize <= maximumBytes,
                "Artifact request lost its job-bound snapshot.");
            Downloads++;
            if (cancelDownload) throw new OperationCanceledException(ct);
            await File.WriteAllBytesAsync(destination, artifact, ct);
            return (artifact.LongLength, invalidDigest ? new string('0', 64) : Convert.ToHexString(SHA256.HashData(artifact)).ToLowerInvariant());
        }
    }

    private sealed class FakeHttpFactory(Func<HttpRequestMessage, HttpResponseMessage> respond) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => new(new FakeHandler(respond), disposeHandler: true);
    }
    private sealed class FakeHandler(Func<HttpRequestMessage, HttpResponseMessage> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(respond(request));
    }

    private sealed class SlowHttpFactory(TimeSpan delay, byte[] bytes, string hash) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => new(new SlowHandler(delay, bytes, hash))
        { Timeout = TimeSpan.FromMilliseconds(25) };
    }

    private sealed class SlowHandler(TimeSpan delay, byte[] bytes, string hash) : HttpMessageHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            await Task.Delay(delay, cancellationToken);
            HttpResponseMessage response = new(HttpStatusCode.OK) { Content = new ByteArrayContent(bytes) };
            response.Headers.Add("X-Software-Artifact-SHA256", hash);
            response.Headers.Add("X-Software-Artifact-Revision", "1");
            return response;
        }
    }
}
