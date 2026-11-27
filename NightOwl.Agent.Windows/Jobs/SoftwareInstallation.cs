using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Diagnostics;
using Microsoft.Win32;
using NightOwl.Agent.Windows.Services;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Jobs;

public interface ISoftwareOperationTransport
{
    Task<JsonElement> GetProfileAsync(AgentConfig config, string jobId, CancellationToken ct);
    Task<(long Size, string Sha256)> DownloadArtifactAsync(AgentConfig config, string jobId, string sha256,
        int revision, long expectedSize, long maximumBytes, string destination, CancellationToken ct);
}

internal sealed record SoftwareOperationEnvelope(
    string OperationId, string Operation, string RequestId, string PackageUuid,
    string ArtifactSha256, int ArtifactRevision, int ProfileRevision, string ProfileSha256)
{
    internal static SoftwareOperationEnvelope Parse(AgentJobRequest job)
    {
        Dictionary<string, object?> payload = job.Payload;
        string[] fields = { "schema_version", "operation_id", "operation", "request_id", "package_uuid",
            "artifact_sha256", "artifact_revision", "profile_revision", "profile_sha256" };
        if (payload.Count != fields.Length || fields.Any(field => !payload.ContainsKey(field))
            || Int(payload, "schema_version") != 2)
            throw new InvalidDataException("SOFTWARE_OPERATION_ENVELOPE_INVALID");
        string operation = Text(payload, "operation");
        string operationId = CanonicalGuid(Text(payload, "operation_id"));
        string requestId = CanonicalGuid(Text(payload, "request_id"));
        string packageId = CanonicalGuid(Text(payload, "package_uuid"));
        string artifactHash = Text(payload, "artifact_sha256");
        string profileHash = Text(payload, "profile_sha256");
        int artifactRevision = Int(payload, "artifact_revision");
        int profileRevision = Int(payload, "profile_revision");
        if (operation is not ("install" or "update" or "uninstall")
            || !Regex.IsMatch(artifactHash, "^[0-9a-f]{64}$") || !Regex.IsMatch(profileHash, "^[0-9a-f]{64}$")
            || artifactRevision < 1 || profileRevision < 1)
            throw new InvalidDataException("SOFTWARE_OPERATION_ENVELOPE_INVALID");
        return new(operationId, operation, requestId, packageId, artifactHash, artifactRevision, profileRevision, profileHash);
    }
    private static string Text(Dictionary<string, object?> data, string key) =>
        data.TryGetValue(key, out object? value) ? JsonSerializer.SerializeToElement(value).GetString() ?? "" : "";
    private static int Int(Dictionary<string, object?> data, string key) =>
        data.TryGetValue(key, out object? value) && JsonSerializer.SerializeToElement(value).TryGetInt32(out int number) ? number : -1;
    private static string CanonicalGuid(string value) => Guid.TryParse(value, out Guid parsed) && parsed.ToString("D") == value
        ? value : throw new InvalidDataException("SOFTWARE_OPERATION_IDENTITY_INVALID");
}

internal sealed record SoftwareOperationProfile(int SchemaVersion, string OperationId, string Operation,
    string RequestId, string PackageUuid, string PackageType, string ArtifactSha256, int ArtifactRevision, int ProfileRevision,
    string ProfileSha256, long SizeBytes, string ProductCode, string ExpectedVersion, JsonElement Profile, string MachineId)
{
    internal static SoftwareOperationProfile Parse(JsonElement root, SoftwareOperationEnvelope expected, string machineId)
    {
        try
        {
            SoftwareOperationProfile value = new(
                root.GetProperty("schema_version").GetInt32(), root.GetProperty("operation_id").GetString()!,
                root.GetProperty("operation").GetString()!, root.GetProperty("request_id").GetString()!,
                root.GetProperty("package_uuid").GetString()!,
                root.TryGetProperty("package_type", out JsonElement packageType) ? packageType.GetString() ?? "" : "msi",
                root.GetProperty("artifact_sha256").GetString()!,
                root.GetProperty("artifact_revision").GetInt32(), root.GetProperty("profile_revision").GetInt32(),
                root.GetProperty("profile_sha256").GetString()!, root.GetProperty("size_bytes").GetInt64(),
                root.TryGetProperty("product_code", out JsonElement productCode) ? productCode.GetString() ?? "" : "",
                root.TryGetProperty("expected_version", out JsonElement expectedVersion) ? expectedVersion.GetString() ?? "" : "",
                root.GetProperty("profile").Clone(), root.GetProperty("machine_id").GetString()!);
            if (value.OperationId != expected.OperationId || value.Operation != expected.Operation
                || value.RequestId != expected.RequestId || value.PackageUuid != expected.PackageUuid
                || !value.ArtifactSha256.Equals(expected.ArtifactSha256, StringComparison.Ordinal)
                || value.ArtifactRevision != expected.ArtifactRevision || value.ProfileRevision != expected.ProfileRevision
                || !value.ProfileSha256.Equals(expected.ProfileSha256, StringComparison.Ordinal)
                || !Guid.TryParse(value.MachineId, out Guid actual) || !actual.ToString("D").Equals(machineId, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("SOFTWARE_PROFILE_SNAPSHOT_MISMATCH");
            return value;
        }
        catch (Exception error) when (error is KeyNotFoundException or InvalidOperationException or FormatException or NullReferenceException)
        { throw new InvalidDataException("SOFTWARE_PROFILE_INVALID"); }
    }
}

internal sealed record ApprovedSoftware(string Id,string Url,string Sha256,long Size,string ProductCode,string Version,string SignerSha256);
internal sealed record ExeDetectionEvidence(bool? Installed, string Version, string State, string Identity);

internal sealed class SoftwareInstallation
{
    private readonly Func<ApprovedSoftware,CancellationToken,Task<byte[]>> _download;
    private readonly Func<string,string,bool> _authentic;
    private readonly Func<string,string,bool> _installed;
    private readonly Func<string,string,CancellationToken,Task<int>> _run;
    private readonly Func<string,(bool? Installed,string Version)> _detect;
    private readonly Func<JsonElement,ExeDetectionEvidence> _detectExe;
    private readonly Func<string,IReadOnlyList<string>,CancellationToken,Task<int>> _runExe;
    private readonly Func<string,string> _resolveConfiguredExecutable;
    internal SoftwareInstallation(Func<ApprovedSoftware,CancellationToken,Task<byte[]>>? download=null,
        Func<string,string,bool>? authentic=null,Func<string,string,bool>? installed=null,
        Func<string,string,CancellationToken,Task<int>>? run=null,
        Func<string,(bool? Installed,string Version)>? detect=null,
        Func<JsonElement,ExeDetectionEvidence>? detectExe=null,
        Func<string,IReadOnlyList<string>,CancellationToken,Task<int>>? runExe=null,
        Func<string,string>? resolveConfiguredExecutable=null)
    {
        _download=download ?? Download; _authentic=authentic ?? Authenticode;
        _installed=installed ?? Installed; _run=run ?? OwnedProcess.RunAsync; _detect=detect ?? DetectInstalled;
        _detectExe=detectExe ?? DetectExeInstalled; _runExe=runExe ?? OwnedProcess.RunAsync;
        _resolveConfiguredExecutable=resolveConfiguredExecutable ?? ResolveConfiguredExecutable;
    }

    internal const long MaximumArtifactBytes = 1024L * 1024 * 1024;
    internal static readonly string[] OperationCapabilities = { "software_msi_install:1", "software_msi_update:1", "software_msi_uninstall:1" };
    internal static readonly string[] ExeOperationCapabilities = { "software_exe_install:1", "software_exe_update:1", "software_exe_uninstall:1" };

    internal static bool IsLocalPolicyReady(AgentConfig config) => config.SoftwareMsiOperationsEnabled
        && config.SoftwareMsiMaxArtifactBytes is > 0 and <= MaximumArtifactBytes
        && config.SoftwareMsiTrustedPublisherSha256 is { Count: > 0 }
        && config.SoftwareMsiTrustedPublisherSha256.All(pin => Regex.IsMatch(pin ?? "", "^[0-9a-fA-F]{64}$"));

    internal static IEnumerable<string> Capabilities(AgentConfig config) =>
        IsLocalPolicyReady(config) && config.AllowedJobTypes.Contains("install_software", StringComparer.OrdinalIgnoreCase)
            ? OperationCapabilities : Array.Empty<string>();

    internal static bool IsExeLocalPolicyReady(AgentConfig config) => config.SoftwareExeOperationsEnabled
        && config.SoftwareMsiMaxArtifactBytes is > 0 and <= MaximumArtifactBytes
        && config.SoftwareMsiTrustedPublisherSha256 is { Count: > 0 }
        && config.SoftwareMsiTrustedPublisherSha256.All(pin => Regex.IsMatch(pin ?? "", "^[0-9a-fA-F]{64}$"));

    internal static IEnumerable<string> ExeCapabilities(AgentConfig config) =>
        IsExeLocalPolicyReady(config) && config.AllowedJobTypes.Contains("install_software", StringComparer.OrdinalIgnoreCase)
            ? ExeOperationCapabilities : Array.Empty<string>();

    internal async Task<JobExecutionResult> ExecuteV2Async(AgentConfig config, AgentJobRequest job,
        ISoftwareOperationTransport transport, DateTimeOffset started, CancellationToken ct, CancellationToken hostCt)
    {
        Stopwatch watch = Stopwatch.StartNew();
        string phase = "profile";
        int exitCode = 1;
        SoftwareOperationEnvelope? envelope = null;
        string? ownedOperationDirectory = null;
        try
        {
            if (!IsLocalPolicyReady(config) && !IsExeLocalPolicyReady(config))
                throw new SoftwareOperationException("SOFTWARE_LOCAL_TRUST_POLICY_UNAVAILABLE");
            if (!Guid.TryParse(job.Id, out Guid jobId) || !Guid.TryParse(config.MachineId, out Guid machineId))
                throw new SoftwareOperationException("SOFTWARE_OPERATION_IDENTITY_INVALID");
            envelope = SoftwareOperationEnvelope.Parse(job);
            JsonElement profileJson = await transport.GetProfileAsync(config, job.Id, ct);
            SoftwareOperationProfile profile;
            try { profile = SoftwareOperationProfile.Parse(profileJson, envelope, machineId.ToString("D")); }
            catch (InvalidDataException error)
            {
                throw new SoftwareOperationException(error.Message == "SOFTWARE_PROFILE_SNAPSHOT_MISMATCH"
                    ? error.Message : "SOFTWARE_PROFILE_INVALID");
            }
            if (profile.PackageType == "exe")
            {
                return await ExecuteExeV2Async(config, job, transport, started, ct, hostCt, watch, envelope, profile);
            }
            if (!IsLocalPolicyReady(config) || profile.PackageType != "msi")
                throw new SoftwareOperationException("SOFTWARE_LOCAL_TRUST_POLICY_UNAVAILABLE");
            ValidateProfile(profile, envelope);
            string productCode = Guid.Parse(profile.ProductCode).ToString("B").ToUpperInvariant();

            string stateRoot = Path.GetFullPath(Path.GetDirectoryName(config.StatePath) ?? throw new SoftwareOperationException("SOFTWARE_STAGING_UNAVAILABLE"));
            EnsureNoReparsePath(stateRoot);
            string stagingRoot = Path.Combine(stateRoot, "software-operation-staging");
            Directory.CreateDirectory(stagingRoot);
            EnsureNoReparsePath(stagingRoot);
            string operationDirectory = Path.Combine(stagingRoot, jobId.ToString("D"));
            if (Directory.Exists(operationDirectory) || File.Exists(operationDirectory))
                throw new SoftwareOperationException("SOFTWARE_OPERATION_RECOVERY_REQUIRES_REVIEW");
            Directory.CreateDirectory(operationDirectory);
            ownedOperationDirectory = operationDirectory;
            EnsureNoReparsePath(operationDirectory);
            string journalPath = Path.Combine(operationDirectory, "operation.json");
            WriteJournal(journalPath, job.Id, envelope, "profile_validated");
            string msi = Path.Combine(operationDirectory, "artifact.msi");
            if (envelope.Operation is "install" or "update")
            {
                phase = "download";
                WriteJournal(journalPath, job.Id, envelope, "downloading");
                (long Size, string Sha256) downloaded = await transport.DownloadArtifactAsync(config, job.Id,
                    envelope.ArtifactSha256, envelope.ArtifactRevision, profile.SizeBytes,
                    Math.Min(config.SoftwareMsiMaxArtifactBytes, MaximumArtifactBytes), msi, ct);
                if (downloaded.Size != profile.SizeBytes || !downloaded.Sha256.Equals(envelope.ArtifactSha256, StringComparison.OrdinalIgnoreCase))
                    throw new SoftwareOperationException("SOFTWARE_ARTIFACT_HASH_INVALID");
                phase = "signature";
                bool trusted = config.SoftwareMsiTrustedPublisherSha256.Any(pin => _authentic(msi, pin.ToUpperInvariant()));
                if (!trusted) throw new SoftwareOperationException("SOFTWARE_SIGNATURE_UNTRUSTED");
                WriteJournal(journalPath, job.Id, envelope, "artifact_verified");
            }

            phase = "precondition";
            (bool? Installed, string Version) before = _detect(productCode);
            if (before.Installed is null) throw new SoftwareOperationException("SOFTWARE_DETECTION_UNAVAILABLE");
            if (envelope.Operation == "install" && before.Installed == true)
                throw new SoftwareOperationException("SOFTWARE_ALREADY_INSTALLED");
            if (envelope.Operation == "update" && before.Installed != true)
                throw new SoftwareOperationException("SOFTWARE_UPDATE_TARGET_MISSING");
            if (envelope.Operation == "uninstall" && before.Installed == false)
                return BuildVerifiedResult(job, started, watch, envelope, profile, 0, false, false, before.Version);

            phase = "msiexec";
            WriteJournal(journalPath, job.Id, envelope, "process_started");
            string executable = Path.Combine(Environment.SystemDirectory, "msiexec.exe");
            string arguments = envelope.Operation == "uninstall"
                ? "/x " + productCode + " /qn /norestart"
                : "/i " + Quote(msi) + " /qn /norestart ALLUSERS=1";
            exitCode = await _run(executable, arguments, ct);
            if (exitCode is not (0 or 3010)) throw new SoftwareOperationException("SOFTWARE_MSI_EXIT_FAILED");

            phase = "verification";
            (bool? Installed, string Version) after = _detect(productCode);
            bool verified = envelope.Operation == "uninstall"
                ? after.Installed == false && after.Version.Length == 0
                : after.Installed == true && after.Version.Equals(profile.ExpectedVersion, StringComparison.Ordinal);
            if (!verified) throw new SoftwareOperationException("SOFTWARE_INSTALLED_STATE_UNCONFIRMED");
            WriteJournal(journalPath, job.Id, envelope, "verified");
            return BuildVerifiedResult(job, started, watch, envelope, profile, exitCode, exitCode == 3010,
                after.Installed == true, after.Version);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            string error = hostCt.IsCancellationRequested ? "SOFTWARE_OPERATION_INTERRUPTED" : "SOFTWARE_OPERATION_TIMEOUT";
            return BuildFailureResult(job, started, watch, envelope, error, phase, exitCode);
        }
        catch (SoftwareOperationException error)
        {
            return BuildFailureResult(job, started, watch, envelope, error.Code, phase, exitCode);
        }
        catch (Exception)
        {
            return BuildFailureResult(job, started, watch, envelope,
                phase == "download" ? "SOFTWARE_DOWNLOAD_FAILED" : "SOFTWARE_EXECUTION_FAILED", phase, exitCode);
        }
        finally
        {
            try
            {
                if (ownedOperationDirectory is not null)
                {
                    if (Directory.Exists(ownedOperationDirectory) && !IsReparse(ownedOperationDirectory))
                        Directory.Delete(ownedOperationDirectory, recursive: true);
                }
            }
            catch (Exception) { /* Preserve the durable job journal and unresolved status. */ }
        }
    }

    private static void ValidateProfile(SoftwareOperationProfile profile, SoftwareOperationEnvelope envelope)
    {
        if (profile.SchemaVersion != 2 || profile.OperationId != envelope.OperationId || profile.Operation != envelope.Operation
            || profile.RequestId != envelope.RequestId || profile.PackageUuid != envelope.PackageUuid
            || !profile.ArtifactSha256.Equals(envelope.ArtifactSha256, StringComparison.OrdinalIgnoreCase)
            || profile.ArtifactRevision != envelope.ArtifactRevision || profile.ProfileRevision != envelope.ProfileRevision
            || !profile.ProfileSha256.Equals(envelope.ProfileSha256, StringComparison.OrdinalIgnoreCase)
            || profile.SizeBytes is < 1 or > MaximumArtifactBytes || !Guid.TryParse(profile.ProductCode, out Guid productCode)
            || string.IsNullOrWhiteSpace(profile.ExpectedVersion) || profile.ExpectedVersion.Length > 120
            || !Guid.TryParse(profile.MachineId, out _))
            throw new SoftwareOperationException("SOFTWARE_PROFILE_SNAPSHOT_MISMATCH");
        JsonElement body = profile.Profile;
        if (!ProfileDigest(body).Equals(envelope.ProfileSha256, StringComparison.Ordinal))
            throw new SoftwareOperationException("SOFTWARE_PROFILE_HASH_INVALID");
        if (body.ValueKind != JsonValueKind.Object || body.GetProperty("schema_version").GetInt32() != 1
            || body.GetProperty("expected_version").GetString() != profile.ExpectedVersion
            || body.GetProperty("detection").GetProperty("method").GetString() != "msi_product_code"
            || Guid.Parse(body.GetProperty("detection").GetProperty("product_code").GetString()!) != productCode)
            throw new SoftwareOperationException("SOFTWARE_PROFILE_INVALID");
        string section = envelope.Operation == "uninstall" ? "uninstall" : envelope.Operation;
        string method = envelope.Operation switch { "install" => "msi", "update" => "msi_upgrade", "uninstall" => "msi_product_code", _ => "" };
        if (method.Length == 0 || !body.TryGetProperty(section, out JsonElement operation)
            || operation.GetProperty("method").GetString() != method)
            throw new SoftwareOperationException("SOFTWARE_OPERATION_NOT_CONFIGURED");
        if (envelope.Operation is "install" or "update")
        {
            if (Guid.Parse(body.GetProperty("install").GetProperty("product_code").GetString()!) != productCode
                || operation.GetProperty("arguments").GetArrayLength() != 0)
                throw new SoftwareOperationException("SOFTWARE_MSI_ARGUMENTS_UNSUPPORTED");
        }
        else if (Guid.Parse(operation.GetProperty("product_code").GetString()!) != productCode)
            throw new SoftwareOperationException("SOFTWARE_PROFILE_INVALID");
    }

    private async Task<JobExecutionResult> ExecuteExeV2Async(AgentConfig config, AgentJobRequest job,
        ISoftwareOperationTransport transport, DateTimeOffset started, CancellationToken ct, CancellationToken hostCt,
        Stopwatch watch, SoftwareOperationEnvelope envelope, SoftwareOperationProfile profile)
    {
        string phase = "profile";
        int exitCode = 1;
        string? operationDirectory = null;
        bool preserveForRecovery = false;
        bool processMayHaveStarted = false;
        try
        {
            if (!IsExeLocalPolicyReady(config)) throw new SoftwareOperationException("SOFTWARE_LOCAL_TRUST_POLICY_UNAVAILABLE");
            ValidateExeProfile(profile, envelope);
            JsonElement body = profile.Profile;
            JsonElement op = body.GetProperty(envelope.Operation);
            int configuredTimeout = op.GetProperty("timeout_seconds").GetInt32();
            if (job.TimeoutSeconds != configuredTimeout)
                throw new SoftwareOperationException("SOFTWARE_OPERATION_TIMEOUT_MISMATCH");
            string identity = ExeDetectionIdentity(body.GetProperty("detection"));
            string stateRoot = Path.GetFullPath(Path.GetDirectoryName(config.StatePath)
                ?? throw new SoftwareOperationException("SOFTWARE_STAGING_UNAVAILABLE"));
            EnsureNoReparsePath(stateRoot);
            string stagingRoot = Path.Combine(stateRoot, "software-operation-staging");
            Directory.CreateDirectory(stagingRoot);
            EnsureNoReparsePath(stagingRoot);
            EnsureNoUnresolvedExeJournal(stagingRoot, envelope.PackageUuid);
            string directory = Path.Combine(stagingRoot, Guid.Parse(job.Id).ToString("D"));
            if (Directory.Exists(directory) || File.Exists(directory))
                throw new SoftwareOperationException("SOFTWARE_OPERATION_RECOVERY_REQUIRES_REVIEW");
            Directory.CreateDirectory(directory);
            operationDirectory = directory;
            EnsureNoReparsePath(directory);
            string journal = Path.Combine(directory, "operation.json");
            WriteExeJournal(journal, job.Id, envelope, "profile_validated");

            phase = "precondition";
            ExeDetectionEvidence before = _detectExe(body.GetProperty("detection"));
            if (before.Installed is null) throw new SoftwareOperationException("SOFTWARE_DETECTION_UNAVAILABLE");
            if (envelope.Operation == "install" && before.Installed == true)
                throw new SoftwareOperationException("SOFTWARE_ALREADY_INSTALLED");
            if (envelope.Operation == "update")
            {
                if (before.Installed != true)
                    throw new SoftwareOperationException("SOFTWARE_UPDATE_TARGET_MISSING");
                string detectionMethod = body.GetProperty("detection").GetProperty("method").GetString() ?? "";
                if (detectionMethod == "windows_service")
                    throw new SoftwareOperationException("SOFTWARE_UPDATE_VERSION_UNVERIFIABLE");
                if (before.Version.Equals(profile.ExpectedVersion, StringComparison.Ordinal))
                    return BuildExeVerifiedResult(job, started, watch, envelope, profile, 0, false, before);
            }
            if (envelope.Operation == "uninstall" && before.Installed == false)
                return BuildExeVerifiedResult(job, started, watch, envelope, profile, 0, false,
                    new(false, "", "absent", identity));

            string executable;
            IReadOnlyList<string> arguments = ReadArguments(op.GetProperty("arguments"));
            if (envelope.Operation == "uninstall")
            {
                phase = "uninstall_signature";
                string configuredPath = ValidateConfiguredExecutablePath(op.GetProperty("executable_path").GetString() ?? "");
                string configuredExecutable = _resolveConfiguredExecutable(configuredPath);
                // Verify the configured uninstaller against the same explicit local publisher pins as the package.
                bool trustedUninstaller = config.SoftwareMsiTrustedPublisherSha256.Any(pin => _authentic(configuredExecutable, pin.ToUpperInvariant()));
                if (!trustedUninstaller) throw new SoftwareOperationException("SOFTWARE_UNINSTALL_SIGNATURE_UNTRUSTED");
                executable = Path.Combine(directory, "approved-uninstaller.exe");
                File.Copy(configuredExecutable, executable, overwrite: false);
                EnsureNoReparsePath(executable);
                if (!config.SoftwareMsiTrustedPublisherSha256.Any(pin => _authentic(executable, pin.ToUpperInvariant())))
                    throw new SoftwareOperationException("SOFTWARE_UNINSTALL_SIGNATURE_UNTRUSTED");
            }
            else
            {
                phase = "download";
                WriteExeJournal(journal, job.Id, envelope, "downloading");
                string artifact = Path.Combine(directory, "artifact.exe");
                (long Size, string Sha256) downloaded = await transport.DownloadArtifactAsync(config, job.Id,
                    envelope.ArtifactSha256, envelope.ArtifactRevision, profile.SizeBytes,
                    Math.Min(config.SoftwareMsiMaxArtifactBytes, MaximumArtifactBytes), artifact, ct);
                if (downloaded.Size != profile.SizeBytes
                    || !downloaded.Sha256.Equals(envelope.ArtifactSha256, StringComparison.OrdinalIgnoreCase))
                    throw new SoftwareOperationException("SOFTWARE_ARTIFACT_HASH_INVALID");
                phase = "signature";
                if (!config.SoftwareMsiTrustedPublisherSha256.Any(pin => _authentic(artifact, pin.ToUpperInvariant())))
                    throw new SoftwareOperationException("SOFTWARE_SIGNATURE_UNTRUSTED");
                executable = artifact;
                WriteExeJournal(journal, job.Id, envelope, "artifact_verified");
            }

            int[] accepted = IntArray(op.GetProperty("accepted_exit_codes"), required: true);
            int[] reboot = IntArray(op.GetProperty("reboot_exit_codes"), required: false);
            phase = "exe";
            WriteExeJournal(journal, job.Id, envelope, "process_started");
            processMayHaveStarted = true;
            exitCode = await _runExe(executable, arguments, ct);
            if (!accepted.Contains(exitCode)) throw new SoftwareOperationException("SOFTWARE_EXE_EXIT_NOT_ACCEPTED");

            phase = "verification";
            ExeDetectionEvidence after = _detectExe(body.GetProperty("detection"));
            bool verified;
            if (envelope.Operation == "uninstall")
                verified = after.Installed == false && after.State == "absent" && after.Version.Length == 0;
            else if (body.GetProperty("detection").GetProperty("method").GetString() == "windows_service")
            {
                string expectedState = body.GetProperty("detection").GetProperty("expected_status").GetString() ?? "any";
                verified = after.Installed == true && after.State is "running" or "stopped"
                    && (expectedState == "any" || after.State == expectedState);
            }
            else
                verified = after.Installed == true && after.State == "present"
                    && after.Version.Equals(profile.ExpectedVersion, StringComparison.Ordinal);
            if (!verified) throw new SoftwareOperationException("SOFTWARE_INSTALLED_STATE_UNCONFIRMED");
            WriteExeJournal(journal, job.Id, envelope, "verified");
            return BuildExeVerifiedResult(job, started, watch, envelope, profile, exitCode,
                reboot.Contains(exitCode), after);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            preserveForRecovery = processMayHaveStarted;
            string error = hostCt.IsCancellationRequested ? "SOFTWARE_OPERATION_INTERRUPTED" : "SOFTWARE_OPERATION_TIMEOUT";
            return BuildFailureResult(job, started, watch, envelope, error, phase, exitCode, "exe");
        }
        catch (SoftwareOperationException error)
        {
            preserveForRecovery = processMayHaveStarted;
            return BuildFailureResult(job, started, watch, envelope, error.Code, phase, exitCode, "exe");
        }
        catch (Exception)
        {
            preserveForRecovery = processMayHaveStarted;
            string error = phase == "download" ? "SOFTWARE_DOWNLOAD_FAILED" : "SOFTWARE_EXECUTION_FAILED";
            return BuildFailureResult(job, started, watch, envelope, error, phase, exitCode, "exe");
        }
        finally
        {
            if (!preserveForRecovery && operationDirectory is not null)
            {
                try
                {
                    if (Directory.Exists(operationDirectory) && !IsReparse(operationDirectory))
                        Directory.Delete(operationDirectory, recursive: true);
                }
                catch (Exception) { /* A retained directory blocks replay and requires operator review. */ }
            }
        }
    }

    private static void ValidateExeProfile(SoftwareOperationProfile profile, SoftwareOperationEnvelope envelope)
    {
        if (profile.SchemaVersion != 2 || profile.PackageType != "exe"
            || profile.OperationId != envelope.OperationId || profile.Operation != envelope.Operation
            || profile.RequestId != envelope.RequestId || profile.PackageUuid != envelope.PackageUuid
            || !profile.ArtifactSha256.Equals(envelope.ArtifactSha256, StringComparison.OrdinalIgnoreCase)
            || profile.ArtifactRevision != envelope.ArtifactRevision || profile.ProfileRevision != envelope.ProfileRevision
            || !profile.ProfileSha256.Equals(envelope.ProfileSha256, StringComparison.OrdinalIgnoreCase)
            || profile.SizeBytes is < 1 or > MaximumArtifactBytes || !Guid.TryParse(profile.MachineId, out _)
            || !ProfileDigest(profile.Profile).Equals(envelope.ProfileSha256, StringComparison.Ordinal))
            throw new SoftwareOperationException("SOFTWARE_PROFILE_SNAPSHOT_MISMATCH");
        JsonElement body = profile.Profile;
        if (body.ValueKind != JsonValueKind.Object || body.GetProperty("schema_version").GetInt32() != 2
            || body.GetProperty("expected_version").GetString() != profile.ExpectedVersion)
            throw new SoftwareOperationException("SOFTWARE_PROFILE_INVALID");
        JsonElement detection = body.GetProperty("detection");
        string method = detection.GetProperty("method").GetString() ?? "";
        if (method is not ("registry_value" or "file_version" or "windows_service"))
            throw new SoftwareOperationException("SOFTWARE_DETECTION_METHOD_INVALID");
        _ = ExeDetectionIdentity(detection);
        string expectedMethod = envelope.Operation switch
        {
            "install" => "exe",
            "update" or "uninstall" => "vendor_exe",
            _ => ""
        };
        if (expectedMethod.Length == 0 || !body.TryGetProperty(envelope.Operation, out JsonElement op)
            || op.ValueKind != JsonValueKind.Object || op.GetProperty("method").GetString() != expectedMethod)
            throw new SoftwareOperationException("SOFTWARE_OPERATION_NOT_CONFIGURED");
        int timeout = op.GetProperty("timeout_seconds").GetInt32();
        if (timeout is < 30 or > 1800) throw new SoftwareOperationException("SOFTWARE_OPERATION_TIMEOUT_INVALID");
        IReadOnlyList<string> args = ReadArguments(op.GetProperty("arguments"));
        if (args.Count > 64) throw new SoftwareOperationException("SOFTWARE_OPERATION_ARGUMENTS_INVALID");
        int[] accepted = IntArray(op.GetProperty("accepted_exit_codes"), required: true);
        int[] reboot = IntArray(op.GetProperty("reboot_exit_codes"), required: false);
        if (!accepted.Contains(0) || accepted.Contains(1641) || reboot.Any(code => !accepted.Contains(code) || code is 0 or 1641))
            throw new SoftwareOperationException("SOFTWARE_OPERATION_EXIT_CODES_INVALID");
        if (envelope.Operation == "uninstall")
            _ = ValidateConfiguredExecutablePath(op.GetProperty("executable_path").GetString() ?? "");
        if ((method is "registry_value" or "file_version") && string.IsNullOrWhiteSpace(profile.ExpectedVersion)
            && (envelope.Operation is "install" or "update"))
            throw new SoftwareOperationException("SOFTWARE_EXPECTED_VERSION_REQUIRED");
        if (envelope.Operation == "update" && method == "windows_service")
            throw new SoftwareOperationException("SOFTWARE_UPDATE_VERSION_UNVERIFIABLE");
    }

    private static IReadOnlyList<string> ReadArguments(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() > 64)
            throw new SoftwareOperationException("SOFTWARE_OPERATION_ARGUMENTS_INVALID");
        List<string> arguments = new();
        int total = 0;
        foreach (JsonElement item in value.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String) throw new SoftwareOperationException("SOFTWARE_OPERATION_ARGUMENTS_INVALID");
            string argument = item.GetString() ?? "";
            if (argument.Length > 512 || argument.Any(character => char.IsControl(character)))
                throw new SoftwareOperationException("SOFTWARE_OPERATION_ARGUMENTS_INVALID");
            total += argument.Length;
            arguments.Add(argument);
        }
        if (total > 4096) throw new SoftwareOperationException("SOFTWARE_OPERATION_ARGUMENTS_INVALID");
        return arguments;
    }

    private static int[] IntArray(JsonElement value, bool required)
    {
        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() > 32
            || required && value.GetArrayLength() == 0) throw new SoftwareOperationException("SOFTWARE_OPERATION_EXIT_CODES_INVALID");
        List<int> codes = new();
        foreach (JsonElement item in value.EnumerateArray())
        {
            if (!item.TryGetInt32(out int code) || code is < 0 or > 65535)
                throw new SoftwareOperationException("SOFTWARE_OPERATION_EXIT_CODES_INVALID");
            codes.Add(code);
        }
        if (codes.Distinct().Count() != codes.Count) throw new SoftwareOperationException("SOFTWARE_OPERATION_EXIT_CODES_INVALID");
        return codes.ToArray();
    }

    private static string ExeDetectionIdentity(JsonElement detection)
    {
        string method = detection.GetProperty("method").GetString() ?? "";
        if (method == "registry_value")
        {
            string hive = detection.GetProperty("hive").GetString() ?? "";
            string view = detection.GetProperty("view").GetString() ?? "";
            string key = detection.GetProperty("key").GetString() ?? "";
            string value = detection.GetProperty("value_name").GetString() ?? "";
            string identity = string.Join("/", hive, view, key, value);
            if (hive != "HKLM" || view is not ("registry32" or "registry64")
                || !Regex.IsMatch(key, @"^SOFTWARE(?:\\[A-Za-z0-9 _().-]{1,128}){1,12}$", RegexOptions.IgnoreCase)
                || !Regex.IsMatch(value, "^[A-Za-z0-9 _().-]{1,128}$") || identity.Length > 512)
                throw new SoftwareOperationException("SOFTWARE_REGISTRY_DETECTION_INVALID");
            return identity;
        }
        if (method == "file_version")
        {
            string path = detection.GetProperty("path").GetString() ?? "";
            if (path.Length > 512 || !IsAllowedMacroPath(path, allowLocalAppData: false) || !path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)
                || detection.GetProperty("version_field").GetString() is not ("file_version" or "product_version"))
                throw new SoftwareOperationException("SOFTWARE_FILE_DETECTION_INVALID");
            return path;
        }
        if (method == "windows_service")
        {
            string name = detection.GetProperty("service_name").GetString() ?? "";
            string expected = detection.GetProperty("expected_status").GetString() ?? "";
            if (!Regex.IsMatch(name, "^[A-Za-z0-9_.-]{1,256}$") || expected is not ("any" or "running" or "stopped"))
                throw new SoftwareOperationException("SOFTWARE_SERVICE_DETECTION_INVALID");
            return name;
        }
        throw new SoftwareOperationException("SOFTWARE_DETECTION_METHOD_INVALID");
    }

    private static ExeDetectionEvidence DetectExeInstalled(JsonElement detection)
    {
        string method = detection.GetProperty("method").GetString() ?? "";
        string identity = ExeDetectionIdentity(detection);
        try
        {
            if (method == "registry_value")
            {
                RegistryView view = detection.GetProperty("view").GetString() == "registry64" ? RegistryView.Registry64 : RegistryView.Registry32;
                using RegistryKey root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view);
                using RegistryKey? key = root.OpenSubKey(detection.GetProperty("key").GetString()!, writable: false);
                if (key is null) return new(false, "", "absent", identity);
                object? value = key.GetValue(detection.GetProperty("value_name").GetString()!, null,
                    RegistryValueOptions.DoNotExpandEnvironmentNames);
                if (value is null) return new(false, "", "absent", identity);
                string version = value as string ?? throw new InvalidDataException("SOFTWARE_REGISTRY_VERSION_INVALID");
                return new(true, version, "present", identity);
            }
            if (method == "file_version")
            {
                string path = ResolveConfiguredPath(detection.GetProperty("path").GetString() ?? "", allowLocalAppData: false);
                EnsureNoReparsePath(path);
                if (!File.Exists(path)) return new(false, "", "absent", identity);
                string version = System.Diagnostics.FileVersionInfo.GetVersionInfo(path).FileVersion ?? "";
                if (detection.GetProperty("version_field").GetString() == "product_version")
                    version = System.Diagnostics.FileVersionInfo.GetVersionInfo(path).ProductVersion ?? "";
                return new(true, version, "present", identity);
            }
            if (method == "windows_service")
            {
                string name = detection.GetProperty("service_name").GetString()!;
                nint manager = OpenSCManager(null, null, 0x0001);
                if (manager == 0) return new(null, "", "unknown", identity);
                try
                {
                    nint service = OpenService(manager, name, 0x0004);
                    if (service == 0)
                    {
                        int error = Marshal.GetLastWin32Error();
                        return error == 1060 ? new(false, "", "absent", identity) : new(null, "", "unknown", identity);
                    }
                    try
                    {
                        if (!QueryServiceStatusEx(service, 0, out ServiceStatusProcess status,
                                (uint)Marshal.SizeOf<ServiceStatusProcess>(), out _))
                            return new(null, "", "unknown", identity);
                        string state = status.CurrentState switch { 1 => "stopped", 4 => "running", _ => "pending" };
                        return new(true, "", state, identity);
                    }
                    finally { CloseServiceHandle(service); }
                }
                finally { CloseServiceHandle(manager); }
            }
        }
        catch (FileNotFoundException) { return new(false, "", "absent", identity); }
        catch (DirectoryNotFoundException) { return new(false, "", "absent", identity); }
        catch (Exception) { return new(null, "", "unknown", identity); }
        return new(null, "", "unknown", identity);
    }

    private static bool IsAllowedMacroPath(string path, bool allowLocalAppData) =>
        (path.StartsWith(@"%ProgramFiles%\", StringComparison.OrdinalIgnoreCase)
         || path.StartsWith(@"%ProgramFiles(x86)%\", StringComparison.OrdinalIgnoreCase)
         || path.StartsWith(@"%ProgramData%\", StringComparison.OrdinalIgnoreCase)
         || allowLocalAppData && path.StartsWith(@"%LOCALAPPDATA%\", StringComparison.OrdinalIgnoreCase))
        && !path.Split('\\').Any(part => part == "..") && !path.Any(char.IsControl);

    private static string ValidateConfiguredExecutablePath(string path)
    {
        if (!IsAllowedMacroPath(path, allowLocalAppData: false) || !path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
            throw new SoftwareOperationException("SOFTWARE_UNINSTALL_PATH_INVALID");
        return path;
    }

    private static string ResolveConfiguredExecutable(string configured)
    {
        string path = ResolveConfiguredPath(ValidateConfiguredExecutablePath(configured), allowLocalAppData: false);
        EnsureNoReparsePath(path);
        if (!File.Exists(path)) throw new SoftwareOperationException("SOFTWARE_UNINSTALL_EXECUTABLE_MISSING");
        return path;
    }

    private static string ResolveConfiguredPath(string configured, bool allowLocalAppData)
    {
        if (!IsAllowedMacroPath(configured, allowLocalAppData)) throw new SoftwareOperationException("SOFTWARE_DETECTION_PATH_INVALID");
        int separator = configured.IndexOf('\\');
        string macro = configured[..separator];
        string remainder = configured[(separator + 1)..];
        string root = macro.ToUpperInvariant() switch
        {
            "%PROGRAMFILES%" => Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "%PROGRAMFILES(X86)%" => Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
            "%PROGRAMDATA%" => Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "%LOCALAPPDATA%" when allowLocalAppData => Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            _ => ""
        };
        if (string.IsNullOrWhiteSpace(root)) throw new SoftwareOperationException("SOFTWARE_DETECTION_PATH_INVALID");
        string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string full = Path.GetFullPath(Path.Combine(fullRoot, remainder));
        if (!full.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase))
            throw new SoftwareOperationException("SOFTWARE_DETECTION_PATH_INVALID");
        return full;
    }

    private static JobExecutionResult BuildExeVerifiedResult(AgentJobRequest job, DateTimeOffset started, Stopwatch watch,
        SoftwareOperationEnvelope envelope, SoftwareOperationProfile profile, int exitCode, bool reboot,
        ExeDetectionEvidence evidence)
    {
        watch.Stop();
        return new JobExecutionResult { JobId = job.Id, Status = "completed", StartedAt = started,
            FinishedAt = DateTimeOffset.UtcNow, DurationSeconds = Math.Round(watch.Elapsed.TotalSeconds, 3), ExitCode = exitCode,
            Result = new { schema_version = 2, operation_id = envelope.OperationId, operation = envelope.Operation,
                request_id = envelope.RequestId, package_uuid = envelope.PackageUuid, artifact_sha256 = envelope.ArtifactSha256,
                artifact_revision = envelope.ArtifactRevision, profile_revision = envelope.ProfileRevision,
                profile_sha256 = envelope.ProfileSha256, phase = "verified", verified = true, success = true,
                exit_code = exitCode, reboot_pending = reboot,
                evidence = new { method = profile.Profile.GetProperty("detection").GetProperty("method").GetString(),
                    identity = evidence.Identity, state = evidence.State, version = evidence.Version,
                    installed = evidence.Installed == true } }, Stdout = "", Stderr = "", ErrorMessage = "" };
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ServiceStatusProcess
    {
        public uint ServiceType, CurrentState, ControlsAccepted, Win32ExitCode, ServiceSpecificExitCode,
            CheckPoint, WaitHint, ProcessId, ServiceFlags;
    }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern nint OpenSCManager(string? machine, string? database, uint access);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern nint OpenService(nint manager, string name, uint access);
    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool QueryServiceStatusEx(nint service, int level, out ServiceStatusProcess status, uint size, out uint needed);
    [DllImport("advapi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseServiceHandle(nint handle);

    internal static string ProfileDigest(JsonElement profile)
    {
        StringBuilder canonical = new();
        WriteCanonicalJson(canonical, profile);
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical.ToString()))).ToLowerInvariant();
    }

    private static void WriteCanonicalJson(StringBuilder output, JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                output.Append('{');
                bool first = true;
                foreach (JsonProperty property in value.EnumerateObject().OrderBy(item => item.Name, StringComparer.Ordinal))
                {
                    if (!first) output.Append(',');
                    first = false;
                    AppendCanonicalString(output, property.Name);
                    output.Append(':');
                    WriteCanonicalJson(output, property.Value);
                }
                output.Append('}');
                break;
            case JsonValueKind.Array:
                output.Append('[');
                first = true;
                foreach (JsonElement item in value.EnumerateArray())
                {
                    if (!first) output.Append(',');
                    first = false;
                    WriteCanonicalJson(output, item);
                }
                output.Append(']');
                break;
            case JsonValueKind.String: AppendCanonicalString(output, value.GetString() ?? ""); break;
            case JsonValueKind.Number: output.Append(value.GetRawText()); break;
            case JsonValueKind.True: output.Append("true"); break;
            case JsonValueKind.False: output.Append("false"); break;
            case JsonValueKind.Null: output.Append("null"); break;
            default: throw new InvalidDataException("SOFTWARE_PROFILE_JSON_INVALID");
        }
    }

    private static void AppendCanonicalString(StringBuilder output, string value)
    {
        output.Append('"');
        for (int index = 0; index < value.Length; index++)
        {
            char character = value[index];
            switch (character)
            {
                case '"': output.Append("\\\""); break;
                case '\\': output.Append("\\\\"); break;
                case '\b': output.Append("\\b"); break;
                case '\t': output.Append("\\t"); break;
                case '\n': output.Append("\\n"); break;
                case '\f': output.Append("\\f"); break;
                case '\r': output.Append("\\r"); break;
                default:
                    if (character < 0x20 || character > 0x7e)
                    {
                        output.Append("\\u").Append(((int)character).ToString("x4", System.Globalization.CultureInfo.InvariantCulture));
                        if (char.IsHighSurrogate(character) && index + 1 < value.Length && char.IsLowSurrogate(value[index + 1]))
                            output.Append("\\u").Append(((int)value[++index]).ToString("x4", System.Globalization.CultureInfo.InvariantCulture));
                    }
                    else output.Append(character);
                    break;
            }
        }
        output.Append('"');
    }

    private static JobExecutionResult BuildVerifiedResult(AgentJobRequest job, DateTimeOffset started, Stopwatch watch,
        SoftwareOperationEnvelope envelope, SoftwareOperationProfile profile, int exitCode, bool reboot, bool installed, string version)
    {
        watch.Stop();
        return new JobExecutionResult { JobId = job.Id, Status = "completed", StartedAt = started,
            FinishedAt = DateTimeOffset.UtcNow, DurationSeconds = Math.Round(watch.Elapsed.TotalSeconds, 3), ExitCode = exitCode,
            Result = new { schema_version = 2, operation_id = envelope.OperationId, operation = envelope.Operation,
                request_id = envelope.RequestId, package_uuid = envelope.PackageUuid, artifact_sha256 = envelope.ArtifactSha256,
                artifact_revision = envelope.ArtifactRevision, profile_revision = envelope.ProfileRevision,
                profile_sha256 = envelope.ProfileSha256, phase = "verified", verified = true, success = true,
                exit_code = exitCode, reboot_pending = reboot,
                evidence = new { method = "msi_product_code", product_code = Guid.Parse(profile.ProductCode).ToString("B").ToUpperInvariant(),
                    version = installed ? version : "", installed } }, Stdout = "", Stderr = "", ErrorMessage = "" };
    }

    private static JobExecutionResult BuildFailureResult(AgentJobRequest job, DateTimeOffset started, Stopwatch watch,
        SoftwareOperationEnvelope? envelope, string error, string phase, int exitCode, string packageType = "msi")
    {
        watch.Stop();
        object output;
        if (envelope is null) output = new { type = "install_software", error_code = error, phase };
        else
        {
            object evidence = packageType == "exe"
                ? new { method = "exe", identity = "", state = "unknown", version = "", installed = false }
                : new { method = "msi_product_code", product_code = "", version = "", installed = false };
            output = new Dictionary<string, object?>
            {
            ["schema_version"] = 2, ["operation_id"] = envelope.OperationId, ["operation"] = envelope.Operation,
            ["request_id"] = envelope.RequestId, ["package_uuid"] = envelope.PackageUuid,
            ["artifact_sha256"] = envelope.ArtifactSha256, ["artifact_revision"] = envelope.ArtifactRevision,
            ["profile_revision"] = envelope.ProfileRevision, ["profile_sha256"] = envelope.ProfileSha256,
            ["phase"] = phase, ["verified"] = false, ["success"] = false, ["error_code"] = error,
            ["exit_code"] = Math.Max(0, exitCode), ["reboot_pending"] = exitCode == 3010,
            ["evidence"] = evidence
            };
        }
        return new JobExecutionResult { JobId = job.Id, Status = error switch
            { "SOFTWARE_OPERATION_INTERRUPTED" => "interrupted", "SOFTWARE_OPERATION_TIMEOUT" => "timed_out", _ => "failed" },
            StartedAt = started, FinishedAt = DateTimeOffset.UtcNow, DurationSeconds = Math.Round(watch.Elapsed.TotalSeconds, 3),
            ExitCode = exitCode == 0 ? 1 : exitCode, Result = output, Stdout = "", Stderr = "", ErrorMessage = "" };
    }

    private static string Quote(string value) => "\"" + value.Replace("\"", "\\\"") + "\"";
    private static bool IsReparse(string path) => (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;
    private static void EnsureNoReparsePath(string path)
    {
        string current = Path.GetFullPath(path);
        while (!string.IsNullOrEmpty(current))
        {
            if ((Directory.Exists(current) || File.Exists(current)) && IsReparse(current))
                throw new SoftwareOperationException("SOFTWARE_STAGING_REPARSE_BLOCKED");
            string? parent = Path.GetDirectoryName(current);
            if (string.IsNullOrEmpty(parent) || parent == current) break;
            current = parent;
        }
    }
    private static void WriteJournal(string path, string jobId, SoftwareOperationEnvelope envelope, string state) =>
        NightOwlFileStore.WriteAllTextDurable(path, JsonSerializer.Serialize(new { schema_version = 1, job_id = jobId,
            operation_id = envelope.OperationId, artifact_sha256 = envelope.ArtifactSha256,
            artifact_revision = envelope.ArtifactRevision, state, updated_at = DateTimeOffset.UtcNow }));

    private static void WriteExeJournal(string path, string jobId, SoftwareOperationEnvelope envelope, string state) =>
        NightOwlFileStore.WriteAllTextDurable(path, JsonSerializer.Serialize(new { schema_version = 2, job_id = jobId,
            operation_id = envelope.OperationId, request_id = envelope.RequestId, package_uuid = envelope.PackageUuid,
            operation = envelope.Operation, artifact_sha256 = envelope.ArtifactSha256,
            artifact_revision = envelope.ArtifactRevision, profile_revision = envelope.ProfileRevision,
            profile_sha256 = envelope.ProfileSha256, state, updated_at = DateTimeOffset.UtcNow }));

    private static void EnsureNoUnresolvedExeJournal(string stagingRoot, string packageUuid)
    {
        IEnumerable<string> candidateDirectories = Directory.EnumerateDirectories(stagingRoot).Take(513).ToArray();
        if (candidateDirectories.Count() > 512) throw new SoftwareOperationException("SOFTWARE_OPERATION_RECOVERY_REQUIRES_REVIEW");
        foreach (string directory in candidateDirectories)
        {
            EnsureNoReparsePath(directory);
            string journal = Path.Combine(directory, "operation.json");
            if (!File.Exists(journal)) continue;
            EnsureNoReparsePath(journal);
            FileInfo info = new(journal);
            if (info.Length is < 1 or > 16 * 1024)
                throw new SoftwareOperationException("SOFTWARE_OPERATION_RECOVERY_REQUIRES_REVIEW");
            try
            {
                using JsonDocument document = JsonDocument.Parse(File.ReadAllBytes(journal));
                JsonElement root = document.RootElement;
                if (root.GetProperty("package_uuid").GetString() == packageUuid
                    && root.GetProperty("state").GetString() != "verified")
                    throw new SoftwareOperationException("SOFTWARE_OPERATION_RECOVERY_REQUIRES_REVIEW");
            }
            catch (SoftwareOperationException) { throw; }
            catch (Exception) { throw new SoftwareOperationException("SOFTWARE_OPERATION_RECOVERY_REQUIRES_REVIEW"); }
        }
    }

    private static (bool? Installed,string Version) DetectInstalled(string product)
    {
        // This executor installs with ALLUSERS=1 and runs as the service
        // identity. Query only the machine context so a per-user registration
        // in the service account's profile cannot be mistaken for machine state.
        uint length = 0;
        uint initial = MsiGetProductInfoEx(product, null, MsiInstallContextMachine,
            "VersionString", null, ref length);
        if (initial == ErrorUnknownProduct) return (false, "");
        if (!TryGetMachineProductInfoCapacity(initial, length, out uint capacity)) return (null, "");
        System.Text.StringBuilder value = new((int)capacity);
        length = capacity;
        uint queried = MsiGetProductInfoEx(product, null, MsiInstallContextMachine,
            "VersionString", value, ref length);
        return InterpretMachineProductInfo(queried, value.ToString());
    }

    internal static bool TryGetMachineProductInfoCapacity(uint error, uint valueLength, out uint capacity)
    {
        // MsiGetProductInfoExW returns ERROR_SUCCESS (not ERROR_MORE_DATA)
        // when szValue is NULL and pcchValue is valid, while still reporting
        // the required character count. The second call needs room for NUL.
        if (error == 0 && valueLength is > 0 and <= 120)
        {
            capacity = valueLength + 1;
            return true;
        }
        capacity = 0;
        return false;
    }

    internal static (bool? Installed, string Version) InterpretMachineProductInfo(uint error, string version) =>
        error switch
        {
            0 when !string.IsNullOrWhiteSpace(version) && version.Length <= 120 => (true, version),
            ErrorUnknownProduct => (false, ""),
            _ => (null, "")
        };

    private const uint MsiInstallContextMachine = 4;
    private const uint ErrorUnknownProduct = 1605;

    private sealed class SoftwareOperationException(string code) : Exception(code) { internal string Code { get; } = code; }
    internal async Task<object> InstallAsync(AgentConfig config,string packageId,CancellationToken ct)
    {
        if(!config.SoftwareInstallEnabled) throw new InvalidOperationException("SOFTWARE_INSTALL_DISABLED");
        string root=Path.GetDirectoryName(config.StatePath)!;
        string catalog=Path.Combine(root,"software-catalog.json");
        byte[] bytes=await File.ReadAllBytesAsync(catalog,ct);
        if(bytes.Length>256*1024 || !Hash(bytes,config.SoftwareCatalogSha256)) throw new InvalidDataException("SOFTWARE_CATALOG_UNTRUSTED");
        ApprovedSoftware[] entries=JsonSerializer.Deserialize<ApprovedSoftware[]>(bytes,new JsonSerializerOptions(JsonSerializerDefaults.Web)) ?? Array.Empty<ApprovedSoftware>();
        ApprovedSoftware[] matches=entries.Where(item=>item.Id==packageId).ToArray();
        if(matches.Length!=1) throw new InvalidDataException("SOFTWARE_PACKAGE_NOT_APPROVED");
        ApprovedSoftware package=matches[0];
        if(!Uri.TryCreate(package.Url,UriKind.Absolute,out Uri? uri) || uri.Scheme!="https" || uri.UserInfo!="" || uri.Query!="" || uri.Fragment!="" ||
            !Guid.TryParse(package.ProductCode,out Guid product) || package.Size is < 1 or > 100*1024*1024 ||
            package.Sha256.Length!=64 || package.SignerSha256.Length!=64 || string.IsNullOrWhiteSpace(package.Version))
            throw new InvalidDataException("SOFTWARE_POLICY_INVALID");
        string productCode=product.ToString("B").ToUpperInvariant();
        string journal=Path.Combine(root,"software-install-"+product.ToString("N")+".json");
        if(_installed(productCode,package.Version)) return new {installed=true,verified=true,already_present=true,package_id=packageId,scope="machine"};
        if(File.Exists(journal)) throw new InvalidOperationException("SOFTWARE_PREVIOUS_OUTCOME_REQUIRES_REVIEW");
        string directory=Path.Combine(root,"software-staging",Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        string file=Path.Combine(directory,"package.msi");
        try
        {
            byte[] payload=await _download(package,ct);
            if(payload.LongLength!=package.Size || !Hash(payload,package.Sha256)) throw new InvalidDataException("SOFTWARE_HASH_INVALID");
            await NightOwlFileStore.WriteAllBytesAsync(file,payload,ct);
            if(!_authentic(file,package.SignerSha256)) throw new InvalidDataException("SOFTWARE_SIGNATURE_INVALID");
            // Local admin-owned catalog only. No arbitrary arguments, user context or secrets.
            NightOwlFileStore.WriteAllTextDurable(journal,JsonSerializer.Serialize(new {package_id=packageId,product_code=productCode,state="outcome_pending",started_at=DateTimeOffset.UtcNow}));
            int code=await _run(Path.Combine(Environment.SystemDirectory,"msiexec.exe"),"/i \""+file+"\" /qn /norestart ALLUSERS=1",ct);
            bool verified=(code==0 || code==3010) && _installed(productCode,package.Version);
            if(!verified) throw new InvalidOperationException("SOFTWARE_INSTALLATION_UNCONFIRMED");
            NightOwlFileStore.WriteAllTextDurable(journal,JsonSerializer.Serialize(new {package_id=packageId,product_code=productCode,state="verified",exit_code=code}));
            return new {installed=true,verified=true,package_id=packageId,exit_code=code,reboot_required=code==3010,scope="machine"};
        }
        finally { if(File.Exists(file)) File.Delete(file); if(Directory.Exists(directory)) Directory.Delete(directory); }
    }
    private static bool Hash(byte[] data,string expected) => expected.Length==64 &&
        CryptographicOperations.FixedTimeEquals(System.Text.Encoding.ASCII.GetBytes(Convert.ToHexString(SHA256.HashData(data))),System.Text.Encoding.ASCII.GetBytes(expected.ToUpperInvariant()));
    private static async Task<byte[]> Download(ApprovedSoftware package,CancellationToken ct)
    {
        using HttpClient client=new(new HttpClientHandler {AllowAutoRedirect=false});
        using HttpResponseMessage response=await client.GetAsync(package.Url,HttpCompletionOption.ResponseHeadersRead,ct);
        response.EnsureSuccessStatusCode();
        if((int)response.StatusCode!=200 || response.Content.Headers.ContentLength!=package.Size) throw new InvalidDataException("SOFTWARE_DOWNLOAD_SIZE_INVALID");
        using Stream source=await response.Content.ReadAsStreamAsync(ct);
        using MemoryStream buffer=new();
        byte[] chunk=new byte[65536];
        int count;
        while((count=await source.ReadAsync(chunk,ct))>0)
        { if(buffer.Length+count>package.Size) throw new InvalidDataException("SOFTWARE_DOWNLOAD_TOO_LARGE"); await buffer.WriteAsync(chunk.AsMemory(0,count),ct); }
        return buffer.ToArray();
    }
    private static bool Installed(string product,string version)
    {
        if(MsiQueryProductState(product)!=5) return false;
        uint length=0;
        MsiGetProductInfo(product,"VersionString",null,ref length);
        var result=new System.Text.StringBuilder((int)length+1);
        length++;
        return MsiGetProductInfo(product,"VersionString",result,ref length)==0 && result.ToString()==version;
    }
    private static bool Authenticode(string file,string signer)
    {
        nint pointer=Marshal.AllocHGlobal(Marshal.SizeOf<TrustFile>());
        TrustFile info=new() {Size=(uint)Marshal.SizeOf<TrustFile>(),Path=file};
        Marshal.StructureToPtr(info,pointer,false);
        TrustData data=new() {Size=(uint)Marshal.SizeOf<TrustData>(),Ui=2,Revocation=1,Choice=1,File=pointer,StateAction=1};
        Guid action=new("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");
        try
        {
            if(WinVerifyTrust(new nint(-1),ref action,ref data)!=0) return false;
            using X509Certificate cert=X509Certificate.CreateFromSignedFile(file);
            return Hash(cert.GetRawCertData(),signer);
        }
        finally { data.StateAction=2; WinVerifyTrust(new nint(-1),ref action,ref data); Marshal.DestroyStructure<TrustFile>(pointer); Marshal.FreeHGlobal(pointer); }
    }
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] private struct TrustFile {public uint Size; [MarshalAs(UnmanagedType.LPWStr)] public string Path; public nint Handle,KnownSubject;}
    [StructLayout(LayoutKind.Sequential)] private struct TrustData {public uint Size;public nint Policy,Sip;public uint Ui,Revocation,Choice;public nint File;public uint StateAction;public nint State,Url;public uint Flags,Context;public nint Signature;}
    [DllImport("wintrust.dll",ExactSpelling=true)] private static extern int WinVerifyTrust(nint window,ref Guid action,ref TrustData data);
    [DllImport("msi.dll",CharSet=CharSet.Unicode,EntryPoint="MsiQueryProductStateW")] private static extern int MsiQueryProductState(string product);
    [DllImport("msi.dll",CharSet=CharSet.Unicode,EntryPoint="MsiGetProductInfoW")] private static extern uint MsiGetProductInfo(string product,string property,System.Text.StringBuilder? value,ref uint length);
    [DllImport("msi.dll", CharSet=CharSet.Unicode, EntryPoint="MsiGetProductInfoExW")]
    private static extern uint MsiGetProductInfoEx(string productCode, string? userSid, uint context,
        string property, System.Text.StringBuilder? value, ref uint length);
}
