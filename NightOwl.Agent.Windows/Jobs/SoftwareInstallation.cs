using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Diagnostics;
using System.Text.Encodings.Web;
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
    string RequestId, string PackageUuid, string ArtifactSha256, int ArtifactRevision, int ProfileRevision,
    string ProfileSha256, long SizeBytes, string ProductCode, string ExpectedVersion, JsonElement Profile, string MachineId)
{
    internal static SoftwareOperationProfile Parse(JsonElement root, SoftwareOperationEnvelope expected, string machineId)
    {
        try
        {
            SoftwareOperationProfile value = new(
                root.GetProperty("schema_version").GetInt32(), root.GetProperty("operation_id").GetString()!,
                root.GetProperty("operation").GetString()!, root.GetProperty("request_id").GetString()!,
                root.GetProperty("package_uuid").GetString()!, root.GetProperty("artifact_sha256").GetString()!,
                root.GetProperty("artifact_revision").GetInt32(), root.GetProperty("profile_revision").GetInt32(),
                root.GetProperty("profile_sha256").GetString()!, root.GetProperty("size_bytes").GetInt64(),
                root.GetProperty("product_code").GetString()!, root.GetProperty("expected_version").GetString()!,
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

internal sealed class SoftwareInstallation
{
    private readonly Func<ApprovedSoftware,CancellationToken,Task<byte[]>> _download;
    private readonly Func<string,string,bool> _authentic;
    private readonly Func<string,string,bool> _installed;
    private readonly Func<string,string,CancellationToken,Task<int>> _run;
    private readonly Func<string,(bool? Installed,string Version)> _detect;
    internal SoftwareInstallation(Func<ApprovedSoftware,CancellationToken,Task<byte[]>>? download=null,
        Func<string,string,bool>? authentic=null,Func<string,string,bool>? installed=null,
        Func<string,string,CancellationToken,Task<int>>? run=null,
        Func<string,(bool? Installed,string Version)>? detect=null)
    {
        _download=download ?? Download; _authentic=authentic ?? Authenticode;
        _installed=installed ?? Installed; _run=run ?? OwnedProcess.RunAsync; _detect=detect ?? DetectInstalled;
    }

    internal const long MaximumArtifactBytes = 1024L * 1024 * 1024;
    internal static readonly string[] OperationCapabilities = { "software_msi_install:1", "software_msi_update:1", "software_msi_uninstall:1" };

    internal static bool IsLocalPolicyReady(AgentConfig config) => config.SoftwareMsiOperationsEnabled
        && config.SoftwareMsiMaxArtifactBytes is > 0 and <= MaximumArtifactBytes
        && config.SoftwareMsiTrustedPublisherSha256 is { Count: > 0 }
        && config.SoftwareMsiTrustedPublisherSha256.All(pin => Regex.IsMatch(pin ?? "", "^[0-9a-fA-F]{64}$"));

    internal static IEnumerable<string> Capabilities(AgentConfig config) =>
        IsLocalPolicyReady(config) && config.AllowedJobTypes.Contains("install_software", StringComparer.OrdinalIgnoreCase)
            ? OperationCapabilities : Array.Empty<string>();

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
            if (!IsLocalPolicyReady(config)) throw new SoftwareOperationException("SOFTWARE_LOCAL_TRUST_POLICY_UNAVAILABLE");
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

    internal static string ProfileDigest(JsonElement profile)
    {
        using MemoryStream buffer = new();
        using (Utf8JsonWriter writer = new(buffer, new JsonWriterOptions { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping }))
            WriteCanonicalJson(writer, profile);
        return Convert.ToHexString(SHA256.HashData(buffer.ToArray())).ToLowerInvariant();
    }

    private static void WriteCanonicalJson(Utf8JsonWriter writer, JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (JsonProperty property in value.EnumerateObject().OrderBy(item => item.Name, StringComparer.Ordinal))
                { writer.WritePropertyName(property.Name); WriteCanonicalJson(writer, property.Value); }
                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (JsonElement item in value.EnumerateArray()) WriteCanonicalJson(writer, item);
                writer.WriteEndArray();
                break;
            case JsonValueKind.String: writer.WriteStringValue(value.GetString()); break;
            case JsonValueKind.Number: value.WriteTo(writer); break;
            case JsonValueKind.True: writer.WriteBooleanValue(true); break;
            case JsonValueKind.False: writer.WriteBooleanValue(false); break;
            case JsonValueKind.Null: writer.WriteNullValue(); break;
            default: throw new InvalidDataException("SOFTWARE_PROFILE_JSON_INVALID");
        }
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
        SoftwareOperationEnvelope? envelope, string error, string phase, int exitCode)
    {
        watch.Stop();
        object output;
        if (envelope is null) output = new { type = "install_software", error_code = error, phase };
        else output = new Dictionary<string, object?>
        {
            ["schema_version"] = 2, ["operation_id"] = envelope.OperationId, ["operation"] = envelope.Operation,
            ["request_id"] = envelope.RequestId, ["package_uuid"] = envelope.PackageUuid,
            ["artifact_sha256"] = envelope.ArtifactSha256, ["artifact_revision"] = envelope.ArtifactRevision,
            ["profile_revision"] = envelope.ProfileRevision, ["profile_sha256"] = envelope.ProfileSha256,
            ["phase"] = phase, ["verified"] = false, ["success"] = false, ["error_code"] = error,
            ["exit_code"] = Math.Max(0, exitCode), ["reboot_pending"] = exitCode == 3010,
            ["evidence"] = new { method = "msi_product_code", product_code = "", version = "", installed = false }
        };
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
