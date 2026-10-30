using System.Reflection;
using System.Text.Json;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class ConfigurationConsolidationTests
{
    internal static void Run()
    {
        void Check(bool ok,string message) { if(!ok)throw new InvalidOperationException(message); }
        MethodInfo normalize=typeof(ConfigService).GetMethod("Normalize",BindingFlags.Static|BindingFlags.NonPublic)!;
        foreach(int version in new[]{6,999})
        {
            AgentConfig config=new(){ConfigMigrationVersion=version,AllowedJobTypes=new(){" ping ","PING","",null!,"collect_logs"},
                TelemetrySampleSeconds=1,TelemetryFlushSeconds=int.MaxValue,TelemetryMode="legacy",TelemetryEnabled=false,
                ServerBaseUrl="https://isolated.invalid/",Intervals=new(){HeartbeatSeconds=333,CollectSeconds=4444,JobsSeconds=78}};
            normalize.Invoke(null,new object[]{config});
            ConfigService.ApplyConfigMigrations(config);
            Check(config.AllowedJobTypes.SequenceEqual(new[]{"ping","collect_logs"}),"Allowlist order, deduplication or explicit removals changed.");
            Check(config.TelemetrySampleSeconds==60&&config.TelemetryFlushSeconds==86400,"Legacy normalization bounds changed.");
            Check(!config.TelemetryEnabled&&config.TelemetryMode=="legacy"&&config.Intervals.JobsSeconds==78,"Explicit legacy settings changed.");
            string json=JsonSerializer.Serialize(config);
            Check(JsonSerializer.Serialize(JsonSerializer.Deserialize<AgentConfig>(json))==json,"Public config serialization changed.");
        }
        foreach((int sample,int flush) in new[]{(60,60),(3600,86400)})
            Check(TelemetrySettings.Parse(new(){{"telemetryEnabled",true},{"telemetrySampleSeconds",sample},{"telemetryFlushSeconds",flush}})==new TelemetrySettings(true,sample,flush),"Boundary rejected.");
        foreach((int sample,int flush) in new[]{(59,60),(3601,60),(60,59),(60,86401)})
        {
            bool rejected=false;
            try { TelemetrySettings.Parse(new(){{"telemetryEnabled",true},{"telemetrySampleSeconds",sample},{"telemetryFlushSeconds",flush}}); }
            catch(InvalidOperationException){rejected=true;}
            Check(rejected,"Invalid live telemetry parameters accepted.");
        }
        AgentConfig missing=ConfigService.DeserializeExistingConfig("{}");
        Check(!missing.TelemetryEnabled&&missing.TelemetryMode=="legacy"&&missing.TelemetryFlushSeconds==3600,"Missing legacy flags became consent.");
        Console.WriteLine("PASS configuration consolidation: allowlist, strict/clamped bounds, serialization, missing legacy flags, preserved cadences.");
    }
}
