using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;

namespace NightOwl.Agent.Shared;

/// <summary>Public metadata only. Trust is the service-owned RX projection, or a same-user DPAPI proof.</summary>
public sealed record TerminalConsentRequest(TerminalIdentityPolicy Policy, TerminalAuthorization Authorization,
    string OperatorName, string EndpointName, string ServerOrigin, DateTimeOffset Deadline, string UserProof = "")
{
    public static string PathFor(NightOwlPaths paths) => Path.Combine(paths.LogsDir, "terminal-consent-request.json");
    private static readonly JsonSerializerOptions Json = new() { UnmappedMemberHandling = System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow };
    private byte[] Digest() => SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(this with { UserProof = "" }));

    public void Write(NightOwlPaths paths)
    {
        var caller = TerminalUserIdentity.CurrentFacts();
        TerminalConsentRequest request = this;
        if (caller.Sid != NightOwlPaths.SystemSid)
        {
            TerminalUserIdentity.ValidateFacts(caller, Policy);
            request = this with { UserProof = Convert.ToBase64String(TerminalUserIdentity.Protect(Digest(), true)) };
        }
        string target = PathFor(paths);
        Directory.CreateDirectory(paths.LogsDir);
        string temporary = target + ".tmp";
        File.WriteAllBytes(temporary, JsonSerializer.SerializeToUtf8Bytes(request, Json));
        File.Move(temporary, target, true);
    }

    public static TerminalConsentRequest ReadTrusted(NightOwlPaths paths)
    {
        string path = PathFor(paths);
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 ||
            (File.GetAttributes(paths.LogsDir) & FileAttributes.ReparsePoint) != 0 ||
            (File.GetAttributes(paths.Root) & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("terminal_request_untrusted");
        using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
        if (file.Length is < 1 or > 16384) throw new InvalidOperationException("terminal_request_invalid");
        byte[] data = new byte[(int)file.Length]; file.ReadExactly(data);
        var request = JsonSerializer.Deserialize<TerminalConsentRequest>(data, Json) ?? throw new InvalidOperationException("terminal_request_invalid");
        var current = TerminalUserIdentity.CurrentFacts();
        TerminalUserIdentity.ValidateFacts(current, request.Policy);
        if (request.Authorization.MachineId != request.Policy.MachineId || request.Authorization.SessionId == Guid.Empty ||
            string.IsNullOrEmpty(request.Authorization.OperatorId) || request.Deadline > request.Authorization.ExpiresAt ||
            request.Authorization.ExpiresAt > DateTimeOffset.UtcNow.AddMinutes(5) || request.OperatorName.Length > 150 || request.EndpointName.Length > 255 ||
            !Uri.TryCreate(request.ServerOrigin, UriKind.Absolute, out var origin) || origin.UserInfo != "" ||
            origin.Scheme != "https" && !(origin.Scheme == "http" && origin.IsLoopback))
            throw new InvalidOperationException("terminal_request_invalid");
        var acl = new FileInfo(path).GetAccessControl();
        string owner = (acl.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier)?.Value ?? throw new InvalidOperationException("terminal_request_untrusted");
        bool self = owner == current.Sid;
        if (owner != NightOwlPaths.SystemSid && owner != NightOwlPaths.AdministratorsSid && !self) throw new InvalidOperationException("terminal_request_untrusted");
        foreach (var security in new FileSystemSecurity[] { acl, new DirectoryInfo(paths.LogsDir).GetAccessControl(), new DirectoryInfo(paths.Root).GetAccessControl() })
        foreach (FileSystemAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier)))
        {
            if ((rule.PropagationFlags & PropagationFlags.InheritOnly) != 0) continue;
            var write = FileSystemRights.Write | FileSystemRights.Delete | FileSystemRights.ChangePermissions | FileSystemRights.TakeOwnership;
            if (rule.AccessControlType == AccessControlType.Allow && (rule.FileSystemRights & write) != 0 &&
                rule.IdentityReference.Value != NightOwlPaths.SystemSid && rule.IdentityReference.Value != NightOwlPaths.AdministratorsSid &&
                !(self && rule.IdentityReference.Value == current.Sid)) throw new InvalidOperationException("terminal_request_untrusted");
        }
        if (self && (request.UserProof == "" || !CryptographicOperations.FixedTimeEquals(request.Digest(),
            TerminalUserIdentity.Protect(Convert.FromBase64String(request.UserProof), false)))) throw new InvalidOperationException("terminal_request_untrusted");
        return request;
    }

    [System.Text.Json.Serialization.JsonIgnore]
    public bool IsPending => Deadline > DateTimeOffset.UtcNow && Authorization.ExpiresAt > DateTimeOffset.UtcNow;
}
