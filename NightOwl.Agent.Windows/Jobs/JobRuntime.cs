using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

namespace NightOwl.Agent.Windows.Jobs;

internal sealed class JobRuntime
{
    private readonly AgentApiClient _api;
    private readonly JobExecutionCoordinator _coordinator;
    private readonly Func<AgentConfig,AgentJobRequest,CancellationToken,Task<JobExecutionResult>> _execute;
    private readonly PendingResultQueue _results;
    private readonly JsonlLogger _log;
    private readonly SemaphoreSlim _heavy;
    private readonly Func<CancellationToken,Task>? _migrate;
    private int _journalQuarantined;
    internal long SignalRequests { get; private set; }
    internal long DeliveryCount { get; private set; }
    internal long Failures { get; private set; }
    internal string LastFailure { get; private set; } = "";
    internal Action<string,string,DateTimeOffset>? Observe { get; set; }
    internal Action<DateTimeOffset>? PollConfirmed { get; set; }
    internal JobRuntime(AgentApiClient api, JobExecutionCoordinator coordinator,
        Func<AgentConfig,AgentJobRequest,CancellationToken,Task<JobExecutionResult>> execute,
        PendingResultQueue results, JsonlLogger log, SemaphoreSlim heavy, Func<CancellationToken,Task>? migrate=null)
        { _api=api; _coordinator=coordinator; _execute=execute; _results=results; _log=log; _heavy=heavy; _migrate=migrate; }

    internal async Task RunAsync(AgentConfig config, CancellationToken ct)
    {
        JobJournal journal = new(config,_coordinator.Store);
        await Task.WhenAll(Dispatch(config,journal,ct), Execute(config,journal,ct), Upload(config,journal,ct));
    }
    private async Task SafeLog(string code, Exception error)
    {
        Failures++;
        LastFailure=code+":"+(error is AgentApiException api ? "HTTP_"+api.StatusCodeValue : error.GetType().Name);
        using CancellationTokenSource budget=new(TimeSpan.FromSeconds(1));
        try { await _log.LogAsync(code,"Jobs operation incomplete.",new {error_type=error.GetType().Name},budget.Token,"warning"); }
        catch (Exception) { }
    }
    private static async Task Delay(double seconds,CancellationToken ct)
        { try { await Task.Delay(TimeSpan.FromSeconds(seconds),ct); } catch(OperationCanceledException) { } }
    private async Task Dispatch(AgentConfig config,JobJournal journal,CancellationToken ct)
    {
        int failures=0;
        while(!ct.IsCancellationRequested)
        {
            double delay=Math.Clamp(config.JobSignalSeconds,2,30);
            try
            {
                if(config.HasValidToken && journal.Pending().Count<24 && _results.LoadAll().Count<100)
                {
                    using CancellationTokenSource budget=CancellationTokenSource.CreateLinkedTokenSource(ct);
                    budget.CancelAfter(TimeSpan.FromSeconds(5));
                    SignalRequests++;
                    bool available=await _api.JobSignalAsync(config,budget.Token);
                    PollConfirmed?.Invoke(DateTimeOffset.UtcNow);
                    if(available)
                    {
                        DateTimeOffset signaled=DateTimeOffset.UtcNow;
                        AgentJobsPullResponse batch=await _api.PullJobsAsync(config,budget.Token,protocolV2:true);
                        foreach(AgentJobRequest job in batch.Jobs.Take(5))
                        { journal.Accept(job); DeliveryCount++; Observe?.Invoke("signal",job.Id,signaled); Observe?.Invoke("received",job.Id,DateTimeOffset.UtcNow); }
                    }
                }
                failures=0;
            }
            catch(Exception error) when(!ct.IsCancellationRequested)
            {
                failures=Math.Min(6,failures+1);
                delay=Math.Min(60,Math.Pow(2,failures))+Random.Shared.NextDouble();
                await SafeLog("job.dispatch.failed",error);
            }
            await Delay(delay,ct);
        }
    }
    private async Task Execute(AgentConfig config,JobJournal journal,CancellationToken ct)
    {
        while(!ct.IsCancellationRequested)
        {
            try
            {
                AgentJobRequest? job=journal.Pending().FirstOrDefault();
                if(job is null || _results.LoadAll().Count>=100 || _coordinator.HasExternalOwner)
                    { await Delay(.25,ct); continue; }
                await _heavy.WaitAsync(ct);
                try
                {
                    JobStateRecord? state=_coordinator.Store.Load(job.Id);
                    if(state?.IsFinal==true)
                    {
                        // Recovery never re-executes a locally final operation.
                        journal.Remove(job);
                        continue;
                    }
                    if(state?.Status=="running")
                    {
                        JobExecutionResult uncertain=new() {JobId=job.Id,Status="interrupted",StartedAt=state.UpdatedAt,
                            FinishedAt=DateTimeOffset.UtcNow,ExitCode=1,Result=new {type=job.Type,error_code="JOB_OUTCOME_UNCONFIRMED"}};
                        _coordinator.PersistOutcome(config,job,uncertain);
                        Queue(job.Type,uncertain);
                        journal.Remove(job);
                        continue;
                    }
                    JobDecision validation = _coordinator.Validate(config,job);
                    if(!validation.ShouldExecute)
                    {
                        journal.Result(validation.FinalResult!);
                        Queue(job.Type,validation.FinalResult!);
                        journal.Remove(job);
                        continue;
                    }
                    using CancellationTokenSource budget=CancellationTokenSource.CreateLinkedTokenSource(ct);
                    budget.CancelAfter(TimeSpan.FromSeconds(5));
                    try { await _api.JobStartedAsync(config,job,budget.Token); }
                    catch(AgentApiException error) when(error.StatusCodeValue==409)
                    {
                        JobExecutionResult revoked=new(){JobId=job.Id,Status="cancelled",FinishedAt=DateTimeOffset.UtcNow,
                            Result=new {type=job.Type,error_code="JOB_START_REVOKED"}};
                        _coordinator.PersistOutcome(config,job,revoked);Queue(job.Type,revoked);journal.Remove(job);continue;
                    }
                    JobStartDecision start=await _coordinator.TryStartAsync(config,job,ct);
                    if(!start.CanStart) { await Delay(.5,ct); continue; }
                    try
                    {
                        Observe?.Invoke("execution_started",job.Id,DateTimeOffset.UtcNow);
                        JobExecutionResult result=await _execute(config,job,ct);
                        journal.Result(result);
                        Queue(job.Type,result);
                        journal.Remove(job);
                    }
                    finally { _coordinator.Release(job); }
                }
                finally { _heavy.Release(); }
            }
            catch(Exception error) when(!ct.IsCancellationRequested) { await SafeLog("job.execution.pending",error); await Delay(2,ct); }
            catch(OperationCanceledException) when(ct.IsCancellationRequested) { break; }
            await Delay(.1,ct);
        }
    }
    private void Queue(string type, JobExecutionResult result)
    {
        string id=ResultIdentity(result);
        if(!_results.HasResult(id)) _results.Enqueue(type,result,JobExecutionCoordinator.IsCritical(type),id);
    }
    private static string ResultIdentity(JobExecutionResult result) => "job-"+Guid.Parse(result.JobId).ToString("D")+"-"+result.Status;
    private async Task Upload(AgentConfig config,JobJournal journal,CancellationToken ct)
    {
        while(!ct.IsCancellationRequested)
        {
            try
            {
                if(_migrate is not null && !_coordinator.HasActiveUpdate) await _migrate(ct);
                foreach(JobExecutionResult result in journal.Results())
                {
                    if(_results.IsAcknowledged(ResultIdentity(result))) { journal.Acknowledged(result.JobId); continue; }
                    JobStateRecord? state=_coordinator.Store.Load(result.JobId);
                    Queue(state?.JobType ?? "",result);
                }
                foreach(PendingResultRecord record in _results.ListDue(DateTimeOffset.UtcNow).Take(10))
                {
                    try
                    {
                        JobExecutionResult result=record.Payload.Deserialize<JobExecutionResult>(new JsonSerializerOptions(JsonSerializerDefaults.Web))!;
                        _coordinator.RecoverFinalResult(config,record);
                        using CancellationTokenSource budget=CancellationTokenSource.CreateLinkedTokenSource(ct);
                        budget.CancelAfter(TimeSpan.FromSeconds(5));
                        await _api.SendJobResultWithAckAsync(config,result,record.ResultId,budget.Token);
                        Observe?.Invoke("result_ack",record.JobId,DateTimeOffset.UtcNow);
                        _results.MarkSent(record);
                        journal.Acknowledged(record.JobId);
                    }
                    catch(Exception error) when(!ct.IsCancellationRequested)
                    { _results.MarkAttemptFailed(record,JobErrorCodes.ResultSendFailed,"Result transmission incomplete."); await SafeLog("job.result.retry",error); }
                }
                int corrupt=_results.DrainQuarantineEvents().Count;
                if(journal.Quarantined>_journalQuarantined)
                {
                    corrupt+=journal.Quarantined-_journalQuarantined;
                    _journalQuarantined=journal.Quarantined;
                }
                if(corrupt>0)
                {
                    using CancellationTokenSource logBudget=new(TimeSpan.FromSeconds(1));
                    await _log.LogAsync("job.result.quarantined","Invalid persisted results isolated.",
                        new {count=corrupt,error_code=JobErrorCodes.ResultQueueCorrupted},logBudget.Token,"warning");
                }
            }
            catch(Exception error) when(!ct.IsCancellationRequested) { await SafeLog("job.result.pending",error); }
            await Delay(1,ct);
        }
    }
}
