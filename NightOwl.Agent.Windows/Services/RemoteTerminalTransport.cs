using NightOwl.Agent.Shared;
using System.Buffers.Binary;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

/// <summary>Outbound session transport; real execution requires separate local identity/consent policy.</summary>
public sealed class RemoteTerminalTransport(AgentApiClient api, JsonlLogger logger)
{
    public Action<ConPtyObservation>? ObserveSession { get; set; }
    public Action<string>? ObserveFailure { get; set; }
    public const int MaxDataBytes = 16 * 1024;
    public const int MaxFrameBytes = MaxDataBytes + 9;

    public async Task RunAsync(AgentConfig config, CancellationToken ct)
    {
        if (!config.RemoteTerminalTransportEnabled || !config.HasValidToken) return;
        int failures = 0;
        Guid? consentSession = null;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                budget.CancelAfter(TimeSpan.FromSeconds(5));
                JsonElement response = await api.TerminalPollAsync(config, budget.Token);
                if (response.TryGetProperty("consent_request", out var proposal))
                {
                    consentSession = Guid.Parse(proposal.GetProperty("session_id").GetString()!);
                    if (!await new TerminalConsentCoordinator(api).RequestAsync(config, proposal, ct)) consentSession = null;
                    else
                    {
                        using var opening = CancellationTokenSource.CreateLinkedTokenSource(ct);
                        opening.CancelAfter(TimeSpan.FromSeconds(5));
                        response = await api.TerminalPollAsync(config, opening.Token);
                    }
                }
                if (response.GetProperty("session").ValueKind != JsonValueKind.Null)
                {
                    try { await ConnectAsync(config, response.GetProperty("session"), ct); }
                    finally { TerminalConsentCoordinator.Clear(consentSession); consentSession = null; }
                }
                failures = 0;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested) { break; }
            catch (Exception failure)
            {
                string category = failure is AgentApiException apiError ? "http_" + apiError.StatusCodeValue :
                    failure is InvalidOperationException or InvalidDataException && System.Text.RegularExpressions.Regex.IsMatch(failure.Message, "^terminal_[a-z_]{1,80}$") ?
                    failure.Message : failure.GetType().Name;
                try { ObserveFailure?.Invoke(category); } catch (Exception) { }
                TerminalConsentCoordinator.Clear(consentSession); consentSession = null;
                failures = Math.Min(4, failures + 1);
                // Never log URLs, credentials, native output, or exception bodies.
                try { await logger.LogAsync("terminal.transport.unavailable", "Synthetic transport unavailable.",
                    new { category = "connection_or_authorization" }, ct, "warning"); }
                catch (Exception) { }
            }
            try { await Task.Delay(TimeSpan.FromSeconds(Math.Min(30, 5 * Math.Pow(2, failures))) +
                TimeSpan.FromMilliseconds(Random.Shared.Next(250)), ct); }
            catch (OperationCanceledException) when (ct.IsCancellationRequested) { break; }
        }
        TerminalConsentCoordinator.Clear(consentSession);
    }

    public static Uri ValidateConnection(AgentConfig config, JsonElement session)
    {
        if (!config.RemoteTerminalTransportEnabled) throw new InvalidOperationException("terminal_disabled");
        string? mode = session.GetProperty("mode").GetString();
        bool real = mode == "conpty";
        if (mode is not ("synthetic" or "conpty") || session.GetProperty("execution").GetBoolean() != real ||
            session.GetProperty("protocol").GetInt32() != 1 || real && (!config.TerminalConPtyEnabled ||
            session.GetProperty("machine_id").GetString() != config.MachineId || string.IsNullOrEmpty(session.GetProperty("operator_id").GetString())))
            throw new InvalidDataException("terminal_mode_invalid");
        Guid id = Guid.Parse(session.GetProperty("session_id").GetString()!);
        string expected = $"/ws/terminal/{id:D}/agent/";
        if (session.GetProperty("websocket_path").GetString() != expected ||
            session.GetProperty("credential").GetString() is not { Length: >= 32 and <= 128 })
            throw new InvalidDataException("terminal_contract_invalid");
        Uri server = new(config.ServerBaseUrl);
        bool testLoopback = server.IsLoopback && Environment.GetEnvironmentVariable("NIGHTOWL_TEST_SERVER") == server.GetLeftPart(UriPartial.Authority);
        if (server.UserInfo != "" || server.Query != "" || server.Fragment != "" ||
            server.Scheme != "https" && !(server.Scheme == "http" && testLoopback))
            throw new InvalidOperationException("terminal_tls_required");
        return new UriBuilder(server) { Scheme = server.Scheme == "https" ? "wss" : "ws", Path = expected, Query = "", Fragment = "" }.Uri;
    }

    public async Task ConnectSyntheticAsync(AgentConfig config, JsonElement session, CancellationToken ct)
    {
        if (session.GetProperty("mode").GetString() != "synthetic") throw new InvalidDataException("terminal_mode_invalid");
        await ConnectAsync(config, session, ct);
    }

    public async Task ConnectAsync(AgentConfig config, JsonElement session, CancellationToken ct)
    {
        Uri uri = ValidateConnection(config, session);
        DateTimeOffset expires = session.GetProperty("expires_at").GetDateTimeOffset();
        TimeSpan remaining = expires - DateTimeOffset.UtcNow;
        if (remaining <= TimeSpan.Zero || remaining > TimeSpan.FromMinutes(5)) throw new InvalidDataException("terminal_expiry_invalid");
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(ct);
        lifetime.CancelAfter(remaining);
        using ClientWebSocket socket = new();
        using SemaphoreSlim sendGate = new(1, 1);
        ConPtyTerminal? terminal = null;
        Task output = Task.CompletedTask;
        bool real = session.GetProperty("mode").GetString() == "conpty";
        socket.Options.AddSubProtocol("nightowl-terminal.v1");
        socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(10);
        socket.Options.SetBuffer(MaxFrameBytes, MaxFrameBytes);
        using (var opening = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token))
        {
            opening.CancelAfter(TimeSpan.FromSeconds(5));
            await socket.ConnectAsync(uri, opening.Token);
        }
        await SendControl(socket, new { type = "authenticate", credential = session.GetProperty("credential").GetString() }, lifetime.Token, sendGate);
        byte[] buffer = new byte[MaxFrameBytes];
        ulong receivedSequence = 0, sentSequence = 0;
        bool ready = false;
        try
        {
        while (socket.State == WebSocketState.Open && !lifetime.IsCancellationRequested)
        {
            int size = 0;
            WebSocketReceiveResult part;
            using (var idle = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token))
            {
                idle.CancelAfter(TimeSpan.FromSeconds(25));
                do
                {
                    if (size == buffer.Length) throw new InvalidDataException("terminal_frame_limit");
                    part = await socket.ReceiveAsync(new ArraySegment<byte>(buffer, size, buffer.Length - size), idle.Token);
                    if (part.MessageType == WebSocketMessageType.Close)
                    {
                        using var closing = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                        await socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "transport_closed", closing.Token);
                        return;
                    }
                    size += part.Count;
                } while (!part.EndOfMessage);
            }
            if (part.MessageType == WebSocketMessageType.Binary)
            {
                if (!ready || !ValidInputFrame(buffer.AsSpan(0, size), receivedSequence + 1))
                    throw new InvalidDataException("terminal_binary_invalid");
                receivedSequence++;
                if (real)
                {
                    await terminal!.WriteAsync(buffer.AsMemory(9, size - 9), lifetime.Token);
                    continue;
                }
                // D.1 compatibility: echo never interprets input.
                buffer[0] = 1;
                BinaryPrimitives.WriteUInt64BigEndian(buffer.AsSpan(1, 8), ++sentSequence);
                await Send(socket, buffer.AsMemory(0, size), WebSocketMessageType.Binary, lifetime.Token, sendGate);
                continue;
            }
            if (size > 1024) throw new InvalidDataException("terminal_control_limit");
            using JsonDocument document = JsonDocument.Parse(buffer.AsMemory(0, size));
            JsonElement message = document.RootElement;
            switch (message.GetProperty("type").GetString())
            {
                case "connected": break;
                case "transport_ready":
                    if (!real || terminal is not null || message.GetProperty("mode").GetString() != "conpty" || !message.GetProperty("execution").GetBoolean())
                        throw new InvalidDataException("terminal_mode_invalid");
                    try
                    {
                        var authorization = new TerminalAuthorization(Guid.Parse(session.GetProperty("session_id").GetString()!), config.MachineId,
                            session.GetProperty("operator_id").GetString()!, expires);
                        terminal = await ConPtyTerminal.StartAsync(config, authorization, 80, 24, lifetime.Token);
                    }
                    catch (Exception ex)
                    {
                        bool identityFailure = ex is InvalidOperationException &&
                            (ex.Message.Contains("identity", StringComparison.Ordinal) || ex.Message.Contains("consent", StringComparison.Ordinal) ||
                             ex.Message.Contains("profile", StringComparison.Ordinal) || ex.Message == "terminal_session_not_authorized");
                        await SendControl(socket, new { type = "error", code = identityFailure ? "identity_or_consent_required" : "conpty_start_failed" }, lifetime.Token, sendGate);
                        return;
                    }
                    await SendControl(socket, new { type = "process_ready", mode = "conpty", execution = true, non_system = true,
                        identity = new { sid = terminal.Identity.Sid, windows_session_id = terminal.Identity.SessionId,
                            integrity = terminal.Identity.Integrity, elevated = terminal.Identity.Elevated } }, lifetime.Token, sendGate);
                    break;
                case "ready":
                    if (ready || message.GetProperty("mode").GetString() != (real ? "conpty" : "synthetic") ||
                        message.GetProperty("execution").GetBoolean() != real || real && terminal is null)
                        throw new InvalidDataException("terminal_mode_invalid");
                    ready = true;
                    if (real)
                    {
                        output = Task.Run(async () =>
                        {
                            byte[] frame = new byte[MaxFrameBytes];
                            try
                            {
                                await foreach (byte[] bytes in terminal!.Output.ReadAllAsync(lifetime.Token))
                                {
                                    frame[0] = 1;
                                    BinaryPrimitives.WriteUInt64BigEndian(frame.AsSpan(1, 8), ++sentSequence);
                                    bytes.CopyTo(frame, 9);
                                    int size = bytes.Length;
                                    // Coalesce already available VT fragments, preserving order and
                                    // fixed memory/latency. Never wait for command completion.
                                    while (terminal.Output.TryPeek(out var next) && next.Length <= MaxDataBytes - size && terminal.Output.TryRead(out next))
                                    { next.CopyTo(frame, size + 9); size += next.Length; }
                                    await Send(socket, frame.AsMemory(0, size + 9), WebSocketMessageType.Binary, lifetime.Token, sendGate);
                                    await Task.Delay(12, lifetime.Token); // <100 frames/s; fixed bounded queues preserve pressure.
                                }
                                await SendControl(socket, new { type = "error", code = terminal.Observe().OutputBytes > ConPtyTerminal.MaxOutputBytes ?
                                    "terminal_output_limit" : "terminal_process_exited" }, lifetime.Token, sendGate);
                            }
                            catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
                            finally { lifetime.Cancel(); }
                        });
                    }
                    break;
                case "ping":
                    string nonce = message.GetProperty("nonce").GetString()!;
                    if (nonce.Length != 16 || nonce.Any(c => !Uri.IsHexDigit(c))) throw new InvalidDataException("terminal_ping_invalid");
                    await SendControl(socket, new { type = "pong", nonce }, lifetime.Token, sendGate);
                    break;
                case "resize":
                    int cols = message.GetProperty("cols").GetInt32(), rows = message.GetProperty("rows").GetInt32();
                    if (!ready || cols is < 1 or > 500 || rows is < 1 or > 500) throw new InvalidDataException("terminal_resize_invalid");
                    if (real) terminal!.Resize(cols, rows);
                    await SendControl(socket, new { type = "resized", cols, rows }, lifetime.Token, sendGate);
                    break;
                case "closed": case "error": return;
                default: throw new InvalidDataException("terminal_control_invalid");
            }
        }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
        finally
        {
            lifetime.Cancel();
            if (terminal is not null)
            {
                await terminal.DisposeAsync();
                try { ObserveSession?.Invoke(terminal.Observe()); } catch (Exception) { }
            }
            try { await output; } catch (Exception) { }
        }
    }

    public static bool ValidInputFrame(ReadOnlySpan<byte> frame, ulong expectedSequence) =>
        frame.Length is > 9 and <= MaxFrameBytes && frame[0] == 0 && BinaryPrimitives.ReadUInt64BigEndian(frame.Slice(1, 8)) == expectedSequence;

    private static Task SendControl(ClientWebSocket socket, object value, CancellationToken ct, SemaphoreSlim gate) =>
        Send(socket, Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value)), WebSocketMessageType.Text, ct, gate);

    private static async Task Send(ClientWebSocket socket, ReadOnlyMemory<byte> data, WebSocketMessageType type, CancellationToken ct, SemaphoreSlim gate)
    {
        using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
        budget.CancelAfter(TimeSpan.FromSeconds(2));
        await gate.WaitAsync(budget.Token);
        try { await socket.SendAsync(data, type, true, budget.Token); }
        finally { gate.Release(); }
    }
}
