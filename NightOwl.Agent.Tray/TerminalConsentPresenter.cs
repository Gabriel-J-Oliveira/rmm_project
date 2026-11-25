using Microsoft.Win32;
using NightOwl.Agent.Shared;
using System.Security.Principal;
using System.Diagnostics;
using System.Text.Json;

namespace NightOwl.Agent.Tray;

/// <summary>Local metadata/decision only. No agent bearer, operator ticket, or terminal bytes.</summary>
internal sealed class TerminalConsentPresenter : IDisposable
{
    private readonly System.Windows.Forms.Timer _timer = new() { Interval = 1000 };
    private readonly HashSet<Guid> _reviewed = new();
    private readonly HashSet<string> _loggedStages = new(StringComparer.Ordinal);
    private TerminalConsentRequest? _active;
    private Form? _dialog;
    public TerminalConsentPresenter()
    {
        _timer.Tick += (_, _) => Check();
        SystemEvents.SessionEnding += OnSessionEnding;
        _timer.Start();
    }
    private void OnSessionEnding(object? sender, SessionEndingEventArgs e) => Revoke();
    private void Check()
    {
        if (_active is not null && !File.Exists(TerminalConsentRequest.PathFor(NightOwlPaths.Current))) Revoke();
        if (_active is not null && _active.Authorization.ExpiresAt <= DateTimeOffset.UtcNow) Revoke();
        if (_dialog is not null && _active is not null && !_active.IsPending) _dialog.Close();
        try
        {
            var request = TerminalConsentRequest.ReadTrusted(NightOwlPaths.Current);
            if (!request.IsPending || _reviewed.Contains(request.Authorization.SessionId) || _dialog is not null) return;
            _active = request;
            _reviewed.Add(request.Authorization.SessionId);
            LogStage("tray.consent.request_validated", request.Authorization.SessionId, request.Policy.TerminalWindowsSessionId,
                true, true, "accepted");
            // At most one active request. Keep only a small metadata replay set.
            if (_reviewed.Count > 32) { _reviewed.Clear(); _reviewed.Add(request.Authorization.SessionId); }
            ShowApproval(request);
        }
        catch (Exception ex)
        {
            if (TryReadProjectionContext(out Guid sessionId, out int? configuredSession, out bool? sidMatches, out bool? sessionMatches))
                LogStage("tray.consent.request_rejected", sessionId, configuredSession, sidMatches, sessionMatches,
                    "rejected", SafeFailureCode(ex));
            else if (File.Exists(TerminalConsentRequest.PathFor(NightOwlPaths.Current)))
                LogStage("tray.consent.projection_unreadable", Guid.Empty, null, null, null,
                    "unreadable", ex.GetType().Name);
        }
    }
    private static string Label(string value) => new(value.Where(c => !char.IsControl(c)).Take(255).ToArray());
    private void ShowApproval(TerminalConsentRequest request)
    {
        var form = new Form { Text = "NightOwl — consentimento de terminal", Width = 570, Height = 350,
            StartPosition = FormStartPosition.CenterScreen, FormBorderStyle = FormBorderStyle.FixedDialog,
            MaximizeBox = false, MinimizeBox = false, TopMost = true, ShowInTaskbar = true };
        _dialog = form;
        var message = new Label { Dock = DockStyle.Fill, Padding = new Padding(18), AutoSize = false,
            Text = $"Permitir acesso ao PowerShell com seus privilégios atuais?\n\n" +
                $"Operador: {Label(request.OperatorName)} (ID {Label(request.Authorization.OperatorId)})\n" +
                $"Endpoint: {Label(request.EndpointName)}\nServidor: {Label(request.ServerOrigin)}\n" +
                $"Usuário Windows: {Label(WindowsIdentity.GetCurrent().Name)}\n" +
                $"Sessão Windows: {request.Policy.TerminalWindowsSessionId}; sem elevação administrativa\n" +
                $"Válido até: {request.Authorization.ExpiresAt.ToLocalTime():HH:mm:ss}\n" +
                $"Sessão remota: {request.Authorization.SessionId:D}\n\n" +
                "O operador poderá executar comandos como este usuário. Você pode revogar pelo menu do Tray." };
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 52, FlowDirection = FlowDirection.RightToLeft };
        var deny = new Button { Text = "Negar", DialogResult = DialogResult.No, Width = 110 };
        var allow = new Button { Text = "Permitir", Width = 110, DialogResult = DialogResult.Yes };
        buttons.Controls.Add(deny); buttons.Controls.Add(allow);
        form.Controls.Add(message); form.Controls.Add(buttons);
        form.AcceptButton = deny; form.CancelButton = deny;
        form.Shown += (_, _) => LogStage("tray.consent.dialog_visible", request.Authorization.SessionId,
            request.Policy.TerminalWindowsSessionId, true, true, "visible");
        LogStage("tray.consent.dialog_opening", request.Authorization.SessionId, request.Policy.TerminalWindowsSessionId,
            true, true, "shown");
        try
        {
            var result = form.ShowDialog();
            string decision = result == DialogResult.Yes ? "approved" : result == DialogResult.No ? "denied" : "dismissed";
            LogStage("tray.consent.dialog_closed", request.Authorization.SessionId, request.Policy.TerminalWindowsSessionId,
                true, true, decision);
            if (_active != request) return; // Cancellation/logoff must not be followed by a late approval.
            // Re-read origin/ACL/proof and exact request after the user decides.
            var current = TerminalConsentRequest.ReadTrusted(NightOwlPaths.Current);
            if (current != request || !current.IsPending) return;
            if (result == DialogResult.Yes)
                TerminalUserIdentity.ApproveCurrentUser(current.Policy, current.Authorization, true);
            else TerminalUserIdentity.DenyCurrentUser(current.Policy, current.Authorization);
            LogStage("tray.consent.decision_written", request.Authorization.SessionId, request.Policy.TerminalWindowsSessionId,
                true, true, decision);
        }
        catch (Exception ex)
        {
            LogStage("tray.consent.decision_failed", request.Authorization.SessionId, request.Policy.TerminalWindowsSessionId,
                true, true, "failed", SafeFailureCode(ex));
        }
        finally { form.Dispose(); _dialog = null; }
    }
    public void Revoke()
    {
        try { if (_active is not null) TerminalUserIdentity.RevokeCurrentUser(_active.Policy, _active.Authorization.SessionId); }
        catch (Exception) { }
        if (_active is not null)
            LogStage("tray.consent.revoked", _active.Authorization.SessionId, _active.Policy.TerminalWindowsSessionId,
                true, true, "revoked_or_session_ending");
        if (_dialog is { IsDisposed: false } dialog)
        {
            try { if (dialog.InvokeRequired) dialog.BeginInvoke(new Action(dialog.Close)); else dialog.Close(); }
            catch (InvalidOperationException) { }
        }
        _active = null;
    }

    private void LogStage(string eventType, Guid sessionId, int? configuredSessionId,
        bool? sidMatches, bool? sessionMatches, string outcome, string? failureCode = null)
    {
        string dedupe = $"{sessionId:D}:{eventType}:{outcome}";
        if (!_loggedStages.Add(dedupe)) return;
        if (_loggedStages.Count > 128) _loggedStages.Clear();
        TrayLog.Write(eventType, "Terminal consent UI stage recorded.", TerminalConsentDiagnostics.SafeContext(
            sessionId, configuredSessionId, Process.GetCurrentProcess().SessionId, sidMatches,
            sessionMatches, outcome, failureCode));
    }

    private static string SafeFailureCode(Exception exception) => exception is InvalidOperationException &&
        TerminalConsentDiagnostics.SanitizeFailureCode(exception.Message) is string code ? code : exception.GetType().Name;

    private static bool TryReadProjectionContext(out Guid sessionId, out int? configuredSessionId,
        out bool? sidMatches, out bool? sessionMatches)
    {
        sessionId = Guid.Empty; configuredSessionId = null; sidMatches = null; sessionMatches = null;
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllBytes(
                TerminalConsentRequest.PathFor(NightOwlPaths.Current)));
            JsonElement root = document.RootElement;
            JsonElement authorization = root.GetProperty("Authorization");
            JsonElement policy = root.GetProperty("Policy");
            if (!Guid.TryParse(authorization.GetProperty("SessionId").GetString(), out sessionId)) return false;
            if (policy.TryGetProperty("TerminalWindowsSessionId", out JsonElement expectedSession) &&
                expectedSession.ValueKind == JsonValueKind.Number && expectedSession.TryGetInt32(out int parsedSession))
            {
                configuredSessionId = parsedSession;
                sessionMatches = parsedSession == Process.GetCurrentProcess().SessionId;
            }
            if (policy.TryGetProperty("TerminalAuthorizedUserSid", out JsonElement expectedSid) &&
                expectedSid.ValueKind == JsonValueKind.String)
                sidMatches = expectedSid.GetString() == TerminalUserIdentity.CurrentFacts().Sid;
            return true;
        }
        catch { return false; }
    }
    public void Dispose()
    {
        _timer.Stop(); _timer.Dispose();
        SystemEvents.SessionEnding -= OnSessionEnding;
        Revoke();
    }
}
