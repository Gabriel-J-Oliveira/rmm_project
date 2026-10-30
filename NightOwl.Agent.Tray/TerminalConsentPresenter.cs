using Microsoft.Win32;
using NightOwl.Agent.Shared;
using System.Security.Principal;

namespace NightOwl.Agent.Tray;

/// <summary>Local metadata/decision only. No agent bearer, operator ticket, or terminal bytes.</summary>
internal sealed class TerminalConsentPresenter : IDisposable
{
    private readonly System.Windows.Forms.Timer _timer = new() { Interval = 1000 };
    private readonly HashSet<Guid> _reviewed = new();
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
            // At most one active request. Keep only a small metadata replay set.
            if (_reviewed.Count > 32) { _reviewed.Clear(); _reviewed.Add(request.Authorization.SessionId); }
            ShowApproval(request);
        }
        catch (Exception) { /* Missing/foreign/untrusted/expired projections never prompt or approve. */ }
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
        try
        {
            var result = form.ShowDialog();
            if (_active != request) return; // Cancellation/logoff must not be followed by a late approval.
            // Re-read origin/ACL/proof and exact request after the user decides.
            var current = TerminalConsentRequest.ReadTrusted(NightOwlPaths.Current);
            if (current != request || !current.IsPending) return;
            if (result == DialogResult.Yes)
                TerminalUserIdentity.ApproveCurrentUser(current.Policy, current.Authorization, true);
            else TerminalUserIdentity.DenyCurrentUser(current.Policy, current.Authorization);
        }
        catch (Exception) { /* Fail closed. Never log receipts or terminal content. */ }
        finally { form.Dispose(); _dialog = null; }
    }
    public void Revoke()
    {
        try { if (_active is not null) TerminalUserIdentity.RevokeCurrentUser(_active.Policy, _active.Authorization.SessionId); }
        catch (Exception) { }
        if (_dialog is { IsDisposed: false } dialog)
        {
            try { if (dialog.InvokeRequired) dialog.BeginInvoke(new Action(dialog.Close)); else dialog.Close(); }
            catch (InvalidOperationException) { }
        }
        _active = null;
    }
    public void Dispose()
    {
        _timer.Stop(); _timer.Dispose();
        SystemEvents.SessionEnding -= OnSessionEnding;
        Revoke();
    }
}
