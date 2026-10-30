using System.Collections.ObjectModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text.Json;
using System.Text.RegularExpressions;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Jobs;

public sealed record OperationalActionDefinition(string Id, int Version, bool RequiresAdministrator, IReadOnlyList<string> Parameters);
public sealed record OperationalActionRequest(string Id, int Version, string? ServiceName);

public interface IOperationalActionNative
{
    bool IsAdministrator { get; }
    void FlushDns();
    string QueryService(string name);
}

/// <summary>Closed native actions. No executable, script, or command is accepted.</summary>
public sealed class OperationalActions
{
    public const int MaxTimeoutSeconds = 15;
    public static IReadOnlyDictionary<string, OperationalActionDefinition> Catalog { get; } =
        new ReadOnlyDictionary<string, OperationalActionDefinition>(new Dictionary<string, OperationalActionDefinition>(StringComparer.Ordinal)
        {
            ["flush_dns"] = new("flush_dns", 1, true, Array.Empty<string>()),
            ["query_service"] = new("query_service", 1, false, Array.AsReadOnly(new[] { "service_name" })),
            ["run_approved_script"] = new("run_approved_script", 1, false, Array.AsReadOnly(new[] { "script_id", "script_version", "sha256", "parameters" }))
        });
    private readonly IOperationalActionNative _native;
    public OperationalActions(IOperationalActionNative? native = null) => _native = native ?? new WindowsOperationalActionNative();

    public static OperationalActionRequest Validate(AgentConfig config, AgentJobRequest job)
    {
        JsonElement p = JsonSerializer.SerializeToElement(job.Payload);
        if (p.ValueKind != JsonValueKind.Object || p.EnumerateObject().Count() != 3 ||
            !p.TryGetProperty("action_id", out var id) || id.ValueKind != JsonValueKind.String ||
            !p.TryGetProperty("action_version", out var version) || version.ValueKind != JsonValueKind.Number ||
            !version.TryGetInt32(out int v) || !p.TryGetProperty("parameters", out var parameters) || parameters.ValueKind != JsonValueKind.Object)
            throw new InvalidOperationException("invalid_action_envelope");
        string action = id.GetString()!;
        if (!Catalog.TryGetValue(action, out var definition)) throw new InvalidOperationException("unknown_action");
        if (v != definition.Version) throw new InvalidOperationException("unsupported_action_version");
        if (!config.OperationalActionsEnabled || !config.AllowedOperationalActions.Contains(action, StringComparer.Ordinal))
            throw new InvalidOperationException("action_not_authorized");
        if (!parameters.EnumerateObject().Select(x => x.Name).Order().SequenceEqual(definition.Parameters.Order()))
            throw new InvalidOperationException("invalid_action_parameters");
        string? name = null;
        if (action == "query_service")
        {
            var field = parameters.GetProperty("service_name");
            if (field.ValueKind != JsonValueKind.String || !Regex.IsMatch(field.GetString()!, @"\A[A-Za-z0-9_.-]{1,256}\z", RegexOptions.CultureInvariant))
                throw new InvalidOperationException("invalid_service_name");
            name = field.GetString();
        }
        if (action == "run_approved_script") ApprovedScripts.Validate(config, parameters);
        return new(action, v, name);
    }

    public static IEnumerable<string> Capabilities(AgentConfig config) =>
        config.OperationalActionsEnabled && config.AllowedJobTypes.Contains("operational_action", StringComparer.Ordinal)
            ? Catalog.Values.Where(x => config.AllowedOperationalActions.Contains(x.Id, StringComparer.Ordinal) &&
                    (x.Id != "run_approved_script" || ApprovedScripts.Enabled(config)))
                .Select(x => $"operational_action:{x.Id}:{x.Version}")
                .Concat(config.AllowedOperationalActions.Contains("run_approved_script", StringComparer.Ordinal) && ApprovedScripts.Enabled(config)
                    ? new[] { ApprovedScripts.Capability } : Array.Empty<string>()) : Array.Empty<string>();

    public object Execute(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        var request = Validate(config, job);
        if (request.Id == "run_approved_script") throw new InvalidOperationException("script_requires_async_executor");
        ct.ThrowIfCancellationRequested();
        try
        {
            if (Catalog[request.Id].RequiresAdministrator && !_native.IsAdministrator)
                throw new InvalidOperationException("action_admin_required");
            if (request.Id == "flush_dns")
            {
                _native.FlushDns();
                ct.ThrowIfCancellationRequested();
                return new { schema_version = 1, action_id = request.Id, action_version = request.Version, success = true };
            }
            string state = _native.QueryService(request.ServiceName!);
            if (state is not ("Stopped" or "StartPending" or "StopPending" or "Running" or "ContinuePending" or "PausePending" or "Paused"))
                throw new InvalidOperationException("action_native_failed");
            ct.ThrowIfCancellationRequested();
            return new { schema_version = 1, action_id = request.Id, action_version = request.Version,
                success = true, service_name = request.ServiceName, state };
        }
        catch (OperationCanceledException) { throw; }
        catch (InvalidOperationException ex) when (ex.Message == "action_admin_required") { throw; }
        catch (Exception) { throw new InvalidOperationException("action_native_failed"); }
    }
}

internal sealed class WindowsOperationalActionNative : IOperationalActionNative
{
    public bool IsAdministrator
    {
        get
        {
            using WindowsIdentity identity = WindowsIdentity.GetCurrent();
            return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
        }
    }
    public void FlushDns()
    {
        if (!DnsFlushResolverCache()) throw new InvalidOperationException("action_native_failed");
    }
    public string QueryService(string name)
    {
        // Local SCM only, query status access only; no configuration/account/path disclosure.
        using ServiceController service = new(name, ".");
        return service.Status.ToString();
    }
    [DllImport("dnsapi.dll", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DnsFlushResolverCache();
}
