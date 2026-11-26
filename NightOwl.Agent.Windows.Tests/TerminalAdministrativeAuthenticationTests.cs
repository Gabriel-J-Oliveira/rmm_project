using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Services;
using System.Security.Principal;

internal static class TerminalAdministrativeAuthenticationTests
{
    internal static void Run()
    {
        if (TerminalAdministrativeAuthentication.NormalizeDomain("") is not null ||
            TerminalAdministrativeAuthentication.NormalizeDomain(" ") is not null ||
            TerminalAdministrativeAuthentication.NormalizeDomain(".") != "." ||
            TerminalAdministrativeAuthentication.NormalizeDomain("CONTROL") != "CONTROL" ||
            TerminalAdministrativeAuthentication.NormalizeDomain("other.example") != "other.example" ||
            TerminalAdministrativeAuthentication.RejectionReason("terminal_auth_failed") != "credentials_rejected" ||
            TerminalAdministrativeAuthentication.RejectionReason("terminal_auth_not_administrator") != "administrator_required" ||
            TerminalAdministrativeAuthentication.RejectionReason("terminal_start_failed") is not null)
            throw new Exception("Windows authentication domain normalization failed");
        var admin = new TerminalTokenFacts("S-1-5-21-1-2-3-1001", 1, 1, 12288, true, true, true);
        TerminalAdministrativeAuthentication.Validate(admin);
        ConPtyTerminal.ValidateChildIdentity(admin, admin, true);
        var headlessAdmin = admin with { SessionId = 0, Interactive = false };
        TerminalAdministrativeAuthentication.Validate(headlessAdmin);
        ConPtyTerminal.ValidateChildIdentity(headlessAdmin, headlessAdmin, true);
        foreach (var invalid in new[] { admin with { Sid = "S-1-5-18" }, admin with { LogonId = 2 }, admin with { Elevated = false } })
        {
            try { ConPtyTerminal.ValidateChildIdentity(admin, invalid, true); throw new Exception("Invalid process identity accepted"); }
            catch (InvalidOperationException) { }
        }
        foreach (var invalid in new[] { admin with { Administrator = false }, admin with { Elevated = false },
            admin with { Integrity = 8192 }, admin with { Sid = "S-1-5-18" } })
        {
            try { TerminalAdministrativeAuthentication.Validate(invalid); throw new Exception("Invalid token accepted"); }
            catch (InvalidOperationException) { }
        }
        char[] secret = "synthetic-password".ToCharArray();
        try { using var token = TerminalAdministrativeAuthentication.Authenticate("nightowl-nonexistent-" + Guid.NewGuid().ToString("N"), ".", secret); throw new Exception("Invalid credentials accepted"); }
        catch (InvalidOperationException) { }
        if (secret.Any(c => c != '\0')) throw new Exception("Password buffer not cleared");
        using var current = WindowsIdentity.GetCurrent();
        string tokenAccount = TerminalIdentity.TokenAccountName(current.AccessToken, current.User?.Value ?? "");
        if (string.IsNullOrWhiteSpace(tokenAccount) || tokenAccount != current.Name)
            throw new Exception("Authenticated account name was not derived from the Windows token");
        Console.WriteLine("PASS administrative authentication: token policy, domain/UPN normalization, nonexistent account and password buffer cleanup.");
    }
}
