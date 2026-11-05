using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;
using NightOwl.Agent.Shared;

namespace NightOwl.Agent.Windows.Services;

// Isolated foundation only: no transport, persistence, token elevation or process creation.
public static class TerminalAdministrativeAuthentication
{
    public static SafeAccessTokenHandle Authenticate(string account, string domain, char[] password)
    {
        ArgumentNullException.ThrowIfNull(password);
        nint nativePassword = 0;
        try
        {
            if (string.IsNullOrWhiteSpace(account) || account.Length > 256 || domain.Length > 256 ||
                account.Contains('\0') || domain.Contains('\0') || password.Length is < 1 or > 512 || Array.IndexOf(password, '\0') >= 0)
                throw new InvalidOperationException("terminal_auth_invalid");
            nativePassword = Marshal.AllocHGlobal(checked((password.Length + 1) * sizeof(char)));
            Marshal.Copy(password, 0, nativePassword, password.Length);
            Marshal.WriteInt16(nativePassword, password.Length * sizeof(char), 0);
            if (!LogonUser(account, string.IsNullOrWhiteSpace(domain) ? "." : domain, nativePassword, 2, 0, out var token))
                throw new InvalidOperationException("terminal_auth_failed");
            try
            {
                Validate(TerminalUserIdentity.Inspect(token));
                return token;
            }
            catch { token.Dispose(); throw; }
        }
        finally
        {
            CryptographicOperations.ZeroMemory(MemoryMarshal.AsBytes(password.AsSpan()));
            if (nativePassword != 0)
            {
                for (int i = 0; i <= password.Length; i++) Marshal.WriteInt16(nativePassword, i * sizeof(char), 0);
                Marshal.FreeHGlobal(nativePassword);
            }
        }
    }

    public static void Validate(TerminalTokenFacts facts)
    {
        if (facts.Sid is "S-1-5-18" or "S-1-5-19" or "S-1-5-20" ||
            !facts.Administrator || !facts.Elevated || facts.Integrity < 12288)
            throw new InvalidOperationException("terminal_auth_not_administrator");
    }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "LogonUserW")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool LogonUser(string user, string domain, nint password, int logonType, int provider, out SafeAccessTokenHandle token);
}
