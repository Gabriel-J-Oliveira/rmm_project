using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Jobs;

public sealed record ApprovedScriptExecution(object Output, string Stdout, string Stderr);
internal sealed class ScriptExecutionFailure(string code, int exitCode) : Exception(code)
{
    internal int ExitCode { get; } = exitCode;
}

/// <summary>One compiled, reviewed diagnostic. No remote content, path, shell or upload.</summary>
public static class ApprovedScripts
{
    public const string Id = "environment_diagnostic";
    public const int Version = 1;
    public const string Sha256 = "8791bd2ddb422912abfa6c8909062b7c386fc5645bd7fc801992c4f816293521";
    public const string Runtime = "windows_powershell_5_1";
    public const int MaxOutputBytes = 4096;
    public static string Capability => $"approved_script:{Id}:{Version}:{Sha256}";
    public static bool Enabled(AgentConfig config) => config.ApprovedScriptsEnabled &&
        config.AllowedScriptVersions?.Contains($"{Id}:{Version}", StringComparer.Ordinal) == true;

    public static void Validate(AgentConfig config, JsonElement value)
    {
        if (!Enabled(config)) throw new InvalidOperationException("approved_scripts_disabled");
        if (value.ValueKind != JsonValueKind.Object || value.EnumerateObject().Count() != 4 ||
            !value.TryGetProperty("script_id", out var id) || id.ValueKind != JsonValueKind.String || id.GetString() != Id ||
            !value.TryGetProperty("script_version", out var version) || version.ValueKind != JsonValueKind.Number || !version.TryGetInt32(out int v) || v != Version ||
            !value.TryGetProperty("sha256", out var hash) || hash.ValueKind != JsonValueKind.String || hash.GetString() != Sha256 ||
            !value.TryGetProperty("parameters", out var parameters) || parameters.ValueKind != JsonValueKind.Object || parameters.EnumerateObject().Any())
            throw new InvalidOperationException("invalid_script_reference");
        _ = Content();
    }

    internal static string Content()
    {
        using Stream stream = typeof(ApprovedScripts).Assembly.GetManifestResourceStream("NightOwl.ApprovedScripts.environment_diagnostic_v1.ps1")
            ?? throw new InvalidOperationException("script_content_unavailable");
        using MemoryStream bytes = new();
        stream.CopyTo(bytes);
        byte[] content = bytes.ToArray();
        if (Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant() != Sha256)
            throw new InvalidOperationException("script_integrity_mismatch");
        return Encoding.UTF8.GetString(content);
    }

    public static async Task<ApprovedScriptExecution> ExecuteAsync(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        OperationalActions.Validate(config, job);
        try
        {
            string runtime = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell", "v1.0", "powershell.exe");
            // Only the OS runtime and this embedded content can reach the process helper.
            var capture = await OwnedProcess.CaptureAsync(runtime,
                new[] { "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-" }, Content() + "\n", MaxOutputBytes, ct);
            return Confirm(capture);
        }
        catch (OperationCanceledException) { throw; }
        catch (ScriptExecutionFailure) { throw; }
        catch (Exception) { throw new InvalidOperationException("script_execution_failed"); }
    }

    internal static ApprovedScriptExecution Confirm(OwnedProcess.Capture capture)
    {
        if (capture.OutputLimitReached) throw new ScriptExecutionFailure("SCRIPT_OUTPUT_LIMIT", capture.ExitCode);
        if (capture.ExitCode != 0 || !string.IsNullOrWhiteSpace(capture.Stderr))
            throw new ScriptExecutionFailure("SCRIPT_PROCESS_FAILED", capture.ExitCode);
        try
        {
            using JsonDocument document = JsonDocument.Parse(capture.Stdout);
            JsonElement diagnostic = document.RootElement;
            if (diagnostic.ValueKind != JsonValueKind.Object || diagnostic.EnumerateObject().Count() != 2 ||
                !diagnostic.TryGetProperty("utc", out var utc) || utc.ValueKind != JsonValueKind.String ||
                !Regex.IsMatch(utc.GetString()!, @"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{7}Z\z", RegexOptions.CultureInvariant) ||
                !DateTimeOffset.TryParse(utc.GetString(), System.Globalization.CultureInfo.InvariantCulture,
                    System.Globalization.DateTimeStyles.None, out _) ||
                !diagnostic.TryGetProperty("powershell_version", out var ps) || ps.ValueKind != JsonValueKind.String ||
                !Regex.IsMatch(ps.GetString()!, @"\A5\.1(?:\.\d{1,10}){0,2}\z", RegexOptions.CultureInvariant))
                throw new ScriptExecutionFailure("SCRIPT_OUTPUT_INVALID", capture.ExitCode);
            // Re-serialize only the permitted values; never persist raw library/runtime errors.
            var safe = new { utc = utc.GetString(), powershell_version = ps.GetString() };
            return new(new { schema_version = 1, action_id = "run_approved_script", action_version = 1, success = true,
                script_id = Id, script_version = Version, sha256 = Sha256, runtime = Runtime, diagnostic = safe },
                JsonSerializer.Serialize(safe), "");
        }
        catch (JsonException) { throw new ScriptExecutionFailure("SCRIPT_OUTPUT_INVALID", capture.ExitCode); }
    }
}
