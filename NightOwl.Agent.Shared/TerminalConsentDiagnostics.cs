using System.Text.RegularExpressions;

namespace NightOwl.Agent.Shared;

public static class TerminalConsentDiagnostics
{
    public static object SafeContext(Guid? sessionId, int? configuredSessionId, int processSessionId,
        bool? sidMatches, bool? sessionMatches, string outcome, string? failureCode = null) => new
    {
        session_id = sessionId?.ToString("D"),
        configured_session_id = configuredSessionId,
        process_session_id = processSessionId,
        sid_matches = sidMatches,
        session_matches = sessionMatches,
        outcome = SanitizeFailureCode(outcome) ?? "unknown",
        failure_code = SanitizeFailureCode(failureCode)
    };

    public static string? SanitizeFailureCode(string? value) => value is not null &&
        Regex.IsMatch(value, "\\A[a-zA-Z0-9_.-]{1,80}\\z", RegexOptions.CultureInvariant) ? value : null;

    public static (bool SidMatches, bool SessionMatches) CompareIdentity(string expectedSid, int? expectedSessionId,
        string actualSid, int actualSessionId) =>
        (expectedSid.Length > 0 && string.Equals(expectedSid, actualSid, StringComparison.Ordinal),
            expectedSessionId.HasValue && expectedSessionId.Value == actualSessionId);
}
