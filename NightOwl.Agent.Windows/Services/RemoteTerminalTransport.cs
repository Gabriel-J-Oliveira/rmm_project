using System.Buffers.Binary;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

/// <summary>D.1 outbound synthetic relay only. Contains no process or shell interface.</summary>
public sealed class RemoteTerminalTransport(AgentApiClient api, JsonlLogger logger)
{
    public const int MaxDataBytes = 16 * 1024;
    public const int MaxFrameBytes = MaxDataBytes + 9;

    public async Task RunAsync(AgentConfig config, CancellationToken ct)
    {
        if (!config.RemoteTerminalTransportEnabled || !config.HasValidToken) return;
        int failures = 0;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
                budget.CancelAfter(TimeSpan.FromSeconds(5));
                JsonElement response = await api.TerminalPollAsync(config, budget.Token);
                if (response.GetProperty("session").ValueKind != JsonValueKind.Null)
                    await ConnectSyntheticAsync(config, response.GetProperty("session"), ct);
                failures = 0;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested) { break; }
            catch (Exception)
            {
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
    }

    public static Uri ValidateConnection(AgentConfig config, JsonElement session)
    {
        if (!config.RemoteTerminalTransportEnabled) throw new InvalidOperationException("terminal_disabled");
        if (session.GetProperty("mode").GetString() != "synthetic" || session.GetProperty("execution").GetBoolean() ||
            session.GetProperty("protocol").GetInt32() != 1) throw new InvalidDataException("terminal_mode_invalid");
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
        Uri uri = ValidateConnection(config, session);
        DateTimeOffset expires = session.GetProperty("expires_at").GetDateTimeOffset();
        TimeSpan remaining = expires - DateTimeOffset.UtcNow;
        if (remaining <= TimeSpan.Zero || remaining > TimeSpan.FromMinutes(5)) throw new InvalidDataException("terminal_expiry_invalid");
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(ct);
        lifetime.CancelAfter(remaining);
        using ClientWebSocket socket = new();
        socket.Options.AddSubProtocol("nightowl-terminal.v1");
        socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(10);
        socket.Options.SetBuffer(MaxFrameBytes, MaxFrameBytes);
        using (var opening = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token))
        {
            opening.CancelAfter(TimeSpan.FromSeconds(5));
            await socket.ConnectAsync(uri, opening.Token);
        }
        await SendControl(socket, new { type = "authenticate", credential = session.GetProperty("credential").GetString() }, lifetime.Token);
        byte[] buffer = new byte[MaxFrameBytes];
        ulong receivedSequence = 0, sentSequence = 0;
        bool ready = false;
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
                // Synthetic echo proves byte transport; it never interprets or executes input.
                buffer[0] = 1;
                BinaryPrimitives.WriteUInt64BigEndian(buffer.AsSpan(1, 8), ++sentSequence);
                await Send(socket, buffer.AsMemory(0, size), WebSocketMessageType.Binary, lifetime.Token);
                continue;
            }
            if (size > 1024) throw new InvalidDataException("terminal_control_limit");
            using JsonDocument document = JsonDocument.Parse(buffer.AsMemory(0, size));
            JsonElement message = document.RootElement;
            switch (message.GetProperty("type").GetString())
            {
                case "connected": break;
                case "ready":
                    if (message.GetProperty("mode").GetString() != "synthetic" || message.GetProperty("execution").GetBoolean())
                        throw new InvalidDataException("terminal_mode_invalid");
                    ready = true;
                    break;
                case "ping":
                    string nonce = message.GetProperty("nonce").GetString()!;
                    if (nonce.Length != 16 || nonce.Any(c => !Uri.IsHexDigit(c))) throw new InvalidDataException("terminal_ping_invalid");
                    await SendControl(socket, new { type = "pong", nonce }, lifetime.Token);
                    break;
                case "resize":
                    int cols = message.GetProperty("cols").GetInt32(), rows = message.GetProperty("rows").GetInt32();
                    if (!ready || cols is < 1 or > 500 || rows is < 1 or > 500) throw new InvalidDataException("terminal_resize_invalid");
                    await SendControl(socket, new { type = "resized", cols, rows }, lifetime.Token);
                    break;
                case "closed": case "error": return;
                default: throw new InvalidDataException("terminal_control_invalid");
            }
        }
    }

    public static bool ValidInputFrame(ReadOnlySpan<byte> frame, ulong expectedSequence) =>
        frame.Length is > 9 and <= MaxFrameBytes && frame[0] == 0 && BinaryPrimitives.ReadUInt64BigEndian(frame.Slice(1, 8)) == expectedSequence;

    private static Task SendControl(ClientWebSocket socket, object value, CancellationToken ct) =>
        Send(socket, Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value)), WebSocketMessageType.Text, ct);

    private static async Task Send(ClientWebSocket socket, ReadOnlyMemory<byte> data, WebSocketMessageType type, CancellationToken ct)
    {
        using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
        budget.CancelAfter(TimeSpan.FromSeconds(2));
        await socket.SendAsync(data, type, true, budget.Token);
    }
}
