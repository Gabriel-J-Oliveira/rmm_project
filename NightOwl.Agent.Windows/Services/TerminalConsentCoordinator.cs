using System.Diagnostics;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class TerminalConsentCoordinator(AgentApiClient api)
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
        var request = Proposal(config, value);
        request.Write(NightOwlPaths.Current);
        bool approved = false;
        try
        {
            long checkedAt = Stopwatch.GetTimestamp();
            while (request.IsPending && !ct.IsCancellationRequested)
            {
                (string Decision, TerminalTokenFacts? Identity) decision;
                try { decision = TerminalUserIdentity.ReadDecision(request.Policy, request.Authorization); }
                catch (Exception) { decision = ("denied", null); }
                if (decision.Decision != "pending")
                {
                    using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    budget.CancelAfter(TimeSpan.FromSeconds(5));
                    await api.TerminalConsentAsync(config, request.Authorization.SessionId, decision.Decision, decision.Identity, budget.Token);
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
                        return false;
                    checkedAt = Stopwatch.GetTimestamp();
                }
                await Task.Delay(250, ct);
            }
            if (!ct.IsCancellationRequested)
            {
                using var budget = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await api.TerminalConsentAsync(config, request.Authorization.SessionId, "expired", null, budget.Token);
            }
            return false;
        }
        finally { if (!approved) Clear(request.Authorization.SessionId); }
    }

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
