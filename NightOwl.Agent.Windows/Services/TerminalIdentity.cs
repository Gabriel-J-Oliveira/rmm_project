using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed record TerminalAuthorization(Guid SessionId, string MachineId, string OperatorId, DateTimeOffset ExpiresAt);
public sealed record TerminalTokenFacts(string Sid, int SessionId, long LogonId, int Integrity, bool Elevated, bool Administrator, bool Interactive, bool DangerousPrivileges = false);

/// <summary>Explicit target + per-session user DPAPI consent. Never selects an active user implicitly.</summary>
public sealed class TerminalIdentity : IDisposable
{
    public SafeAccessTokenHandle Token { get; }
    public TerminalTokenFacts Facts { get; }
    public string Profile { get; }
    public bool IsCaller { get; }
    private string? _consumedPath;
    private byte[]? _consumedHash;
    private DateTimeOffset _consentExpires;
    private TerminalIdentity(SafeAccessTokenHandle token, TerminalTokenFacts facts, bool caller)
    {
        Token = token; Facts = facts; IsCaller = caller;
        uint size = 1024; StringBuilder path = new((int)size);
        if (!GetUserProfileDirectory(token, path, ref size)) throw new InvalidOperationException("terminal_profile_unavailable");
        Profile = Path.GetFullPath(path.ToString());
    }

    public static void ValidateFacts(TerminalTokenFacts facts, AgentConfig config)
    {
        if (!config.TerminalConPtyEnabled || !config.RemoteTerminalTransportEnabled || config.TerminalShell != "powershell51" ||
            string.IsNullOrEmpty(config.TerminalAuthorizedUserSid) || config.TerminalWindowsSessionId is null or <= 0 ||
            facts.Sid != config.TerminalAuthorizedUserSid || facts.SessionId != config.TerminalWindowsSessionId ||
            facts.Sid is "S-1-5-18" or "S-1-5-19" or "S-1-5-20" || facts.Integrity > 8192 ||
            facts.Elevated || facts.Administrator || !facts.Interactive || facts.DangerousPrivileges)
            throw new InvalidOperationException("terminal_identity_not_authorized");
    }

    public static TerminalTokenFacts Inspect(SafeAccessTokenHandle token)
    {
        nint Read(int kind)
        {
            GetTokenInformation(token, kind, 0, 0, out int size);
            nint data = Marshal.AllocHGlobal(size);
            if (!GetTokenInformation(token, kind, data, size, out _)) { Marshal.FreeHGlobal(data); throw new InvalidOperationException("terminal_token_unavailable"); }
            return data;
        }
        int Integer(int kind) { nint p = Read(kind); try { return Marshal.ReadInt32(p); } finally { Marshal.FreeHGlobal(p); } }
        nint user = Read(1), integrity = Read(25), statistics = Read(10), privileges = Read(3);
        try
        {
            using WindowsIdentity identity = new(token.DangerousGetHandle());
            string sid = new SecurityIdentifier(Marshal.ReadIntPtr(user)).Value;
            string level = new SecurityIdentifier(Marshal.ReadIntPtr(integrity)).Value;
            // TOKEN_STATISTICS: TokenId LUID, AuthenticationId LUID.
            long logon = Marshal.ReadInt64(statistics, 8);
            bool Member(string group)
            {
                // CheckTokenMembership requires an impersonation token when supplied explicitly.
                if (!DuplicateTokenEx(token, 0x0008, 0, 2, 2, out var duplicate)) throw new InvalidOperationException("terminal_token_unavailable");
                using (duplicate)
                {
                    var binary = new byte[new SecurityIdentifier(group).BinaryLength];
                    new SecurityIdentifier(group).GetBinaryForm(binary, 0);
                    if (!CheckTokenMembership(duplicate, binary, out bool member)) throw new InvalidOperationException("terminal_token_unavailable");
                    return member;
                }
            }
            bool dangerous = false;
            var permitted = new HashSet<string>(StringComparer.Ordinal) { "SeChangeNotifyPrivilege", "SeIncreaseWorkingSetPrivilege", "SeTimeZonePrivilege", "SeShutdownPrivilege", "SeUndockPrivilege" };
            for (int index = 0; index < Marshal.ReadInt32(privileges); index++)
            {
                nint entry = privileges + 4 + index * 12;
                long luid = Marshal.ReadInt64(entry);
                uint length = 128; StringBuilder name = new((int)length);
                if (!LookupPrivilegeName(null, ref luid, name, ref length) || !permitted.Contains(name.ToString())) dangerous = true;
            }
            // Reject powerful privileges even if disabled: a child could enable them.
            return new(sid, Integer(12), logon, int.Parse(level.Split('-')[^1]), Integer(20) != 0,
                Member("S-1-5-32-544"), Member("S-1-5-4"), dangerous);
        }
        finally { Marshal.FreeHGlobal(user); Marshal.FreeHGlobal(integrity); Marshal.FreeHGlobal(statistics); Marshal.FreeHGlobal(privileges); }
    }

    public static TerminalTokenFacts CurrentFacts()
    {
        if (!OpenProcessToken(GetCurrentProcess(), 0x000A, out var token)) throw new InvalidOperationException("terminal_token_unavailable");
        using (token) return Inspect(token);
    }

    private static TerminalIdentity Resolve(AgentConfig config)
    {
        if (!config.TerminalConPtyEnabled || string.IsNullOrEmpty(config.TerminalAuthorizedUserSid) || config.TerminalWindowsSessionId is null or <= 0)
            throw new InvalidOperationException("terminal_identity_not_authorized");
        TerminalTokenFacts caller = CurrentFacts();
        SafeAccessTokenHandle token;
        bool same = caller.Sid == config.TerminalAuthorizedUserSid && caller.SessionId == config.TerminalWindowsSessionId;
        if (same)
        {
            if (!OpenProcessToken(GetCurrentProcess(), 0x000B, out token)) throw new InvalidOperationException("terminal_token_unavailable");
        }
        else if (caller.Sid == "S-1-5-18")
        {
            // Only this explicitly configured session. No active-session enumeration,
            // linked/elevated token, credential logon, or privilege enabling fallback.
            if (!WTSQueryUserToken((uint)config.TerminalWindowsSessionId.Value, out token))
                throw new InvalidOperationException("terminal_delegated_identity_unavailable");
        }
        else throw new InvalidOperationException("terminal_identity_not_authorized");
        try
        {
            var facts = Inspect(token);
            ValidateFacts(facts, config);
            return new TerminalIdentity(token, facts, same);
        }
        catch { token.Dispose(); throw; }
    }

    private sealed record Consent(TerminalAuthorization Authorization, TerminalTokenFacts Identity, string Shell);
    private static void ValidateAuthorization(TerminalAuthorization authorization, AgentConfig config)
    {
        if (authorization.SessionId == Guid.Empty || authorization.MachineId != config.MachineId ||
            string.IsNullOrEmpty(authorization.OperatorId) || authorization.ExpiresAt <= DateTimeOffset.UtcNow ||
            authorization.ExpiresAt > DateTimeOffset.UtcNow.AddMinutes(5))
            throw new InvalidOperationException("terminal_session_not_authorized");
    }

    public static TerminalIdentity Acquire(AgentConfig config, TerminalAuthorization authorization)
    {
        ValidateAuthorization(authorization, config);
        TerminalIdentity identity = Resolve(config);
        try
        {
            WindowsIdentity.RunImpersonated(identity.Token, () =>
            {
                string path = identity.ConsentPath(authorization.SessionId);
                using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None);
                if (file.Length is < 1 or > 4096) throw new InvalidOperationException("terminal_consent_invalid");
                byte[] encrypted = new byte[(int)file.Length]; file.ReadExactly(encrypted); file.Dispose();
                byte[] plain = Protect(encrypted, false);
                try
                {
                    Consent consent = JsonSerializer.Deserialize<Consent>(plain, new JsonSerializerOptions { UnmappedMemberHandling = System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow })!;
                    if (consent is null || consent.Authorization != authorization || consent.Identity != identity.Facts || consent.Shell != "powershell51")
                        throw new InvalidOperationException("terminal_consent_invalid");
                    // Atomic one-use receipt. Existing .used is never overwritten.
                    File.Move(path, path + ".used", false);
                    identity._consumedPath = path + ".used";
                    identity._consumedHash = System.Security.Cryptography.SHA256.HashData(encrypted);
                    identity._consentExpires = authorization.ExpiresAt;
                }
                finally { System.Security.Cryptography.CryptographicOperations.ZeroMemory(plain); }
            });
            return identity;
        }
        catch { identity.Dispose(); throw new InvalidOperationException("terminal_local_consent_required"); }
    }

    public bool ConsentStillValid()
    {
        try
        {
            if (_consentExpires <= DateTimeOffset.UtcNow || _consumedPath is null || _consumedHash is null) return false;
            return WindowsIdentity.RunImpersonated(Token, () =>
            {
                using var file = new FileStream(_consumedPath, FileMode.Open, FileAccess.Read, FileShare.Read);
                if (file.Length is < 1 or > 4096) return false;
                byte[] data = new byte[(int)file.Length]; file.ReadExactly(data);
                return System.Security.Cryptography.CryptographicOperations.FixedTimeEquals(
                    _consumedHash, System.Security.Cryptography.SHA256.HashData(data));
            });
        }
        catch (Exception) { return false; }
    }

    /// <summary>Explicit local-user consent API for a future approval surface. Never called by transport/service.</summary>
    public static string ApproveCurrentUser(AgentConfig config, TerminalAuthorization authorization, bool explicitlyConfirmed)
    {
        if (!explicitlyConfirmed) throw new InvalidOperationException("terminal_local_consent_required");
        ValidateAuthorization(authorization, config);
        using TerminalIdentity identity = Resolve(config);
        if (!identity.IsCaller) throw new InvalidOperationException("terminal_local_user_required");
        string path = identity.ConsentPath(authorization.SessionId);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        byte[] plain = JsonSerializer.SerializeToUtf8Bytes(new Consent(authorization, identity.Facts, "powershell51"));
        try
        {
            byte[] encrypted = Protect(plain, true);
            using var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            output.Write(encrypted);
        }
        finally { System.Security.Cryptography.CryptographicOperations.ZeroMemory(plain); }
        return path;
    }

    private string ConsentPath(Guid id) => Path.Combine(Profile, "AppData", "Local", "NightOwl", "TerminalConsent", id.ToString("D") + ".bin");
    private static byte[] Protect(byte[] input, bool encrypt)
    {
        nint data = Marshal.AllocHGlobal(input.Length); Marshal.Copy(input, 0, data, input.Length);
        Blob source = new() { Size = input.Length, Data = data }, result = default;
        try
        {
            bool ok = encrypt ? CryptProtectData(ref source, null, 0, 0, 0, 1, out result) : CryptUnprotectData(ref source, 0, 0, 0, 0, 1, out result);
            if (!ok) throw new InvalidOperationException("terminal_consent_invalid");
            byte[] output = new byte[result.Size]; Marshal.Copy(result.Data, output, 0, output.Length); return output;
        }
        finally { Marshal.FreeHGlobal(data); if (result.Data != 0) LocalFree(result.Data); }
    }
    public void Dispose() => Token.Dispose();

    [StructLayout(LayoutKind.Sequential)] private struct Blob { public int Size; public nint Data; }
    [DllImport("advapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] internal static extern bool OpenProcessToken(nint process, uint access, out SafeAccessTokenHandle token);
    [DllImport("kernel32.dll")] private static extern nint GetCurrentProcess();
    [DllImport("advapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetTokenInformation(SafeAccessTokenHandle token, int kind, nint info, int length, out int returned);
    [DllImport("advapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool DuplicateTokenEx(SafeAccessTokenHandle token, uint access, nint attributes, int level, int type, out SafeAccessTokenHandle duplicate);
    [DllImport("advapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CheckTokenMembership(SafeAccessTokenHandle token, byte[] sid, [MarshalAs(UnmanagedType.Bool)] out bool member);
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, EntryPoint="LookupPrivilegeNameW")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool LookupPrivilegeName(string? system, ref long luid, StringBuilder name, ref uint size);
    [DllImport("wtsapi32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool WTSQueryUserToken(uint session, out SafeAccessTokenHandle token);
    [DllImport("userenv.dll", CharSet = CharSet.Unicode, EntryPoint = "GetUserProfileDirectoryW", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetUserProfileDirectory(SafeAccessTokenHandle token, StringBuilder directory, ref uint size);
    [DllImport("crypt32.dll", CharSet = CharSet.Unicode, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CryptProtectData(ref Blob input, string? description, nint entropy, nint reserved, nint prompt, uint flags, out Blob output);
    [DllImport("crypt32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CryptUnprotectData(ref Blob input, nint description, nint entropy, nint reserved, nint prompt, uint flags, out Blob output);
    [DllImport("kernel32.dll")] private static extern nint LocalFree(nint memory);
}
