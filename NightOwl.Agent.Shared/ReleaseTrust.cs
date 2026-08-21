using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Xml;

namespace NightOwl.Agent.Shared;

public static class ReleaseTrustErrorCodes
{
    public const string TrustMetadataInvalid = "TRUST_METADATA_INVALID";
    public const string TrustBundleInvalid = "TRUST_BUNDLE_INVALID";
    public const string TrustBundleExpired = "TRUST_BUNDLE_EXPIRED";
    public const string TrustBundleDowngrade = "TRUST_BUNDLE_DOWNGRADE";
    public const string TrustBundleSameVersionDivergent = "TRUST_BUNDLE_SAME_VERSION_DIVERGENT";
    public const string TrustSignatureInvalid = "TRUST_SIGNATURE_INVALID";
    public const string TrustRootUnknown = "TRUST_ROOT_UNKNOWN";
    public const string TrustRootRevoked = "TRUST_ROOT_REVOKED";
    public const string TrustPrivateParameters = "TRUST_PRIVATE_PARAMETERS";
    public const string TrustKeyDuplicate = "TRUST_KEY_DUPLICATE";
    public const string TrustKeyRevocationRegression = "TRUST_KEY_REVOCATION_REGRESSION";
    public const string TrustInstallFailed = "TRUST_INSTALL_FAILED";
    public const string TrustDownloadFailed = "TRUST_DOWNLOAD_FAILED";
}

public sealed class ReleaseTrustBundle
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("bundle_version")]
    public long BundleVersion { get; set; }

    [JsonPropertyName("generated_at")]
    public DateTimeOffset GeneratedAt { get; set; }

    [JsonPropertyName("valid_from")]
    public DateTimeOffset? ValidFrom { get; set; }

    [JsonPropertyName("valid_until")]
    public DateTimeOffset? ValidUntil { get; set; }

    [JsonPropertyName("keys")]
    public List<ReleaseTrustKey> Keys { get; set; } = new();
}

public sealed class ReleaseTrustKey
{
    [JsonPropertyName("key_id")]
    public string KeyId { get; set; } = "";

    [JsonPropertyName("algorithm")]
    public string Algorithm { get; set; } = "RSA-PSS-SHA256";

    [JsonPropertyName("public_key_xml")]
    public string PublicKeyXml { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "active";

    [JsonPropertyName("valid_from")]
    public DateTimeOffset? ValidFrom { get; set; }

    [JsonPropertyName("valid_until")]
    public DateTimeOffset? ValidUntil { get; set; }

    [JsonPropertyName("revoked_at")]
    public DateTimeOffset? RevokedAt { get; set; }
}

public sealed class ReleaseTrustBundleMetadata
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("bundle_version")]
    public long BundleVersion { get; set; }

    [JsonPropertyName("bundle_sha256")]
    public string BundleSha256 { get; set; } = "";

    [JsonPropertyName("signature_sha256")]
    public string SignatureSha256 { get; set; } = "";

    [JsonPropertyName("root_key_id")]
    public string RootKeyId { get; set; } = "";

    [JsonPropertyName("size")]
    public long Size { get; set; }

    [JsonPropertyName("generated_at")]
    public DateTimeOffset GeneratedAt { get; set; }

    [JsonPropertyName("bundle_url")]
    public string BundleUrl { get; set; } = "";

    [JsonPropertyName("signature_url")]
    public string SignatureUrl { get; set; } = "";

    [JsonPropertyName("metadata_url")]
    public string MetadataUrl { get; set; } = "";
}

public sealed class ReleaseTrustState
{
    [JsonPropertyName("installed_bundle_version")]
    public long InstalledBundleVersion { get; set; }

    [JsonPropertyName("installed_bundle_sha256")]
    public string InstalledBundleSha256 { get; set; } = "";

    [JsonPropertyName("installed_root_key_id")]
    public string InstalledRootKeyId { get; set; } = "";

    [JsonPropertyName("installed_at")]
    public DateTimeOffset? InstalledAt { get; set; }

    [JsonPropertyName("last_check_at")]
    public DateTimeOffset? LastCheckAt { get; set; }

    [JsonPropertyName("last_success_at")]
    public DateTimeOffset? LastSuccessAt { get; set; }

    [JsonPropertyName("last_error")]
    public string LastError { get; set; } = "";

    [JsonPropertyName("last_job_id")]
    public string LastJobId { get; set; } = "";

    [JsonPropertyName("backup_path")]
    public string BackupPath { get; set; } = "";

    [JsonPropertyName("active_key_ids")]
    public List<string> ActiveKeyIds { get; set; } = new();

    [JsonPropertyName("revoked_key_ids")]
    public List<string> RevokedKeyIds { get; set; } = new();
}

public sealed class ReleaseTrustRootKey
{
    public string KeyId { get; init; } = "";
    public string Algorithm { get; init; } = "RSA-PSS-SHA256";
    public string PublicKeyXml { get; init; } = "";
    public bool Revoked { get; init; }
}

public sealed record ReleaseTrustValidationResult(
    bool IsValid,
    string ErrorCode,
    string ErrorMessage,
    ReleaseTrustBundle? Bundle,
    string BundleSha256,
    string SignatureSha256,
    string RootKeyId)
{
    public static ReleaseTrustValidationResult Ok(ReleaseTrustBundle bundle, string bundleSha256, string signatureSha256, string rootKeyId)
        => new(true, "", "", bundle, bundleSha256, signatureSha256, rootKeyId);

    public static ReleaseTrustValidationResult Fail(string errorCode, string message, string bundleSha256 = "", string signatureSha256 = "", string rootKeyId = "")
        => new(false, errorCode, message, null, bundleSha256, signatureSha256, rootKeyId);
}

public static class ReleaseTrustBundleValidator
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true
    };

    public static ReleaseTrustValidationResult Validate(
        byte[] bundleBytes,
        byte[] signatureBytes,
        ReleaseTrustBundleMetadata metadata,
        IEnumerable<ReleaseTrustRootKey> trustedRoots,
        ReleaseTrustState? currentState = null,
        ReleaseTrustBundle? currentBundle = null,
        DateTimeOffset? now = null)
    {
        DateTimeOffset clock = now ?? DateTimeOffset.UtcNow;
        string bundleSha = Sha256Hex(bundleBytes);
        string signatureSha = Sha256Hex(signatureBytes);

        if (metadata.SchemaVersion != 1 || metadata.BundleVersion <= 0)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustMetadataInvalid, "Invalid trust bundle metadata.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (!FixedHexEquals(bundleSha, metadata.BundleSha256) || !FixedHexEquals(signatureSha, metadata.SignatureSha256))
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustMetadataInvalid, "Metadata hash does not match downloaded files.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (metadata.Size > 0 && metadata.Size != bundleBytes.LongLength)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustMetadataInvalid, "Metadata size does not match trust bundle.", bundleSha, signatureSha, metadata.RootKeyId);
        }

        ReleaseTrustRootKey? root = trustedRoots.FirstOrDefault(item => item.KeyId.Equals(metadata.RootKeyId, StringComparison.OrdinalIgnoreCase));
        if (root is null)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustRootUnknown, $"Unknown trust root: {metadata.RootKeyId}.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (root.Revoked)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustRootRevoked, $"Trust root is revoked: {metadata.RootKeyId}.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (!root.Algorithm.Equals("RSA-PSS-SHA256", StringComparison.OrdinalIgnoreCase))
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustRootUnknown, $"Unsupported trust root algorithm: {root.Algorithm}.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (!VerifySignature(root.PublicKeyXml, bundleBytes, DecodeSignature(signatureBytes)))
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustSignatureInvalid, "Trust bundle signature is invalid.", bundleSha, signatureSha, metadata.RootKeyId);
        }

        ReleaseTrustBundle? bundle;
        try
        {
            bundle = JsonSerializer.Deserialize<ReleaseTrustBundle>(bundleBytes, JsonOptions);
        }
        catch (Exception ex)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleInvalid, $"Trust bundle JSON is invalid: {ex.Message}", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (bundle is null || bundle.SchemaVersion != 1 || bundle.BundleVersion <= 0)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleInvalid, "Trust bundle schema is invalid.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (bundle.BundleVersion != metadata.BundleVersion)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustMetadataInvalid, "Metadata bundle_version does not match bundle.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (bundle.ValidFrom is not null && bundle.ValidFrom.Value > clock)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleInvalid, "Trust bundle is not valid yet.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (bundle.ValidUntil is not null && bundle.ValidUntil.Value <= clock)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleExpired, "Trust bundle is expired.", bundleSha, signatureSha, metadata.RootKeyId);
        }
        if (currentState is not null)
        {
            if (bundle.BundleVersion < currentState.InstalledBundleVersion)
            {
                return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleDowngrade, "Trust bundle downgrade is blocked.", bundleSha, signatureSha, metadata.RootKeyId);
            }
            if (bundle.BundleVersion == currentState.InstalledBundleVersion
                && !string.IsNullOrWhiteSpace(currentState.InstalledBundleSha256)
                && !FixedHexEquals(bundleSha, currentState.InstalledBundleSha256))
            {
                return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleSameVersionDivergent, "Same trust bundle version has different content.", bundleSha, signatureSha, metadata.RootKeyId);
            }
        }

        ReleaseTrustValidationResult policy = ValidateBundlePolicy(bundle, currentBundle, bundleSha, signatureSha, metadata.RootKeyId);
        return policy.IsValid ? ReleaseTrustValidationResult.Ok(bundle, bundleSha, signatureSha, metadata.RootKeyId) : policy;
    }

    public static ReleaseTrustBundle ParseBundleFile(string path)
    {
        return JsonSerializer.Deserialize<ReleaseTrustBundle>(File.ReadAllBytes(path), JsonOptions)
            ?? throw new InvalidOperationException("Trust bundle JSON is empty.");
    }

    public static ReleaseTrustBundleMetadata ParseMetadata(byte[] bytes)
    {
        return JsonSerializer.Deserialize<ReleaseTrustBundleMetadata>(bytes, JsonOptions)
            ?? throw new InvalidOperationException("Trust bundle metadata JSON is empty.");
    }

    private static ReleaseTrustValidationResult ValidateBundlePolicy(ReleaseTrustBundle bundle, ReleaseTrustBundle? currentBundle, string bundleSha, string signatureSha, string rootKeyId)
    {
        Dictionary<string, ReleaseTrustKey> seen = new(StringComparer.OrdinalIgnoreCase);
        int activeCount = 0;
        foreach (ReleaseTrustKey key in bundle.Keys)
        {
            if (string.IsNullOrWhiteSpace(key.KeyId) || string.IsNullOrWhiteSpace(key.PublicKeyXml))
            {
                return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleInvalid, "Trust bundle contains empty key_id or public_key_xml.", bundleSha, signatureSha, rootKeyId);
            }
            if (!seen.TryAdd(key.KeyId, key))
            {
                return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustKeyDuplicate, $"Duplicate key_id in trust bundle: {key.KeyId}.", bundleSha, signatureSha, rootKeyId);
            }
            if (!key.Algorithm.Equals("RSA-PSS-SHA256", StringComparison.OrdinalIgnoreCase))
            {
                return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleInvalid, $"Unsupported key algorithm: {key.KeyId}.", bundleSha, signatureSha, rootKeyId);
            }
            if (ContainsPrivateRsaParameters(key.PublicKeyXml))
            {
                return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustPrivateParameters, $"Public key entry contains private RSA parameters: {key.KeyId}.", bundleSha, signatureSha, rootKeyId);
            }
            if (!key.Status.Equals("active", StringComparison.OrdinalIgnoreCase)
                && !key.Status.Equals("revoked", StringComparison.OrdinalIgnoreCase)
                && !key.Status.Equals("retired", StringComparison.OrdinalIgnoreCase))
            {
                return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleInvalid, $"Unsupported key status: {key.KeyId}.", bundleSha, signatureSha, rootKeyId);
            }
            if (key.ValidFrom is not null && key.ValidUntil is not null && key.ValidUntil <= key.ValidFrom)
            {
                return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleInvalid, $"Invalid key validity window: {key.KeyId}.", bundleSha, signatureSha, rootKeyId);
            }
            if (key.Status.Equals("active", StringComparison.OrdinalIgnoreCase))
            {
                activeCount++;
            }
        }
        if (activeCount == 0)
        {
            return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustBundleInvalid, "Trust bundle must contain at least one active release key.", bundleSha, signatureSha, rootKeyId);
        }
        if (currentBundle is not null)
        {
            foreach (ReleaseTrustKey oldKey in currentBundle.Keys.Where(item => item.Status.Equals("revoked", StringComparison.OrdinalIgnoreCase)))
            {
                if (seen.TryGetValue(oldKey.KeyId, out ReleaseTrustKey? newKey)
                    && newKey.Status.Equals("active", StringComparison.OrdinalIgnoreCase))
                {
                    return ReleaseTrustValidationResult.Fail(ReleaseTrustErrorCodes.TrustKeyRevocationRegression, $"Revoked key cannot become active again: {oldKey.KeyId}.", bundleSha, signatureSha, rootKeyId);
                }
            }
        }
        return ReleaseTrustValidationResult.Ok(bundle, bundleSha, signatureSha, rootKeyId);
    }

    private static bool ContainsPrivateRsaParameters(string publicXml)
    {
        try
        {
            XmlDocument document = new() { XmlResolver = null };
            document.LoadXml(publicXml);
            foreach (string name in new[] { "P", "Q", "DP", "DQ", "InverseQ", "D" })
            {
                if (document.GetElementsByTagName(name).Count > 0)
                {
                    return true;
                }
            }
            return false;
        }
        catch
        {
            return true;
        }
    }

    private static bool VerifySignature(string publicKeyXml, byte[] data, byte[] signature)
    {
        using RSA rsa = RSA.Create();
        rsa.FromXmlString(publicKeyXml);
        return rsa.VerifyData(data, signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pss);
    }

    private static byte[] DecodeSignature(byte[] signatureBytes)
    {
        string text = Encoding.ASCII.GetString(signatureBytes).Trim();
        try
        {
            if (!string.IsNullOrWhiteSpace(text))
            {
                return Convert.FromBase64String(text);
            }
        }
        catch (FormatException)
        {
            // Existing tooling can also persist binary signatures; keep accepting raw bytes.
        }
        return signatureBytes;
    }

    public static string Sha256Hex(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static bool FixedHexEquals(string actual, string expected)
    {
        if (string.IsNullOrWhiteSpace(actual) || string.IsNullOrWhiteSpace(expected))
        {
            return false;
        }
        byte[] left = Encoding.ASCII.GetBytes(actual.Trim().ToLowerInvariant());
        byte[] right = Encoding.ASCII.GetBytes(expected.Trim().ToLowerInvariant());
        return left.Length == right.Length && CryptographicOperations.FixedTimeEquals(left, right);
    }
}

public sealed class ReleaseTrustStore
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true
    };

    private readonly NightOwlPaths _paths;
    private readonly bool _applyAcl;

    public ReleaseTrustStore(NightOwlPaths paths, bool applyAcl = true)
    {
        _paths = paths;
        _applyAcl = applyAcl;
    }

    public ReleaseTrustState LoadState()
    {
        try
        {
            if (!File.Exists(_paths.TrustStatePath))
            {
                return new ReleaseTrustState();
            }
            return JsonSerializer.Deserialize<ReleaseTrustState>(File.ReadAllText(_paths.TrustStatePath), JsonOptions) ?? new ReleaseTrustState();
        }
        catch
        {
            return new ReleaseTrustState();
        }
    }

    public ReleaseTrustBundle? LoadCurrentBundle()
    {
        try
        {
            string path = File.Exists(_paths.TrustBundlePath) ? _paths.TrustBundlePath :
                File.Exists(_paths.LegacyTrustBundlePath) ? _paths.LegacyTrustBundlePath : "";
            return string.IsNullOrWhiteSpace(path) ? null : ReleaseTrustBundleValidator.ParseBundleFile(path);
        }
        catch
        {
            return null;
        }
    }

    public async Task<ReleaseTrustState> InstallAsync(
        ReleaseTrustBundle bundle,
        byte[] bundleBytes,
        byte[] signatureBytes,
        byte[] metadataBytes,
        string bundleSha256,
        string rootKeyId,
        string jobId,
        CancellationToken ct)
    {
        Directory.CreateDirectory(_paths.TrustDir);
        Directory.CreateDirectory(_paths.TrustBackupsDir);
        Directory.CreateDirectory(_paths.TrustDownloadsDir);
        ReleaseTrustState previous = LoadState();
        string backupPath = "";
        if (File.Exists(_paths.TrustBundlePath))
        {
            backupPath = Path.Combine(_paths.TrustBackupsDir, $"release-public-keys-v{previous.InstalledBundleVersion}-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}.json");
            File.Copy(_paths.TrustBundlePath, backupPath, overwrite: false);
        }

        await NightOwlFileStore.WriteAllBytesAsync(_paths.TrustBundlePath, bundleBytes, ct);
        await NightOwlFileStore.WriteAllBytesAsync(_paths.TrustSignaturePath, signatureBytes, ct);
        await NightOwlFileStore.WriteAllBytesAsync(_paths.TrustMetadataPath, metadataBytes, ct);

        ReleaseTrustState state = new()
        {
            InstalledBundleVersion = bundle.BundleVersion,
            InstalledBundleSha256 = bundleSha256,
            InstalledRootKeyId = rootKeyId,
            InstalledAt = DateTimeOffset.UtcNow,
            LastCheckAt = DateTimeOffset.UtcNow,
            LastSuccessAt = DateTimeOffset.UtcNow,
            LastError = "",
            LastJobId = jobId,
            BackupPath = backupPath,
            ActiveKeyIds = bundle.Keys.Where(item => item.Status.Equals("active", StringComparison.OrdinalIgnoreCase)).Select(item => item.KeyId).Order(StringComparer.OrdinalIgnoreCase).ToList(),
            RevokedKeyIds = bundle.Keys.Where(item => item.Status.Equals("revoked", StringComparison.OrdinalIgnoreCase)).Select(item => item.KeyId).Order(StringComparer.OrdinalIgnoreCase).ToList()
        };
        await WriteJsonAtomicAsync(_paths.TrustStatePath, state, ct);
        ApplyTrustAcl();
        return state;
    }

    public async Task<ReleaseTrustState> MarkNoUpdateAsync(
        ReleaseTrustBundle bundle,
        string bundleSha256,
        string rootKeyId,
        string jobId,
        CancellationToken ct)
    {
        Directory.CreateDirectory(_paths.TrustDir);
        ReleaseTrustState state = LoadState();
        state.InstalledBundleVersion = bundle.BundleVersion;
        state.InstalledBundleSha256 = bundleSha256;
        state.InstalledRootKeyId = string.IsNullOrWhiteSpace(state.InstalledRootKeyId) ? rootKeyId : state.InstalledRootKeyId;
        state.LastCheckAt = DateTimeOffset.UtcNow;
        state.LastSuccessAt = DateTimeOffset.UtcNow;
        state.LastError = "";
        state.LastJobId = jobId;
        if (state.ActiveKeyIds.Count == 0 && state.RevokedKeyIds.Count == 0)
        {
            state.ActiveKeyIds = bundle.Keys.Where(item => item.Status.Equals("active", StringComparison.OrdinalIgnoreCase)).Select(item => item.KeyId).Order(StringComparer.OrdinalIgnoreCase).ToList();
            state.RevokedKeyIds = bundle.Keys.Where(item => item.Status.Equals("revoked", StringComparison.OrdinalIgnoreCase)).Select(item => item.KeyId).Order(StringComparer.OrdinalIgnoreCase).ToList();
        }
        await WriteJsonAtomicAsync(_paths.TrustStatePath, state, ct);
        return state;
    }

    public async Task WriteFailureAsync(string errorCode, string message, string jobId, CancellationToken ct)
    {
        Directory.CreateDirectory(_paths.TrustDir);
        ReleaseTrustState state = LoadState();
        state.LastCheckAt = DateTimeOffset.UtcNow;
        state.LastError = $"{errorCode}: {message}";
        state.LastJobId = jobId;
        await WriteJsonAtomicAsync(_paths.TrustStatePath, state, ct);
    }

    private static async Task WriteJsonAtomicAsync<T>(string path, T value, CancellationToken ct)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(value, JsonOptions);
        await NightOwlFileStore.WriteAllBytesAsync(path, bytes, ct);
    }

    private void ApplyTrustAcl()
    {
        if (!_applyAcl)
        {
            return;
        }
        if (!OperatingSystem.IsWindows())
        {
            return;
        }
        try
        {
            _paths.ProtectReleaseTrustDirectories("agent");
        }
        catch
        {
            // Trust installation must remain atomic; NightOwlPaths logs ACL failures with path and scope.
        }
    }
}

public sealed class ReleaseTrustSyncRequest
{
    public string MetadataUrl { get; init; } = "";
    public string BundleUrl { get; init; } = "";
    public string SignatureUrl { get; init; } = "";
    public string ExpectedRootKeyId { get; init; } = "";
    public long? ExpectedBundleVersion { get; init; }
    public string ExpectedSha256 { get; init; } = "";
    public string JobId { get; init; } = "";
}

public sealed class ReleaseTrustSyncResult
{
    public string Status { get; init; } = "failed";
    public string UpdateStatus { get; init; } = "";
    public string ErrorCode { get; init; } = "";
    public string ErrorMessage { get; init; } = "";
    public long InstalledBundleVersion { get; init; }
    public string InstalledBundleSha256 { get; init; } = "";
    public string RootKeyId { get; init; } = "";
    public IReadOnlyList<string> ActiveKeyIds { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> RevokedKeyIds { get; init; } = Array.Empty<string>();
}

public sealed class ReleaseTrustBundleUpdater
{
    private readonly HttpClient _http;
    private readonly ReleaseTrustStore _store;
    private readonly IReadOnlyList<ReleaseTrustRootKey> _trustedRoots;

    public ReleaseTrustBundleUpdater(HttpClient http, ReleaseTrustStore store, IEnumerable<ReleaseTrustRootKey> trustedRoots)
    {
        _http = http;
        _store = store;
        _trustedRoots = trustedRoots.ToList();
    }

    public async Task<ReleaseTrustSyncResult> SyncAsync(ReleaseTrustSyncRequest request, CancellationToken ct)
    {
        try
        {
            byte[] metadataBytes = await _http.GetByteArrayAsync(RequireHttps(request.MetadataUrl, "metadata_url"), ct);
            ReleaseTrustBundleMetadata metadata = ReleaseTrustBundleValidator.ParseMetadata(metadataBytes);
            if (!string.IsNullOrWhiteSpace(request.ExpectedRootKeyId)
                && !metadata.RootKeyId.Equals(request.ExpectedRootKeyId, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"{ReleaseTrustErrorCodes.TrustRootUnknown}: unexpected root_key_id.");
            }
            if (request.ExpectedBundleVersion is not null && metadata.BundleVersion != request.ExpectedBundleVersion.Value)
            {
                throw new InvalidOperationException($"{ReleaseTrustErrorCodes.TrustMetadataInvalid}: unexpected bundle_version.");
            }
            byte[] bundleBytes = await _http.GetByteArrayAsync(RequireHttps(string.IsNullOrWhiteSpace(request.BundleUrl) ? metadata.BundleUrl : request.BundleUrl, "bundle_url"), ct);
            byte[] signatureBytes = await _http.GetByteArrayAsync(RequireHttps(string.IsNullOrWhiteSpace(request.SignatureUrl) ? metadata.SignatureUrl : request.SignatureUrl, "signature_url"), ct);
            if (!string.IsNullOrWhiteSpace(request.ExpectedSha256)
                && !ReleaseTrustBundleValidator.Sha256Hex(bundleBytes).Equals(request.ExpectedSha256.Trim(), StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"{ReleaseTrustErrorCodes.TrustMetadataInvalid}: expected bundle hash mismatch.");
            }

            ReleaseTrustValidationResult validation = ReleaseTrustBundleValidator.Validate(
                bundleBytes,
                signatureBytes,
                metadata,
                _trustedRoots,
                _store.LoadState(),
                _store.LoadCurrentBundle());
            if (!validation.IsValid || validation.Bundle is null)
            {
                throw new InvalidOperationException($"{validation.ErrorCode}: {validation.ErrorMessage}");
            }

            ReleaseTrustState currentState = _store.LoadState();
            if (currentState.InstalledBundleVersion == validation.Bundle.BundleVersion
                && !string.IsNullOrWhiteSpace(currentState.InstalledBundleSha256)
                && validation.BundleSha256.Equals(currentState.InstalledBundleSha256, StringComparison.OrdinalIgnoreCase))
            {
                ReleaseTrustState noUpdateState = await _store.MarkNoUpdateAsync(validation.Bundle, validation.BundleSha256, validation.RootKeyId, request.JobId, ct);
                return new ReleaseTrustSyncResult
                {
                    Status = "completed",
                    UpdateStatus = "no_update",
                    InstalledBundleVersion = noUpdateState.InstalledBundleVersion,
                    InstalledBundleSha256 = noUpdateState.InstalledBundleSha256,
                    RootKeyId = noUpdateState.InstalledRootKeyId,
                    ActiveKeyIds = noUpdateState.ActiveKeyIds,
                    RevokedKeyIds = noUpdateState.RevokedKeyIds
                };
            }

            ReleaseTrustState installedState = await _store.InstallAsync(validation.Bundle, bundleBytes, signatureBytes, metadataBytes, validation.BundleSha256, validation.RootKeyId, request.JobId, ct);
            return new ReleaseTrustSyncResult
            {
                Status = "completed",
                UpdateStatus = "updated",
                InstalledBundleVersion = installedState.InstalledBundleVersion,
                InstalledBundleSha256 = installedState.InstalledBundleSha256,
                RootKeyId = installedState.InstalledRootKeyId,
                ActiveKeyIds = installedState.ActiveKeyIds,
                RevokedKeyIds = installedState.RevokedKeyIds
            };
        }
        catch (Exception ex)
        {
            string code = ex.Message.Split(':', 2)[0];
            if (!code.StartsWith("TRUST_", StringComparison.OrdinalIgnoreCase))
            {
                code = ReleaseTrustErrorCodes.TrustDownloadFailed;
            }
            await _store.WriteFailureAsync(code, ex.Message, request.JobId, ct);
            return new ReleaseTrustSyncResult
            {
                Status = "failed",
                ErrorCode = code,
                ErrorMessage = ex.Message
            };
        }
    }

    private static Uri RequireHttps(string url, string field)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out Uri? uri) || uri.Scheme != Uri.UriSchemeHttps)
        {
            throw new InvalidOperationException($"{ReleaseTrustErrorCodes.TrustMetadataInvalid}: {field} must be HTTPS.");
        }
        return uri;
    }
}

public static class ReleaseTrustAnchors
{
    public static IReadOnlyList<ReleaseTrustRootKey> Load(NightOwlPaths paths)
    {
        List<ReleaseTrustRootKey> roots = new();
        string path = Path.Combine(paths.InstallDir, "release-trust-roots.json");
        if (!File.Exists(path))
        {
            path = Path.Combine(AppContext.BaseDirectory, "release-trust-roots.json");
        }
        if (!File.Exists(path))
        {
            return roots;
        }

        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
        JsonElement root = document.RootElement;
        JsonElement keys = root.ValueKind == JsonValueKind.Array
            ? root
            : root.TryGetProperty("roots", out JsonElement rootsProperty)
                ? rootsProperty
                : root.TryGetProperty("keys", out JsonElement keysProperty)
                    ? keysProperty
                    : default;
        if (keys.ValueKind != JsonValueKind.Array)
        {
            return roots;
        }

        foreach (JsonElement item in keys.EnumerateArray())
        {
            string keyId = item.TryGetProperty("key_id", out JsonElement id) ? id.GetString() ?? "" : "";
            string xml = item.TryGetProperty("public_key_xml", out JsonElement xmlElement) ? xmlElement.GetString() ?? "" : "";
            string algorithm = item.TryGetProperty("algorithm", out JsonElement alg) ? alg.GetString() ?? "RSA-PSS-SHA256" : "RSA-PSS-SHA256";
            bool revoked = item.TryGetProperty("status", out JsonElement status) && (status.GetString() ?? "").Equals("revoked", StringComparison.OrdinalIgnoreCase);
            if (!string.IsNullOrWhiteSpace(keyId) && !string.IsNullOrWhiteSpace(xml))
            {
                roots.Add(new ReleaseTrustRootKey { KeyId = keyId, PublicKeyXml = xml, Algorithm = algorithm, Revoked = revoked });
            }
        }
        return roots;
    }
}
