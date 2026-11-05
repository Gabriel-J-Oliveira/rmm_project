using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Services;

internal static class TerminalAdministrativeAuthenticationTests
{
    internal static void Run()
    {
        var admin = new TerminalTokenFacts("S-1-5-21-1-2-3-1001", 1, 1, 12288, true, true, true);
        TerminalAdministrativeAuthentication.Validate(admin);
        ConPtyTerminal.ValidateChildIdentity(admin, admin, true);
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
        Console.WriteLine("PASS administrative authentication: token policy, nonexistent account and password buffer cleanup.");
    }
}
