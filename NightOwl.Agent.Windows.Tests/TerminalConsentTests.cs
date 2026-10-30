using System.Text.Json;
using NightOwl.Agent.Shared;
using System.Security.AccessControl;
using System.Security.Principal;

internal static class TerminalConsentTests
{
    internal static void SecureFixture(string root)
    {
        string full = Path.GetFullPath(root), temp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar);
        if (!string.Equals(Path.GetDirectoryName(full), temp, StringComparison.OrdinalIgnoreCase) ||
            !(Path.GetFileName(full).StartsWith("nightowl-consent-") || Path.GetFileName(full).StartsWith("nightowl-browser-agent-")))
            throw new InvalidOperationException("Non-test ACL target rejected");
        Directory.CreateDirectory(root);
        var acl = new DirectorySecurity(); acl.SetAccessRuleProtection(true, false);
        foreach (string sid in new[] { TerminalUserIdentity.CurrentFacts().Sid, NightOwlPaths.SystemSid, NightOwlPaths.AdministratorsSid })
            acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid), FileSystemRights.FullControl,
                InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        new DirectoryInfo(root).SetAccessControl(acl);
    }
    private static void Check(bool value) { if (!value) throw new Exception("Consent assertion failed"); }
    internal static void Run()
    {
        string root = Path.Combine(Path.GetTempPath(), "nightowl-consent-" + Guid.NewGuid().ToString("N"));
        var paths = new NightOwlPaths(root);
        var facts = TerminalUserIdentity.CurrentFacts();
        var policy = new TerminalIdentityPolicy { MachineId = Guid.NewGuid().ToString(), RemoteTerminalTransportEnabled = true,
            TerminalConPtyEnabled = true, TerminalAuthorizedUserSid = facts.Sid, TerminalWindowsSessionId = facts.SessionId };
        var auth = new TerminalAuthorization(Guid.NewGuid(), policy.MachineId, "isolated-operator", DateTimeOffset.UtcNow.AddMinutes(2));
        var proposal = new TerminalConsentRequest(policy, auth, "Synthetic operator", "Synthetic endpoint", "https://isolated.invalid", DateTimeOffset.UtcNow.AddSeconds(60));
        try
        {
            SecureFixture(root);
            proposal.Write(paths);
            Check(TerminalConsentRequest.ReadTrusted(paths).Authorization == auth);
            Check(!TerminalUserIdentity.SameLogon(facts, null));
            Check(!TerminalUserIdentity.SameLogon(facts, facts with { LogonId = facts.LogonId + 1 }));
            Check(!TerminalUserIdentity.SameLogon(facts, facts with { SessionId = facts.SessionId + 1 }));
            Check(!policy.TerminalServiceConsentHomologated);
            var directory = new DirectoryInfo(paths.LogsDir);
            var trustedAcl = directory.GetAccessControl();
            var unsafeAcl = new DirectorySecurity(); unsafeAcl.SetSecurityDescriptorBinaryForm(trustedAcl.GetSecurityDescriptorBinaryForm(), AccessControlSections.Access);
            unsafeAcl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.WorldSid, null), FileSystemRights.Write, AccessControlType.Allow));
            directory.SetAccessControl(unsafeAcl);
            try { TerminalConsentRequest.ReadTrusted(paths); throw new Exception("Writable request origin accepted"); } catch (InvalidOperationException) { }
            finally
            {
                var restored = new DirectorySecurity();
                restored.SetSecurityDescriptorBinaryForm(trustedAcl.GetSecurityDescriptorBinaryForm(), AccessControlSections.Access);
                directory.SetAccessControl(restored);
            }
            Check(TerminalUserIdentity.ReadDecision(policy, auth).Decision == "pending");
            TerminalUserIdentity.DenyCurrentUser(policy, auth);
            Check(TerminalUserIdentity.ReadDecision(policy, auth).Decision == "denied");
            TerminalUserIdentity.RevokeCurrentUser(policy, auth.SessionId);
            TerminalUserIdentity.ApproveCurrentUser(policy, auth, true);
            Check(TerminalUserIdentity.ReadDecision(policy, auth).Decision == "approved");
            using (var identity = TerminalUserIdentity.Acquire(policy, auth))
            {
                Check(identity.ConsentStillValid());
                TerminalUserIdentity.RevokeCurrentUser(policy, auth.SessionId);
                Check(!identity.ConsentStillValid());
            }
            string path = TerminalConsentRequest.PathFor(paths);
            var decoded = JsonSerializer.Deserialize<TerminalConsentRequest>(File.ReadAllBytes(path))!;
            File.WriteAllBytes(path, JsonSerializer.SerializeToUtf8Bytes(decoded with { OperatorName = "Forged operator" }));
            try { TerminalConsentRequest.ReadTrusted(paths); throw new Exception("Tampered request accepted"); } catch (InvalidOperationException) { }
            (proposal with { Deadline = DateTimeOffset.UtcNow.AddSeconds(-1) }).Write(paths);
            Check(!TerminalConsentRequest.ReadTrusted(paths).IsPending);
            (proposal with { ServerOrigin = "file://localhost/not-a-backend" }).Write(paths);
            try { TerminalConsentRequest.ReadTrusted(paths); throw new Exception("Non-HTTP origin accepted"); } catch (InvalidOperationException) { }
            Console.WriteLine("PASS consent: same-user DPAPI/ACL origin, pending/deny/approve, bound receipt, revoke, tamper and expiry; no UI click automated.");
        }
        finally
        {
            TerminalUserIdentity.RevokeCurrentUser(policy, auth.SessionId);
            string resolved = Path.GetFullPath(root), temp = Path.GetFullPath(Path.GetTempPath());
            if (!resolved.StartsWith(temp, StringComparison.OrdinalIgnoreCase) || !Path.GetFileName(resolved).StartsWith("nightowl-consent-")) throw new Exception("Unsafe cleanup target");
            if (Directory.Exists(resolved)) Directory.Delete(resolved, true);
        }
    }
}
