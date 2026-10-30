using Microsoft.Win32.SafeHandles;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

// D.2 engine facade. Consent code is shared with the existing interactive Tray.
public sealed class TerminalIdentity : IDisposable
{
    private readonly TerminalUserIdentity _identity;
    private TerminalIdentity(TerminalUserIdentity identity) => _identity = identity;
    public SafeAccessTokenHandle Token => _identity.Token;
    public TerminalTokenFacts Facts => _identity.Facts;
    public string Profile => _identity.Profile;
    public bool IsCaller => _identity.IsCaller;
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
    public bool ConsentStillValid() => _identity.ConsentStillValid();
    internal static bool OpenProcessToken(nint p, uint access, out SafeAccessTokenHandle token) => TerminalUserIdentity.OpenProcessToken(p, access, out token);
    public void Dispose() => _identity.Dispose();
}
