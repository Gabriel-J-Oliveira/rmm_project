using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class ConPtyTests
{
    private static void Check(bool value, string code) { if (!value) throw new Exception(code); }
    private static AgentConfig Config()
    {
        var facts = TerminalIdentity.CurrentFacts();
        return new() { MachineId = Guid.NewGuid().ToString(), TerminalConPtyEnabled = true, RemoteTerminalTransportEnabled = true,
            TerminalAuthorizedUserSid = facts.Sid, TerminalWindowsSessionId = facts.SessionId };
    }
    private static TerminalAuthorization Authorization(AgentConfig config, int seconds = 120) =>
        new(Guid.NewGuid(), config.MachineId, "explicit-local-test-operator", DateTimeOffset.UtcNow.AddSeconds(seconds));

    internal static async Task RunAsync()
    {
        AgentConfig config = Config();
        var facts = TerminalIdentity.CurrentFacts();
        foreach (var bad in new[] { facts with { Sid = "S-1-5-18" }, facts with { Sid = "S-1-5-19" }, facts with { Elevated = true },
            facts with { Administrator = true }, facts with { Integrity = 12288 }, facts with { Interactive = false }, facts with { SessionId = 0 }, facts with { DangerousPrivileges = true } })
        {
            try { TerminalIdentity.ValidateFacts(bad, config); throw new Exception("Unsafe identity accepted"); }
            catch (InvalidOperationException) { }
        }
        var authorization = Authorization(config);
        try { await ConPtyTerminal.StartAsync(config, authorization, 80, 24, CancellationToken.None); throw new Exception("Consent absent accepted"); }
        catch (InvalidOperationException) { }
        try { TerminalIdentity.ApproveCurrentUser(config, authorization, false); throw new Exception("Implicit consent accepted"); }
        catch (InvalidOperationException) { }
        try { await ConPtyTerminal.StartAsync(config, authorization with { MachineId = "wrong" }, 80, 24, CancellationToken.None); throw new Exception("Wrong session accepted"); }
        catch (InvalidOperationException) { }
        TerminalIdentity.ValidateFacts(facts, config); // No token sanitizing or elevation to make a blocked test run.
        List<string> receipts = new();
        List<ConPtyObservation> observations = new();
        List<double> commandLatencies = new();
        List<double> echoLatencies = new();
        try
        {
            receipts.Add(TerminalIdentity.ApproveCurrentUser(config, authorization, true));
            Stopwatch startup = Stopwatch.StartNew();
            await using (var terminal = await ConPtyTerminal.StartAsync(config, authorization, 80, 24, CancellationToken.None))
            {
                using var capture = new Capture(terminal);
                string initialized = "READY_" + Guid.NewGuid().ToString("N");
                var marker = capture.Marker(initialized);
                await terminal.WriteAsync(Command(initialized), CancellationToken.None);
                double initializedAt = await marker.WaitAsync(TimeSpan.FromSeconds(20));
                Console.WriteLine("CONPTY_STARTUP=" + JsonSerializer.Serialize(new { native = terminal.Observe(), powershell_marker_ms = initializedAt - capture.Created,
                    total_open_to_marker_ms = startup.Elapsed.TotalMilliseconds, identity = "explicit-current-medium-token", elevated = facts.Elevated, integrity = facts.Integrity }));
                marker = capture.Marker("PID_" + terminal.ProcessId);
                await terminal.WriteAsync("[Console]::WriteLine(('P'+'ID_')+(Get-Process -Id $PID).Id)\r"u8.ToArray(), CancellationToken.None);
                await marker.WaitAsync(TimeSpan.FromSeconds(10));
                try { await ConPtyTerminal.StartAsync(config, Authorization(config), 80, 24, CancellationToken.None); throw new Exception("Concurrent session accepted"); }
                catch (InvalidOperationException ex) { Check(ex.Message == "terminal_session_limit", "Concurrent rejection invalid"); }
                terminal.Resize(132, 44);
                try { terminal.Resize(501, 1); throw new Exception("Unbounded resize accepted"); } catch (InvalidDataException) { }
                string unicode = "UNI_" + Guid.NewGuid().ToString("N");
                marker = capture.Marker(unicode + "ação漢字🙂 $;&|<>");
                await terminal.WriteAsync(Encoding.UTF8.GetBytes($"[Console]::WriteLine('{unicode[..8]}'+'{unicode[8..]}ação漢字🙂 $;&|<>')\r"), CancellationToken.None);
                try { await marker.WaitAsync(TimeSpan.FromSeconds(10)); }
                catch (TimeoutException)
                {
                    Console.WriteLine("CONPTY_UNICODE_DIAGNOSTIC=" + JsonSerializer.Serialize(new { marker = capture.Text.Contains(unicode),
                        cjk = capture.Text.Contains("漢字"), accent = capture.Text.Contains("ação"), emoji = capture.Text.Contains("🙂"),
                        error_category_present = capture.Text.Contains("Exception") }));
                    throw;
                }
                string first = "FIRST_" + Guid.NewGuid().ToString("N"), second = "SECOND_" + Guid.NewGuid().ToString("N");
                Task<double> early = capture.Marker(first), late = capture.Marker(second);
                await terminal.WriteAsync(Encoding.UTF8.GetBytes(Expression(first) + ";Start-Sleep -Milliseconds 350;" + Expression(second) + "\r"), CancellationToken.None);
                double earlyAt = await early.WaitAsync(TimeSpan.FromSeconds(10));
                Check(!late.IsCompleted, "Output waited for command completion");
                Check(await late.WaitAsync(TimeSpan.FromSeconds(10)) - earlyAt >= 250, "Incremental timing invalid");
                for (int index = 0; index < 100; index++)
                {
                    string echo = "ECHO_" + Guid.NewGuid().ToString("N");
                    marker = capture.Marker(echo);
                    double sent = Now();
                    await terminal.WriteAsync(Encoding.UTF8.GetBytes(echo), CancellationToken.None);
                    echoLatencies.Add(await marker.WaitAsync(TimeSpan.FromSeconds(10)) - sent);
                    await terminal.WriteAsync(Encoding.UTF8.GetBytes(new string('\b', echo.Length) + "\r"), CancellationToken.None);
                }
                for (int index = 0; index < 100; index++)
                {
                    string value = "READ_" + Guid.NewGuid().ToString("N");
                    marker = capture.Marker(value);
                    double sent = Now();
                    await terminal.WriteAsync(Command(value), CancellationToken.None);
                    commandLatencies.Add(await marker.WaitAsync(TimeSpan.FromSeconds(10)) - sent);
                }
                string childPrefix = "CHILD_" + Guid.NewGuid().ToString("N") + "_";
                marker = capture.Marker(childPrefix);
                string childCommand = "$p=Start-Process -WindowStyle Hidden -FilePath ($env:SystemRoot+'\\System32\\WindowsPowerShell\\v1.0\\powershell.exe') -ArgumentList '-NoProfile -Command Start-Sleep -Seconds 30' -PassThru;" +
                    $"[Console]::WriteLine('{childPrefix[..8]}'+'{childPrefix[8..]}'+$p.Id)\r";
                await terminal.WriteAsync(Encoding.UTF8.GetBytes(childCommand), CancellationToken.None);
                await marker.WaitAsync(TimeSpan.FromSeconds(10));
                int childId = int.Parse(Regex.Match(capture.Text, Regex.Escape(childPrefix) + @"(\d+)").Groups[1].Value);
                using Process child = Process.GetProcessById(childId);
                Check(!child.HasExited, "Child did not start");
                await terminal.DisposeAsync();
                await child.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
                Check(child.HasExited, "Owned child survived closure");
                await capture.Completion.WaitAsync(TimeSpan.FromSeconds(5));
                observations.Add(terminal.Observe());
                Console.WriteLine("CONPTY_CHUNKS=" + JsonSerializer.Serialize(new { blocks = capture.Gaps.Count, min_gap_ms = capture.Gaps.DefaultIfEmpty().Min(),
                    max_gap_ms = capture.Gaps.DefaultIfEmpty().Max() }));
            }
            // A consumed receipt cannot launch another process for the same authorization.
            try { await ConPtyTerminal.StartAsync(config, authorization, 80, 24, CancellationToken.None); throw new Exception("Consent replay accepted"); }
            catch (InvalidOperationException) { }
            foreach (bool timeout in new[] { false, true })
            {
                var next = Authorization(config, timeout ? 3 : 30);
                receipts.Add(TerminalIdentity.ApproveCurrentUser(config, next, true));
                using var stop = new CancellationTokenSource();
                await using var terminal = await ConPtyTerminal.StartAsync(config, next, 80, 24, stop.Token);
                using var capture = new Capture(terminal);
                if (!timeout) stop.Cancel();
                await terminal.Completion.WaitAsync(TimeSpan.FromSeconds(15));
                observations.Add(terminal.Observe());
            }
            // Exercise actual native output and slow-consumer termination with bounded
            // test budgets; production always uses the closed 16 MiB ceiling.
            var limited = Authorization(config, 30);
            receipts.Add(TerminalIdentity.ApproveCurrentUser(config, limited, true));
            await using (var terminal = await ConPtyTerminal.StartAsync(config, limited, 80, 24, CancellationToken.None, 1024))
            {
                using var capture = new Capture(terminal);
                await terminal.WriteAsync("for($i=0;$i -lt 100;$i++){[Console]::WriteLine([guid]::NewGuid().ToString())}\r"u8.ToArray(), CancellationToken.None);
                await terminal.Completion.WaitAsync(TimeSpan.FromSeconds(15));
                Check(terminal.Observe().OutputBytes > 1024, "Native output ceiling was not exercised");
            }
            var revoked = Authorization(config, 30);
            string revokedPath = TerminalIdentity.ApproveCurrentUser(config, revoked, true); receipts.Add(revokedPath);
            await using (var terminal = await ConPtyTerminal.StartAsync(config, revoked, 80, 24, CancellationToken.None))
            {
                using var capture = new Capture(terminal);
                File.Delete(revokedPath + ".used");
                await terminal.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            var inputLimited = Authorization(config, 30);
            receipts.Add(TerminalIdentity.ApproveCurrentUser(config, inputLimited, true));
            await using (var terminal = await ConPtyTerminal.StartAsync(config, inputLimited, 80, 24, CancellationToken.None, maxInputBytes: 1024))
            {
                using var capture = new Capture(terminal);
                try { await terminal.WriteAsync(new byte[1025], CancellationToken.None); throw new Exception("Input total ceiling accepted"); }
                catch (InvalidDataException) { }
                await terminal.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            var pressure = Authorization(config, 30);
            receipts.Add(TerminalIdentity.ApproveCurrentUser(config, pressure, true));
            await using (var terminal = await ConPtyTerminal.StartAsync(config, pressure, 80, 24, CancellationToken.None))
            {
                using (var capture = new Capture(terminal))
                {
                    string prepared = "PRESSURE_" + Guid.NewGuid().ToString("N");
                    var preparedTask = capture.Marker(prepared);
                    await terminal.WriteAsync(Command(prepared), CancellationToken.None);
                    await preparedTask.WaitAsync(TimeSpan.FromSeconds(10));
                }
                await terminal.WriteAsync("while($true){[Console]::WriteLine([guid]::NewGuid().ToString());Start-Sleep -Milliseconds 10}\r"u8.ToArray(), CancellationToken.None);
                await terminal.Completion.WaitAsync(TimeSpan.FromSeconds(10));
                Check(terminal.Output.Count <= 16, "Output queue exceeded bound");
                Check(terminal.Observe().OutputBytes < ConPtyTerminal.MaxOutputBytes, "Pressure did not close before lifetime/total output limit");
            }
            var sorted = commandLatencies.Order().ToArray();
            var echoes = echoLatencies.Order().ToArray();
            Console.WriteLine("CONPTY_LOCAL_MEASURED=" + JsonSerializer.Serialize(new { commands = sorted.Length,
                p50_ms = (sorted[49] + sorted[50]) / 2, p95_ms = sorted[94], echo_samples = echoes.Length,
                echo_p50_ms = (echoes[49] + echoes[50]) / 2, echo_p95_ms = echoes[94], sessions = observations,
                meaning = "local_console_marker_not_input_echo_no_network_no_production_extrapolation" }));
            Console.WriteLine("PASS ConPTY: consent/identity/replay/concurrency, native PowerShell read, Unicode, incremental output, resize, timeout, cancel, job child cleanup, output limit and pressure.");
        }
        finally
        {
            foreach (string receipt in receipts)
                foreach (string file in new[] { receipt, receipt + ".used" }) if (File.Exists(file)) File.Delete(file);
        }
    }

    internal static async Task E2EAsync()
    {
        using JsonDocument document = JsonDocument.Parse(Environment.GetEnvironmentVariable("NIGHTOWL_TEST_TERMINAL_INFO")!);
        JsonElement session = document.RootElement;
        AgentConfig config = Config();
        config.ServerBaseUrl = Environment.GetEnvironmentVariable("NIGHTOWL_TEST_SERVER")!;
        if (!new Uri(config.ServerBaseUrl).IsLoopback) throw new Exception("Loopback only");
        config.MachineId = session.GetProperty("machine_id").GetString()!;
        var auth = new TerminalAuthorization(Guid.Parse(session.GetProperty("session_id").GetString()!), config.MachineId,
            session.GetProperty("operator_id").GetString()!, session.GetProperty("expires_at").GetDateTimeOffset());
        string receipt = TerminalIdentity.ApproveCurrentUser(config, auth, true); // Explicit test authorization, never production transport behavior.
        try
        {
            using var stop = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            await new RemoteTerminalTransport(new AgentApiClient(new NoHttp()), new JsonlLogger(Path.Combine(Path.GetTempPath(), "unused-conpty-test.jsonl")))
                .ConnectAsync(config, session, stop.Token);
            Console.WriteLine("PASS real ConPTY WebSocket cleanup");
        }
        finally { foreach (string file in new[] { receipt, receipt + ".used" }) if (File.Exists(file)) File.Delete(file); }
    }

    private static string Expression(string marker) => $"[Console]::WriteLine('{marker[..8]}'+'{marker[8..]}')";
    private static byte[] Command(string marker) => Encoding.UTF8.GetBytes(Expression(marker) + "\r");
    private static double Now() => Stopwatch.GetTimestamp() * 1000d / Stopwatch.Frequency;
    private sealed class NoHttp : IHttpClientFactory { public HttpClient CreateClient(string name) => throw new Exception("No HTTP expected"); }
    private sealed class Capture : IDisposable
    {
        private readonly ConPtyTerminal _terminal;
        private readonly StringBuilder _text = new();
        private readonly ConcurrentDictionary<string, TaskCompletionSource<double>> _markers = new();
        private readonly CancellationTokenSource _stop = new();
        private readonly Task _pump;
        public Task Completion => _pump;
        public double Created { get; } = Now();
        public List<double> Gaps { get; } = new();
        public string Text { get { lock (_text) return _text.ToString(); } }
        public Capture(ConPtyTerminal terminal)
        {
            _terminal = terminal;
            _pump = Task.Run(async () =>
            {
                var decoder = Encoding.UTF8.GetDecoder(); char[] chars = new char[32768]; double last = Created;
                try
                {
                    await foreach (byte[] bytes in terminal.Output.ReadAllAsync(_stop.Token))
                    {
                        double at = Now(); Gaps.Add(at - last); last = at;
                        string value = new(chars, 0, decoder.GetChars(bytes, chars, false));
                        string text;
                        lock (_text) { _text.Append(value); if (_text.Length > 131072) _text.Remove(0, _text.Length - 65536); text = _text.ToString(); }
                        foreach (var marker in _markers)
                            if (text.Contains(marker.Key, StringComparison.Ordinal))
                            { marker.Value.TrySetResult(at); _markers.TryRemove(marker.Key, out _); }
                        if (value.Contains("\x1b[6n", StringComparison.Ordinal)) await _terminal.WriteAsync("\x1b[1;1R"u8.ToArray(), _stop.Token);
                    }
                }
                catch (OperationCanceledException) { }
            });
        }
        public Task<double> Marker(string value)
        {
            var marker = new TaskCompletionSource<double>(TaskCreationOptions.RunContinuationsAsynchronously);
            _markers[value] = marker; return marker.Task;
        }
        public void Dispose() { _stop.Cancel(); try { _pump.GetAwaiter().GetResult(); } catch (Exception) { } _stop.Dispose(); }
    }
}
