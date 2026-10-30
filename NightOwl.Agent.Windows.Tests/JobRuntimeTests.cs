using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class JobRuntimeTests
{
    private static void Check(bool ok,string message) { if(!ok) throw new InvalidOperationException(message); }
    internal static async Task RunAsync()
    {
        await IndependentDispatch();
        ContractsAndJournals();
        await Software();
        await Recovery();
        await OwnedChild();
        string services=JsonSerializer.Serialize(WindowsServicesCollector.Collect(new[]{"EventLog"},1,false));
        Check(services.Contains("schema_version") && services.Contains("read_only"),"Service read contract missing.");
        Console.WriteLine("PASS jobs runtime: independent dispatch, durable inbox/outcomes, duplicate/ACK retry, ownership, bounded contracts, software trust/flag/synthetic verification, owned child cancellation, local read-only services.");
    }
    private static AgentConfig Config(string root) => new() {MachineId=Guid.NewGuid().ToString("D"),AgentToken="synthetic-only",
        ServerBaseUrl="https://isolated.invalid",JobsPullUrl="https://isolated.invalid/pull",JobsResultUrl="https://isolated.invalid/result",
        StatePath=Path.Combine(root,"state.json"),JobsPath=Path.Combine(root,"jobs"),LogPath=Path.Combine(root,"log.jsonl"),PendingResultsPath=Path.Combine(root,"results")};
    private static async Task IndependentDispatch()
    {
        string root=Path.Combine(Path.GetTempPath(),"nightowl-jobs-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        AgentConfig config=Config(root);
        JobStore store=new(config.JobsPath);
        JsonlLogger logger=new(config.LogPath);
        JobExecutionCoordinator coordinator=new(new JobExecutionPolicy(store),logger);
        PendingResultQueue queue=new(config.PendingResultsPath);
        AgentJobRequest first=new(){Id=Guid.NewGuid().ToString("D"),Type="collect_software"};
        AgentJobRequest second=new(){Id=Guid.NewGuid().ToString("D"),Type="collect_logs"};
        int pulls=0,executions=0,signals=0,acks=0;
        TaskCompletionSource entered=new(TaskCreationOptions.RunContinuationsAsynchronously),release=new(TaskCreationOptions.RunContinuationsAsynchronously);
        AgentApiClient api=new(new Factory(async (request,ct)=>
        {
            string path=request.RequestUri!.AbsolutePath;
            if(path.EndsWith("/signal/")) { Interlocked.Increment(ref signals); return Json(new {protocol=2,available=pulls<2}); }
            if(path=="/pull") return Json(new {jobs=new[]{Interlocked.Increment(ref pulls)==1 ? first : second}});
            if(path.EndsWith("/started/")) return Json(new {job_id=path.Split('/')[4],state="running"});
            using JsonDocument document=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct));
            if(Interlocked.Increment(ref acks)==1) return new HttpResponseMessage(HttpStatusCode.ServiceUnavailable){Content=new StringContent("sanitized")};
            return Json(new {job_id=document.RootElement.GetProperty("job_id").GetString(),result_id=request.Headers.GetValues("Idempotency-Key").First()});
        }));
        using SemaphoreSlim heavy=new(1,1);
        using CancellationTokenSource stop=new();
        JobRuntime runtime=new(api,coordinator,async (_,job,ct)=>
        {
            Interlocked.Increment(ref executions);
            if(job.Id==first.Id) { entered.SetResult(); await release.Task.WaitAsync(ct); }
            return new JobExecutionResult {JobId=job.Id,Status="completed",StartedAt=DateTimeOffset.UtcNow,FinishedAt=DateTimeOffset.UtcNow,Result=new {type=job.Type}};
        },queue,logger,heavy);
        Task run=runtime.RunAsync(config,stop.Token);
        try
        {
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
            await Task.Delay(2300);
            Check(signals>=2 && pulls==2 && executions==1,"Blocked execution prevented dispatch or heavy jobs overlapped.");
            Check(new JobJournal(config,store).Pending().Count==2,"Inbox was not durable during blocked execution.");
            release.SetResult();
            await Until(()=>executions==2 && acks>=3 && queue.LoadAll().Count==0,15);
            Check(executions==2,"ACK loss caused reexecution.");
            await Until(()=>!new JobJournal(config,store).Results().Any(),5);
        }
        finally {release.TrySetResult();stop.Cancel();try{await run;}catch(OperationCanceledException){} Directory.Delete(root,true);}
    }
    private static void ContractsAndJournals()
    {
        string root=Path.Combine(Path.GetTempPath(),"nightowl-job-journal-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            AgentConfig config=Config(root);
            JobStore store=new(config.JobsPath);
            JobExecutionPolicy policy=new(store);
            AgentJobRequest ping=new(){Id=Guid.NewGuid().ToString("D"),Type="ping",Payload=new(){{"target","127.0.0.1"},{"count",2}}};
            Check(policy.Prepare(config,ping).ShouldExecute,"UI ping count rejected.");
            AgentJobRequest logs=new(){Id=Guid.NewGuid().ToString("D"),Type="collect_logs",Payload=new(){{"lines",120}}};
            Check(policy.Prepare(config,logs).ShouldExecute,"Legacy log alias rejected.");
            logs.Payload["max_lines"]=121;
            Check(!policy.Prepare(config,logs).ShouldExecute,"Conflicting log aliases accepted.");
            JobJournal journal=new(config,store);
            journal.Accept(ping);journal.Accept(ping);
            Check(new JobJournal(config,store).Pending().Count==1,"Inbox replay duplicated work.");
            ping.Payload["count"]=3;
            try{journal.Accept(ping);throw new Exception("Changed job accepted.");}catch(InvalidDataException){}
            JobExecutionResult result=new(){JobId=ping.Id,Status="completed",Stdout="exact-original"};
            policy.MarkFinal(config,ping,result);
            Check(new JobJournal(config,store).Results().Single().Stdout=="exact-original","Crash gap lost exact result.");
            string identityRoot=Directory.GetDirectories(Path.Combine(store.DirectoryPath,"dispatch-v2")).Single();
            File.WriteAllText(Path.Combine(identityRoot,"inbox",Guid.NewGuid()+".json"),"{");
            File.WriteAllText(Path.Combine(identityRoot,"outcomes",Guid.NewGuid()+".json"),"{");
            Check(journal.Pending().Count==1 && journal.Results().Count()==1 && journal.Quarantined==2,"Corrupt journals blocked healthy work/results.");
            policy.MarkFinal(config,ping,new(){JobId=ping.Id,Status="duplicate"});
            Check(store.Load(ping.Id)!.Status=="completed","Duplicate destroyed final state.");
            PendingResultQueue queue=new(Path.Combine(root,"bounded"),maxRecords:1);
            queue.Enqueue("ping",result,true,"synthetic-result");
            try{queue.Enqueue("ping",result,true);throw new Exception("Unbounded critical queue.");}catch(InvalidOperationException error)when(error.Message==JobErrorCodes.ResultQueueFull){}
            queue.MarkSent(queue.LoadAll().Single());
            Check(queue.IsAcknowledged("synthetic-result"),"ACK archive cannot reconcile a replayed outcome.");
        }
        finally{Directory.Delete(root,true);}
    }
    private static async Task Software()
    {
        string root=Path.Combine(Path.GetTempPath(),"nightowl-software-synthetic-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            AgentConfig config=Config(root);
            byte[] fake=Encoding.UTF8.GetBytes("synthetic-not-an-installer");
            string hash=Convert.ToHexString(SHA256.HashData(fake));
            ApprovedSoftware package=new("synthetic","https://approved.invalid/package.msi",hash,fake.Length,Guid.NewGuid().ToString("D"),"1.0",new string('A',64));
            byte[] catalog=JsonSerializer.SerializeToUtf8Bytes(new[]{package},new JsonSerializerOptions(JsonSerializerDefaults.Web));
            await File.WriteAllBytesAsync(Path.Combine(root,"software-catalog.json"),catalog);
            config.SoftwareCatalogSha256=Convert.ToHexString(SHA256.HashData(catalog));
            bool installed=false;int calls=0;
            SoftwareInstallation executor=new((_,_)=>Task.FromResult(fake),(_,_)=>true,(_,_)=>installed,(_,args,_)=>
            {Check(args.Contains("/qn /norestart ALLUSERS=1"),"Silent machine-only arguments changed.");calls++;installed=true;return Task.FromResult(3010);});
            try{await executor.InstallAsync(config,"synthetic",CancellationToken.None);throw new Exception("Disabled installation ran.");}catch(InvalidOperationException error)when(error.Message=="SOFTWARE_INSTALL_DISABLED"){}
            Check(calls==0,"Feature flag bypassed.");
            config.SoftwareInstallEnabled=true;
            config.SoftwareCatalogSha256=new string('0',64);
            try{await executor.InstallAsync(config,"synthetic",CancellationToken.None);throw new Exception("Untrusted catalog ran.");}catch(InvalidDataException){}
            config.SoftwareCatalogSha256=Convert.ToHexString(SHA256.HashData(catalog));
            SoftwareInstallation tampered=new((_,_)=>Task.FromResult(Encoding.UTF8.GetBytes("tampered")),(_,_)=>true,(_,_)=>false,
                (_,_,_)=>throw new Exception("Tampered package reached executor."));
            try{await tampered.InstallAsync(config,"synthetic",CancellationToken.None);throw new Exception("Tampered package accepted.");}catch(InvalidDataException){}
            SoftwareInstallation unsigned=new((_,_)=>Task.FromResult(fake),(_,_)=>false,(_,_)=>false,
                (_,_,_)=>throw new Exception("Unsigned package reached executor."));
            try{await unsigned.InstallAsync(config,"synthetic",CancellationToken.None);throw new Exception("Unsigned package accepted.");}catch(InvalidDataException){}
            object result=await executor.InstallAsync(config,"synthetic",CancellationToken.None);
            Check(JsonSerializer.Serialize(result).Contains("\"reboot_required\":true") && calls==1,"Synthetic confirmation/reboot contract failed.");
            await executor.InstallAsync(config,"synthetic",CancellationToken.None);
            Check(calls==1,"Already installed package executed twice.");
        }
        finally{Directory.Delete(root,true);}
    }
    private static async Task OwnedChild()
    {
        using CancellationTokenSource timeout=new(TimeSpan.FromMilliseconds(250));
        Stopwatch clock=Stopwatch.StartNew();
        try{await OwnedProcess.RunAsync(Path.Combine(Environment.SystemDirectory,"cmd.exe"),"/d /c ping -n 30 127.0.0.1 >nul",timeout.Token);throw new Exception("Owned process ignored cancellation.");}
        catch(OperationCanceledException){}
        Check(clock.Elapsed<TimeSpan.FromSeconds(5),"Owned tree did not terminate promptly.");
        Check(await OwnedProcess.RunAsync(Path.Combine(Environment.SystemDirectory,"cmd.exe"),"/d /c exit 0",CancellationToken.None)==0,"Owned process exit code lost.");
    }
    private static async Task Recovery()
    {
        string root=Path.Combine(Path.GetTempPath(),"nightowl-job-recovery-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            AgentConfig config=Config(root);
            JobStore store=new(config.JobsPath);
            string id=Guid.NewGuid().ToString("D");
            store.Mark(id,"repair_agent","running",1);
            JobStateRecord state=store.MarkExternalRunnerStarted(id,"repair_agent","synthetic-runner",60);
            state.ExternalRunnerStartedAt=DateTimeOffset.UtcNow.AddHours(-1);
            store.Save(state);
            JobExecutionCoordinator coordinator=new(new JobExecutionPolicy(store),new(config.LogPath));
            PendingResultQueue queue=new(config.PendingResultsPath);
            await coordinator.RecoverInterruptedJobsAsync(config,queue,CancellationToken.None,Path.Combine(root,"update.json"));
            Check(store.Load(id)!.Status=="running" && queue.LoadAll().Count==0,"Unknown live external runner was falsely finalized after deadline.");
            JobStateRecord restart=new(){JobId=Guid.NewGuid().ToString("D"),JobType="restart_agent",Status="running",UpdatedAt=DateTimeOffset.UtcNow};
            Check(!JobExecutionCoordinator.RestartConfirmed(restart,restart.UpdatedAt.AddSeconds(-1)),"Restart confirmed in the old process.");
            Check(JobExecutionCoordinator.RestartConfirmed(restart,restart.UpdatedAt.AddSeconds(1)),"A new process cannot confirm restart.");
        }
        finally{Directory.Delete(root,true);}
    }
    private static async Task Until(Func<bool> predicate,int seconds)
    {using CancellationTokenSource timeout=new(TimeSpan.FromSeconds(seconds));while(!predicate())await Task.Delay(50,timeout.Token);}
    internal static async Task ProtocolE2E()
    {
        string root=Path.Combine(Path.GetTempPath(),"nightowl-jobs-e2e-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        using CancellationTokenSource stop=new(TimeSpan.FromSeconds(90));
        Task run=Task.CompletedTask;
        try
        {
            AgentConfig config=Config(root);
            config.AgentToken=Environment.GetEnvironmentVariable("NIGHTOWL_TEST_TOKEN")!;
            config.MachineId=Environment.GetEnvironmentVariable("NIGHTOWL_TEST_MACHINE")!;
            config.ServerBaseUrl=Environment.GetEnvironmentVariable("NIGHTOWL_TEST_SERVER")!;
            Check(config.ServerBaseUrl.StartsWith("http://localhost:") || config.ServerBaseUrl.StartsWith("http://127.0.0.1:"),"Job E2E is restricted to loopback.");
            config.JobsPullUrl=config.ServerBaseUrl+"/api/agent/jobs/pull/";
            config.JobsResultUrl=config.ServerBaseUrl+"/api/agent/jobs/result/";
            AgentApiClient api=new(new RealFactory());
            JsonlLogger logger=new(config.LogPath);
            JobExecutionPolicy policy=new(new JobStore(config.JobsPath));
            JobExecutionCoordinator coordinator=new(policy,logger);
            JobExecutor executor=new(new WindowsInventoryCollector(logger,_=>throw new Exception("No inventory in job E2E.")),logger,policy);
            using SemaphoreSlim heavy=new(1,1);
            JobRuntime runtime=new(api,coordinator,executor.ExecuteAsync,new(config.PendingResultsPath),logger,heavy);
            ConcurrentDictionary<string,Dictionary<string,DateTimeOffset>> observations=new();
            runtime.Observe=(stage,id,at)=> {var stages=observations.GetOrAdd(id,_=>new());lock(stages)stages[stage]=at;};
            run=runtime.RunAsync(config,stop.Token);
            int expected=int.Parse(Environment.GetEnvironmentVariable("NIGHTOWL_TEST_JOB_COUNT")!);
            try { await Until(()=>observations.Values.Count(stages=> {lock(stages)return stages.ContainsKey("result_ack");})==expected,75); }
            catch(Exception)
            {
                Console.WriteLine("JOB_PROTOCOL_FAILURE="+JsonSerializer.Serialize(new {runtime.LastFailure,runtime.Failures,
                    states=policy.Store.LoadAll().Select(item=>new {item.JobType,item.Status,item.ErrorCode}),
                    observations=observations.Values.Select(stages=>stages.Keys.ToArray())}));
                throw;
            }
            stop.Cancel();
            try{await run;}catch(OperationCanceledException){}
            using Process harness=Process.GetCurrentProcess();
            Console.WriteLine("JOB_PROTOCOL_TIMINGS="+JsonSerializer.Serialize(new {jobs=observations,signals=runtime.SignalRequests,
                deliveries=runtime.DeliveryCount,working_set_bytes=harness.WorkingSet64,cpu_ms=harness.TotalProcessorTime.TotalMilliseconds}));
        }
        finally{stop.Cancel();try{await run;}catch(OperationCanceledException){} Directory.Delete(root,true);}
    }
    private sealed class RealFactory:IHttpClientFactory
    {
        private static readonly HttpClientHandler Handler=new() {AllowAutoRedirect=false,UseProxy=false};
        public HttpClient CreateClient(string name)=>new(Handler,disposeHandler:false);
    }
    private static HttpResponseMessage Json(object data)=>new(HttpStatusCode.OK){Content=new StringContent(JsonSerializer.Serialize(data),Encoding.UTF8,"application/json")};
    private sealed class Factory(Func<HttpRequestMessage,CancellationToken,Task<HttpResponseMessage>> handler):IHttpClientFactory
    {public HttpClient CreateClient(string name)=>new(new Handler(handler));}
    private sealed class Handler(Func<HttpRequestMessage,CancellationToken,Task<HttpResponseMessage>> handler):HttpMessageHandler
    {protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct)=>handler(request,ct);}
}
