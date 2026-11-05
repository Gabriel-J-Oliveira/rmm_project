using System.Diagnostics;
using System.Reflection;
using System.Text;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Tray;

internal static class LocalFootprintTests
{
    internal static void Run(string root)
    {
        void Check(bool ok,string message){if(!ok)throw new InvalidOperationException(message);}
        Check(NightOwlPaths.InteractiveTrayLogPath==NightOwlPaths.Current.TrayLogPath,"Fixture log must remain inside NIGHTOWL_HOME.");
        string? priorHome=Environment.GetEnvironmentVariable("NIGHTOWL_HOME");
        try
        {
            Environment.SetEnvironmentVariable("NIGHTOWL_HOME",null);
            Check(NightOwlPaths.InteractiveTrayLogPath==Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"NightOwl","Logs","agent-tray-user.jsonl"),
                "Normal interactive logging must use a user-owned directory, without widening ProgramData ACLs.");
        }
        finally { Environment.SetEnvironmentVariable("NIGHTOWL_HOME",priorHome); }
        string path=Path.Combine(root,"tail-fixture.jsonl");
        string[] fixture=Enumerable.Range(0,100000).Select(i=>$"line-{i:D6}-"+new string('x',90)).ToArray();
        File.WriteAllLines(path,fixture,new UTF8Encoding(false));
        long bytes=new FileInfo(path).Length;
        Stopwatch clock=Stopwatch.StartNew();
        List<string> reference=new(400);
        using(StreamReader reader=new(path))
            while(reader.ReadLine() is {} line){reference.Add(line);if(reference.Count>400)reference.RemoveAt(0);}
        double oldMs=clock.Elapsed.TotalMilliseconds;
        clock.Restart();
        LogTail tail=LogTailReader.Read(path);
        double newMs=clock.Elapsed.TotalMilliseconds;
        Check(tail.Lines.SequenceEqual(reference)&&tail.BytesRead<=1024*1024+1&&tail.Truncated,"Bounded tail lost the last400 normal-sized lines.");
        Console.WriteLine($"LOCAL_LOG_READ_MEASURED source_bytes={bytes} bounded_read_bytes={tail.BytesRead} old_ms={oldMs:F3} new_ms={newMs:F3} kind=synthetic_file_not_service_cpu");
        foreach(string text in new[]{"", "one", "one\r\ntwo\r\n", "one\ntwo\n", "\uFEFFum\n\u00e7\u00e3o\nfinal"})
        {
            File.WriteAllText(path,text,new UTF8Encoding(false));
            using StringReader reader=new(text.TrimStart('\uFEFF'));
            List<string> lines=new();while(reader.ReadLine() is {} line)lines.Add(line);
            Check(LogTailReader.Read(path).Lines.SequenceEqual(lines),"Small UTF8/CRLF/BOM tail changed.");
        }
        File.WriteAllText(path,new string('x',10000)+"\n{broken\n{\"event_type\":\"heartbeat.sent\",\"timestamp\":\"2026-10-30T12:00:00Z\"}\n");
        Check(LogTailReader.Read(path,400,1024).Lines[0]=="{broken","Partial first line must be discarded.");
        MethodInfo actualReader=typeof(AgentLocalState).GetMethod("ReadLastHeartbeatFromLog",BindingFlags.Static|BindingFlags.NonPublic)!;
        Check(Equals(actualReader.Invoke(null,new object[]{path}),DateTimeOffset.Parse("2026-10-30T12:00:00Z")),"Actual Tray reader lost valid heartbeat after malformed line.");
        File.AppendAllText(path,"{\"event_type\":\"heartbeat.sent\",\"timestamp\":\"2026-10-30T12:01:00Z\"}\n");
        Check(Equals(actualReader.Invoke(null,new object[]{path}),DateTimeOffset.Parse("2026-10-30T12:01:00Z")),"Tray did not observe appended heartbeat.");
        File.WriteAllText(path,"{\"event_type\":\"heartbeat.sent\",\"timestamp\":\"2026-10-30T12:02:00Z\"}\n");
        Check(Equals(actualReader.Invoke(null,new object[]{path}),DateTimeOffset.Parse("2026-10-30T12:02:00Z")),"Tray did not recover from rotation/truncation.");
        ServiceObservation service=new();TimeSpan now=TimeSpan.Zero;
        Check(!service.Observe("StartPending",now).Exit,"Tray must tolerate startup pending.");
        Check(!service.Observe("Running",TimeSpan.FromSeconds(30)).Exit,"Tray did not recover from startup.");
        Check(!service.Observe("Running",TimeSpan.FromSeconds(60)).Changed,"Repeated running status must not generate another log.");
        Check(!service.Observe("Stopped",TimeSpan.FromSeconds(90)).Exit,"Tray must tolerate a short restart.");
        Check(!service.Observe("Running",TimeSpan.FromSeconds(120)).Exit,"Tray did not reconnect after restart.");
        Check(!service.Observe("Stopped",TimeSpan.FromSeconds(150)).Exit,"Recovery grace not reset.");
        Check(service.Observe("Stopped",TimeSpan.FromSeconds(270)).Exit,"Recovery wait must be bounded.");
        Check(new ServiceObservation().Observe(null,now).Exit,"Missing service must not keep Tray alive.");
        ServiceObservation pending=new();
        Check(!pending.Observe("StopPending",now).Exit&&!pending.Observe("Stopped",TimeSpan.FromSeconds(90)).Exit&&pending.Observe("StartPending",TimeSpan.FromSeconds(121)).Exit,
            "Status transitions must not extend recovery grace forever.");
        string legacy=NightOwlPaths.Current.LegacyConfigPath;
        Directory.CreateDirectory(Path.GetDirectoryName(legacy)!);
        File.WriteAllText(legacy,"{\"machineId\":\"synthetic-local\",\"agentVersion\":\"synthetic-version\"}");
        string[] beforeFiles=Directory.GetFiles(root,"*",SearchOption.AllDirectories).Order().ToArray();
        AgentLocalState local=AgentLocalState.Load();
        Check(local.MachineId=="synthetic-local"&&local.AgentVersion=="synthetic-version","Read-only refresh lost legacy config fallback.");
        Check(Directory.GetFiles(root,"*",SearchOption.AllDirectories).Order().SequenceEqual(beforeFiles)&&!File.Exists(NightOwlPaths.Current.IdentityPath),
            "Tray refresh must not bootstrap/migrate identity or write files.");
        Directory.CreateDirectory(NightOwlPaths.Current.StateDir);
        Directory.CreateDirectory(NightOwlPaths.Current.LogsDir);
        File.WriteAllText(NightOwlPaths.Current.StatePath,"{\"lastHeartbeatAt\":\"2026-10-30T12:03:00Z\"}");
        File.WriteAllText(NightOwlPaths.Current.AgentLogPath,"{\"event_type\":\"heartbeat.sent\",\"timestamp\":\"2026-10-30T12:00:00Z\"}\n"+new string('x',2*1024*1024));
        Check(AgentLocalState.Load().LastHeartbeatAt==DateTimeOffset.Parse("2026-10-30T12:03:00Z"),
            "Truncated log must preserve authoritative heartbeat from state, never invent a newer value.");
        string publicPath = TrayRuntimeStatus.PathFor(NightOwlPaths.Current);
        File.WriteAllText(publicPath, System.Text.Json.JsonSerializer.Serialize(new TrayRuntimeStatus
        {
            MachineId="public-fixture", AgentVersion="public-version", ServerBaseUrl="https://isolated.invalid/prefix",
            LastHeartbeatAt=DateTimeOffset.Parse("2026-10-30T12:04:00Z")
        }, new System.Text.Json.JsonSerializerOptions(System.Text.Json.JsonSerializerDefaults.Web)));
        using (FileStream blockedConfig = new(legacy,FileMode.Open,FileAccess.Read,FileShare.None))
        {
            AgentLocalState projected = AgentLocalState.Load();
            Check(projected.MachineId=="public-fixture"&&projected.ServerBaseUrl=="https://isolated.invalid/prefix"
                &&projected.LastHeartbeatAt==DateTimeOffset.Parse("2026-10-30T12:04:00Z"),
                "Tray must use sanitized status without access to protected config/state.");
        }
        File.WriteAllText(publicPath,"{\"schemaVersion\":\"invalid\"}");
        Check(AgentLocalState.Load().MachineId=="synthetic-local","Malformed projection must fall back safely.");
        File.WriteAllText(publicPath,new string('x',2*1024*1024));
        Check(AgentLocalState.Load().MachineId=="synthetic-local","Oversized projection must not consume unbounded memory or break fallback.");
        File.Delete(publicPath);
        Console.WriteLine("PASS Shared/Tray footprint: bounded tail, actual reader, malformed/UTF8/rotation, pending/restart/reconnection, bounded grace, transition-only logging.");
    }
}
