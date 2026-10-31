using Microsoft.Win32.SafeHandles;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;
using System.Runtime.InteropServices;
using System.Text;

namespace NightOwl.Agent.Windows.Services;

// D.2 engine facade. Consent code is shared with the existing interactive Tray.
public sealed class TerminalIdentity : IDisposable
{
    private readonly TerminalUserIdentity? _identity;
    private readonly SafeAccessTokenHandle? _administrativeToken;
    private readonly TerminalTokenFacts? _administrativeFacts;
    private readonly string? _administrativeProfile;
    private TerminalIdentity(TerminalUserIdentity identity) => _identity = identity;
    private TerminalIdentity(SafeAccessTokenHandle token, TerminalTokenFacts facts, string profile) { _administrativeToken = token; _administrativeFacts = facts; _administrativeProfile = profile; }
    public SafeAccessTokenHandle Token => _identity?.Token ?? _administrativeToken!;
    public TerminalTokenFacts Facts => _identity?.Facts ?? _administrativeFacts!;
    public string Profile => _identity?.Profile ?? _administrativeProfile!;
    public bool IsCaller => _identity?.IsCaller ?? false;
    public bool IsAdministrative => _administrativeToken is not null;
    public static TerminalIdentity FromAdministrativeToken(SafeAccessTokenHandle token)
    {
        try
        {
            var facts = Inspect(token);
            TerminalAdministrativeAuthentication.Validate(facts);
            uint size = 0;
            GetUserProfileDirectory(token, null, ref size);
            if (size is 0 or > 32768) throw new InvalidOperationException("terminal_profile_unavailable");
            var path = new StringBuilder((int)size);
            if (!GetUserProfileDirectory(token, path, ref size)) throw new InvalidOperationException("terminal_profile_unavailable");
            string profile = Path.GetFullPath(path.ToString());
            if (!Directory.Exists(profile)) throw new InvalidOperationException("terminal_profile_unavailable");
            return new TerminalIdentity(token, facts, profile);
        }
        catch { token.Dispose(); throw; }
    }
    public static bool ServiceGateOpen(AgentConfig config)
    {
        using var caller = System.Security.Principal.WindowsIdentity.GetCurrent();
        return !caller.IsSystem || config.TerminalServiceConsentHomologated;
    }
    public static TerminalIdentityPolicy Policy(AgentConfig c) => new()
    {
        MachineId = c.MachineId, TerminalConPtyEnabled = c.TerminalConPtyEnabled,
        TerminalServiceConsentHomologated = c.TerminalServiceConsentHomologated,
        RemoteTerminalTransportEnabled = c.RemoteTerminalTransportEnabled,
        TerminalAuthorizedUserSid = c.TerminalAuthorizedUserSid, TerminalWindowsSessionId = c.TerminalWindowsSessionId,
        TerminalShell = c.TerminalShell
    };
    public static TerminalIdentity Acquire(AgentConfig c, TerminalAuthorization a) => new(TerminalUserIdentity.Acquire(Policy(c), a));
    public static void ValidateFacts(TerminalTokenFacts f, AgentConfig c) => TerminalUserIdentity.ValidateFacts(f, Policy(c));
    public static TerminalTokenFacts CurrentFacts() => TerminalUserIdentity.CurrentFacts();
    public static TerminalTokenFacts Inspect(SafeAccessTokenHandle t) => TerminalUserIdentity.Inspect(t);
    public static string ApproveCurrentUser(AgentConfig c, TerminalAuthorization a, bool confirmed) => TerminalUserIdentity.ApproveCurrentUser(Policy(c), a, confirmed);
    public bool ConsentStillValid() => _identity?.ConsentStillValid() ?? true;
    internal static bool OpenProcessToken(nint p, uint access, out SafeAccessTokenHandle token) => TerminalUserIdentity.OpenProcessToken(p, access, out token);
    public void Dispose() { _identity?.Dispose(); _administrativeToken?.Dispose(); }
    [DllImport("userenv.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetUserProfileDirectory(SafeAccessTokenHandle token, StringBuilder? path, ref uint size);
}
