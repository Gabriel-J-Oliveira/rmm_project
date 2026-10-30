using System.Buffers.Binary;
using System.Text.Json;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class TerminalTransportTests
{
    private static void Check(bool value) { if (!value) throw new Exception("Terminal contract assertion failed."); }
    internal static async Task RunAsync()
    {
        Guid id = Guid.NewGuid();
        var info = JsonSerializer.SerializeToElement(new { session_id = id.ToString(), protocol = 1, mode = "synthetic", execution = false,
            credential = new string('s', 43), websocket_path = $"/ws/terminal/{id:D}/agent/", expires_at = DateTimeOffset.UtcNow.AddMinutes(1) });
        AgentConfig config = new() { ServerBaseUrl = "https://isolated.invalid" };
        try { RemoteTerminalTransport.ValidateConnection(config, info); throw new Exception("Disabled transport accepted"); }
        catch (InvalidOperationException) { }
        config.RemoteTerminalTransportEnabled = true;
        Check(RemoteTerminalTransport.ValidateConnection(config, info).Scheme == "wss");
        foreach (string origin in new[] { "http://untrusted.invalid", "https://user:secret@isolated.invalid", "https://isolated.invalid?credential=secret" })
        {
            config.ServerBaseUrl = origin;
            try { RemoteTerminalTransport.ValidateConnection(config, info); throw new Exception("Unsafe origin accepted"); }
            catch (InvalidOperationException) { }
        }
        config.ServerBaseUrl = "https://isolated.invalid";
        foreach (var bad in new[] {
            JsonSerializer.SerializeToElement(new { session_id = id.ToString(), protocol = 1, mode = "real", execution = true }),
            JsonSerializer.SerializeToElement(new { session_id = id.ToString(), protocol = 1, mode = "synthetic", execution = false,
                credential = new string('s',43), websocket_path = "wss://untrusted.invalid/" }) })
        {
            try { RemoteTerminalTransport.ValidateConnection(config, bad); throw new Exception("Unsafe session accepted"); }
            catch (InvalidDataException) { }
        }
        byte[] frame = new byte[10];
        BinaryPrimitives.WriteUInt64BigEndian(frame.AsSpan(1, 8), 1);
        Check(RemoteTerminalTransport.ValidInputFrame(frame, 1));
        Check(!RemoteTerminalTransport.ValidInputFrame(frame, 2));
        frame[0] = 1; Check(!RemoteTerminalTransport.ValidInputFrame(frame, 1));
        Check(!RemoteTerminalTransport.ValidInputFrame(new byte[RemoteTerminalTransport.MaxFrameBytes + 1], 0));
        config.RemoteTerminalTransportEnabled = false;
        await new RemoteTerminalTransport(new AgentApiClient(new RejectFactory()), new JsonlLogger(Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".jsonl"))).RunAsync(config, CancellationToken.None);
        Console.WriteLine("PASS terminal transport: default disabled, TLS/origin/session/mode, binary size/channel/sequence, no network when disabled.");
    }

    internal static async Task E2EAsync()
    {
        using JsonDocument info = JsonDocument.Parse(Environment.GetEnvironmentVariable("NIGHTOWL_TEST_TERMINAL_INFO")!);
        AgentConfig config = new() { RemoteTerminalTransportEnabled = true, ServerBaseUrl = Environment.GetEnvironmentVariable("NIGHTOWL_TEST_SERVER")! };
        if (!new Uri(config.ServerBaseUrl).IsLoopback) throw new Exception("Loopback test only");
        using var stop = new CancellationTokenSource(TimeSpan.FromSeconds(30));
        await new RemoteTerminalTransport(new AgentApiClient(new RejectFactory()), new JsonlLogger(Path.Combine(Path.GetTempPath(), "unused-terminal-test.jsonl")))
            .ConnectSyntheticAsync(config, info.RootElement, stop.Token);
        Console.WriteLine("PASS real .NET synthetic terminal transport");
    }

    private sealed class RejectFactory : IHttpClientFactory
    { public HttpClient CreateClient(string name) => throw new Exception("No HTTP expected in this test"); }
}
