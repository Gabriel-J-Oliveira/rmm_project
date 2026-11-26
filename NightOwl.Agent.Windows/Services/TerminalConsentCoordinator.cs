using System.Diagnostics;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class TerminalConsentCoordinator(AgentApiClient api, JsonlLogger logger)
{
    public static TerminalConsentRequest Proposal(AgentConfig config, JsonElement value)
    {
        var auth = new TerminalAuthorization(Guid.Parse(value.GetProperty("session_id").GetString()!), value.GetProperty("machine_id").GetString()!,
            value.GetProperty("operator_id").GetString()!, value.GetProperty("expires_at").GetDateTimeOffset());
        DateTimeOffset deadline = value.GetProperty("consent_deadline").GetDateTimeOffset();
        if (!config.TerminalConPtyEnabled || auth.MachineId != config.MachineId || auth.ExpiresAt <= DateTimeOffset.UtcNow ||
            auth.ExpiresAt > DateTimeOffset.UtcNow.AddMinutes(5) || deadline <= DateTimeOffset.UtcNow || deadline > auth.ExpiresAt ||
            deadline > DateTimeOffset.UtcNow.AddSeconds(120)) throw new InvalidDataException("terminal_consent_request_invalid");
        return new(TerminalIdentity.Policy(config), auth, value.GetProperty("operator_name").GetString()!,
            value.GetProperty("endpoint_name").GetString()!, new Uri(config.ServerBaseUrl).GetLeftPart(UriPartial.Authority), deadline);
    }

    public async Task<bool> RequestAsync(AgentConfig config, JsonElement value, CancellationToken ct)
    {
        Guid? correlation = TrySessionId(value);
        TerminalConsentRequest request;
        try
        {
            request = Proposal(config, value);
        }
        catch (Exception ex)
        {
            await LogAsync("terminal.consent.proposal_rejected", correlation, config, "rejected",
                ex.GetType().Name, ct);
            throw;
        }
        await LogAsync("terminal.consent.proposal_validated", request.Authorization.SessionId, config,
            "accepted", null, ct);
        try
        {
            request.Write(NightOwlPaths.Current);
            await LogAsync("terminal.consent.projection_written", request.Authorization.SessionId, config,
                "written", null, ct);
        }
        catch (Exception ex)
        {
            await LogAsync("terminal.consent.projection_write_failed", request.Authorization.SessionId, config,
                "failed", ex.GetType().Name, ct);
            throw;
        }
        bool approved = false;
        try
        {
            long checkedAt = Stopwatch.GetTimestamp();
            while (request.IsPending && !ct.IsCancellationRequested)
            {
                (string Decision, TerminalTokenFacts? Identity) decision;
                try { decision = TerminalUserIdentity.ReadDecision(request.Policy, request.Authorization); }
                catch (Exception ex)
                {
                    await LogAsync("terminal.consent.decision_read_failed", request.Authorization.SessionId, config,
                        "fail_closed", ex.GetType().Name, ct);
                    decision = ("denied", null);
                }
                if (decision.Decision != "pending")
                {
                    await LogAsync("terminal.consent.decision_observed", request.Authorization.SessionId, config,
                        decision.Decision, null, ct);
                    using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    budget.CancelAfter(TimeSpan.FromSeconds(5));
                    await api.TerminalConsentAsync(config, request.Authorization.SessionId, decision.Decision, decision.Identity, budget.Token);
                    await LogAsync("terminal.consent.decision_submitted", request.Authorization.SessionId, config,
                        decision.Decision, null, ct);
                    approved = decision.Decision == "approved";
                    return approved;
                }
                if (Stopwatch.GetElapsedTime(checkedAt).TotalSeconds >= 5)
                {
                    // Reuse D.1 control cadence while waiting, never poll terminal I/O.
                    using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    budget.CancelAfter(TimeSpan.FromSeconds(5));
                    var state = await api.TerminalPollAsync(config, budget.Token);
                    if (!state.TryGetProperty("consent_request", out var pending) || pending.GetProperty("session_id").GetString() != request.Authorization.SessionId.ToString("D"))
                    {
                        await LogAsync("terminal.consent.poll_no_longer_pending", request.Authorization.SessionId, config,
                            "closed_or_expired", null, ct);
                        return false;
                    }
                    await LogAsync("terminal.consent.poll_refreshed", request.Authorization.SessionId, config,
                        "pending", null, ct);
                    checkedAt = Stopwatch.GetTimestamp();
                }
                await Task.Delay(250, ct);
            }
            if (!ct.IsCancellationRequested)
            {
                await LogAsync("terminal.consent.deadline_reached", request.Authorization.SessionId, config,
                    "expired", null, ct);
                using var budget = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await api.TerminalConsentAsync(config, request.Authorization.SessionId, "expired", null, budget.Token);
                await LogAsync("terminal.consent.expiration_submitted", request.Authorization.SessionId, config,
                    "expired", null, ct);
            }
            return false;
        }
        finally
        {
            if (!approved)
            {
                Clear(request.Authorization.SessionId);
                await LogAsync("terminal.consent.projection_cleared", request.Authorization.SessionId, config,
                    "cleared", null, CancellationToken.None);
            }
        }
    }

    private Task LogAsync(string eventType, Guid? sessionId, AgentConfig config, string outcome,
        string? failureCode, CancellationToken ct)
    {
        object context = TerminalConsentDiagnostics.SafeContext(sessionId,
            config.TerminalWindowsSessionId, Process.GetCurrentProcess().SessionId,
            null, null, outcome, failureCode);
        return LogBestEffortAsync(eventType, context, ct, failureCode is null ? "info" : "warning");
    }

    private async Task LogBestEffortAsync(string eventType, object context, CancellationToken ct, string level)
    {
        try { await logger.LogAsync(eventType, "Terminal consent stage recorded.", context, ct, level); }
        catch { /* Diagnostics must not change the fail-closed consent protocol. */ }
    }

    private static Guid? TrySessionId(JsonElement value) =>
        value.TryGetProperty("session_id", out JsonElement id) && Guid.TryParse(id.GetString(), out Guid parsed) ? parsed : null;

    public static void Clear(Guid? id)
    {
        if (id is null) return;
        string path = TerminalConsentRequest.PathFor(NightOwlPaths.Current);
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllBytes(path));
            if (document.RootElement.GetProperty("Authorization").GetProperty("SessionId").GetGuid() == id) File.Delete(path);
        }
        catch (IOException) { }
        catch (JsonException) { }
    }
}
