using System.Runtime.InteropServices;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text.Json;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Jobs;

public sealed record ServiceControlOutcome(string State, bool Changed);
public interface IAdministrativeActionNative
{
    bool IsAdministrator { get; }
    ServiceControlOutcome ControlService(string name, string action, CancellationToken ct);
    bool RefreshComputerPolicy();
}

public sealed class AdministrativeActions
{
    private readonly IAdministrativeActionNative _native;
    public AdministrativeActions(IAdministrativeActionNative? native = null) => _native = native ?? new WindowsAdministrativeActionNative();
    public static bool IsService(string id) => id is "start_service" or "stop_service" or "restart_service";
    public static bool IsAccount(string id) => id is "create_local_user" or "set_local_user_enabled" or "reset_local_password";
    public static bool Contains(string id) => IsService(id) || IsAccount(id) || id == "gpupdate";
    private static readonly HashSet<string> ProtectedServices = new(StringComparer.OrdinalIgnoreCase)
    {
        "RpcSs", "RpcEptMapper", "DcomLaunch", "SamSs", "EventLog", "WinDefend", "Sense", "WdNisSvc",
        "SecurityHealthService", "wscsvc", "MpsSvc", "BFE", "Winmgmt", "Schedule", "WinRM", "Netlogon",
        "Dnscache", "Dhcp", "NlaSvc", "nsi", "ProfSvc", "LSM", "UserManager", "Power", "PlugPlay",
        "EventSystem", "Appinfo", "KeyIso", "CryptSvc", "gpsvc", "TrustedInstaller", "WaaSMedicSvc",
        "wuauserv", "TermService", "RemoteRegistry"
    };
    private static readonly HashSet<string> ProtectedUsers = new(StringComparer.OrdinalIgnoreCase)
    { "administrator", "administrador", "guest", "convidado", "defaultaccount", "wdagutilityaccount", "krbtgt", "system", "localservice", "networkservice" };

    public static bool CanAdvertise(AgentConfig config, string action) => config.AdministrativeActionsEnabled &&
        (action == "gpupdate" && config.AllowComputerPolicyRefresh || IsService(action) &&
            config.AllowedServiceOperations is not null && config.AllowedServiceOperations.Any(pair =>
                SafeService(pair.Key) && pair.Value?.Contains(action, StringComparer.Ordinal) == true));

    internal static bool SafeService(string name) => Regex.IsMatch(name, @"\A[A-Za-z0-9_.-]{1,128}\z", RegexOptions.CultureInvariant) &&
        !ProtectedServices.Contains(name) && !name.StartsWith("NightOwl", StringComparison.OrdinalIgnoreCase);

    internal static void Validate(AgentConfig config, string action, JsonElement parameters)
    {
        if (!config.AdministrativeActionsEnabled) throw new InvalidOperationException("administrative_actions_disabled");
        if (IsService(action))
        {
            var field = parameters.GetProperty("service_name");
            if (field.ValueKind != JsonValueKind.String || !SafeService(field.GetString()!)) throw new InvalidOperationException("protected_or_invalid_service");
            var matches = config.AllowedServiceOperations?.Where(pair => pair.Key.Equals(field.GetString(), StringComparison.OrdinalIgnoreCase)).ToArray();
            if (matches is null || matches.Length != 1 || matches[0].Value?.Contains(action, StringComparer.Ordinal) != true)
                throw new InvalidOperationException("service_operation_not_authorized");
        }
        else if (action == "gpupdate")
        {
            if (!config.AllowComputerPolicyRefresh) throw new InvalidOperationException("policy_refresh_not_authorized");
        }
        else
        {
            var name = parameters.GetProperty("user_name");
            if (name.ValueKind != JsonValueKind.String || !Regex.IsMatch(name.GetString()!, @"\A[A-Za-z][A-Za-z0-9_-]{0,19}\z") || ProtectedUsers.Contains(name.GetString()!))
                throw new InvalidOperationException("protected_or_invalid_local_account");
            if (parameters.TryGetProperty("account_sid", out var sid))
            {
                if (sid.ValueKind != JsonValueKind.String || !Regex.IsMatch(sid.GetString()!, @"\AS-1-5-21-(?:[0-9]{1,10}-){3}[0-9]{1,10}\z") ||
                    sid.GetString()!.Split('-').Skip(4).Any(part => !uint.TryParse(part, out _)) ||
                    !uint.TryParse(sid.GetString()!.Split('-')[^1], out uint rid) || rid <= 1000)
                    throw new InvalidOperationException("protected_or_invalid_account_sid");
            }
            if (parameters.TryGetProperty("enabled", out var enabled) && enabled.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                throw new InvalidOperationException("invalid_account_enabled");
            if (parameters.TryGetProperty("secret_reference", out var secret) && (secret.ValueKind != JsonValueKind.String ||
                    !Guid.TryParseExact(secret.GetString(), "D", out Guid reference) || reference.ToString("D") != secret.GetString()))
                throw new InvalidOperationException("invalid_secret_reference");
            throw new InvalidOperationException(action == "set_local_user_enabled" ? "local_account_isolation_unavailable" : "protected_secret_delivery_unavailable");
        }
    }

    public object Execute(AgentConfig config, AgentJobRequest job, CancellationToken ct)
    {
        string action = OperationalActions.Validate(config, job).Id;
        if (!Contains(action) || IsAccount(action)) throw new InvalidOperationException("admin_action_blocked");
        ct.ThrowIfCancellationRequested();
        try
        {
            if (!_native.IsAdministrator) throw new InvalidOperationException("action_admin_required");
            if (action == "gpupdate")
            {
                if (!_native.RefreshComputerPolicy()) throw new InvalidOperationException();
                ct.ThrowIfCancellationRequested();
                return new { schema_version = 1, action_id = action, action_version = 1, success = true,
                    request_accepted = true, processing_confirmed = false };
            }
            using var document = JsonDocument.Parse(JsonSerializer.Serialize(job.Payload));
            string name = document.RootElement.GetProperty("parameters").GetProperty("service_name").GetString()!;
            ServiceControlOutcome result = _native.ControlService(name, action, ct);
            ct.ThrowIfCancellationRequested();
            if (result.State != (action == "stop_service" ? "Stopped" : "Running")) throw new InvalidOperationException();
            return new { schema_version = 1, action_id = action, action_version = 1, success = true,
                service_name = name, state = result.State, changed = result.Changed };
        }
        catch (OperationCanceledException) { throw; }
        catch (InvalidOperationException error) when (error.Message == "action_admin_required") { throw; }
        catch (Exception) { throw new InvalidOperationException("admin_native_outcome_unconfirmed"); }
    }
}

internal sealed class WindowsAdministrativeActionNative : IAdministrativeActionNative
{
    public bool IsAdministrator
    {
        get { using WindowsIdentity identity = WindowsIdentity.GetCurrent(); return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator); }
    }
    public bool RefreshComputerPolicy() => RefreshPolicyEx(true, 1); // Computer policy, fixed RP_FORCE; request only.

    public ServiceControlOutcome ControlService(string name, string action, CancellationToken ct)
    {
        if (!AdministrativeActions.SafeService(name)) throw new InvalidOperationException();
        using ServiceController service = new(name, ".");
        if (!service.ServiceName.Equals(name, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException();
        // No drivers, shared Windows hosts, protected launches, Windows binaries or command wrappers.
        if (!QueryServiceConfig2(service.ServiceHandle.DangerousGetHandle(), 12, out uint protection, 4, out _))
            throw new InvalidOperationException();
        using RegistryKey? key = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Services\" + name);
        string image = key?.GetValue("ImagePath", null, RegistryValueOptions.DoNotExpandEnvironmentNames) as string ?? "";
        ValidateResource(service.ServiceType, protection, image);
        var state = service.Status;
        if (state is not (ServiceControllerStatus.Running or ServiceControllerStatus.Stopped)) throw new InvalidOperationException();
        if (action == "restart_service" && state != ServiceControllerStatus.Running) throw new InvalidOperationException();
        bool changed = false;
        if (action is "stop_service" or "restart_service" && state == ServiceControllerStatus.Running)
        {
            CheckDependencies(service.DependentServices, expectedRunning: false);
            ct.ThrowIfCancellationRequested();
            service.Stop(stopDependentServices: false);
            Wait(service, ServiceControllerStatus.Stopped, ct);
            changed = true;
        }
        if (action is "start_service" or "restart_service" && service.Status == ServiceControllerStatus.Stopped)
        {
            CheckDependencies(service.ServicesDependedOn, expectedRunning: true);
            ct.ThrowIfCancellationRequested();
            service.Start();
            Wait(service, ServiceControllerStatus.Running, ct);
            changed = true;
        }
        return new(service.Status.ToString(), changed);
    }
    internal static void ValidateResource(ServiceType type, uint protection, string image)
    {
        if (type != ServiceType.Win32OwnProcess || protection != 0) throw new InvalidOperationException();
        if (image.StartsWith('"') && image.EndsWith('"')) image = image[1..^1];
        else if (image.Any(char.IsWhiteSpace)) throw new InvalidOperationException();
        if (!Path.IsPathFullyQualified(image) || !Regex.IsMatch(image, @"\A[A-Za-z]:\\") || image.Contains('"') || image.Contains('%') || !image.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException();
        string path = Path.GetFullPath(image), windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows).TrimEnd('\\');
        if (new DriveInfo(Path.GetPathRoot(path)!).DriveType != DriveType.Fixed) throw new InvalidOperationException();
        if (path.StartsWith(windows + "\\", StringComparison.OrdinalIgnoreCase) || !File.Exists(path)) throw new InvalidOperationException();
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException();
        for (DirectoryInfo? parent = Directory.GetParent(path); parent is not null; parent = parent.Parent)
            if ((parent.Attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException();
        using SafeFileHandle file = File.OpenHandle(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        StringBuilder final = new(1024);
        uint length = GetFinalPathNameByHandle(file, final, (uint)final.Capacity, 0);
        if (length == 0 || length >= final.Capacity) throw new InvalidOperationException();
        string normalized = final.ToString();
        if (normalized.StartsWith(@"\\?\", StringComparison.Ordinal)) normalized = normalized[4..];
        if (normalized.StartsWith(windows + "\\", StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException();
    }
    private static void CheckDependencies(ServiceController[] services, bool expectedRunning)
    {
        try { if (services.Any(service => service.Status != (expectedRunning ? ServiceControllerStatus.Running : ServiceControllerStatus.Stopped))) throw new InvalidOperationException(); }
        finally { foreach (var service in services) service.Dispose(); }
    }
    private static void Wait(ServiceController service, ServiceControllerStatus expected, CancellationToken ct)
    {
        while (true)
        {
            ct.ThrowIfCancellationRequested(); service.Refresh();
            if (service.Status == expected) return;
            if (ct.WaitHandle.WaitOne(50)) ct.ThrowIfCancellationRequested();
        }
    }
    [DllImport("userenv.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool RefreshPolicyEx([MarshalAs(UnmanagedType.Bool)] bool machine, uint options);
    [DllImport("advapi32.dll", EntryPoint = "QueryServiceConfig2W", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool QueryServiceConfig2(nint service, uint level, out uint value, uint size, out uint needed);
    [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path, uint length, uint flags);
}
