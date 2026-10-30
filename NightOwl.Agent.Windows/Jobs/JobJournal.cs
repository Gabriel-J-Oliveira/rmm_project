using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Collections.Concurrent;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Jobs;

// Local, identity-scoped inbox and exact outcomes. PostgreSQL remains authoritative.
internal sealed class JobJournal
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private readonly string _root;
    private static readonly ConcurrentDictionary<string,object> Gates = new(StringComparer.OrdinalIgnoreCase);
    private readonly object _gate;
    internal int Quarantined { get; private set; }
    internal JobJournal(AgentConfig config, JobStore store)
    {
        string machine = Guid.TryParse(config.MachineId,out Guid id) ? id.ToString("D") : config.MachineId.Trim().ToLowerInvariant();
        string identity = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(machine)))[..24];
        _root = Path.Combine(store.DirectoryPath, "dispatch-v2", identity);
        _gate = Gates.GetOrAdd(_root,_=>new object());
        Directory.CreateDirectory(Path.Combine(_root,"inbox"));
        Directory.CreateDirectory(Path.Combine(_root,"outcomes"));
    }
    private string PathFor(string folder, string id)
        => Path.Combine(_root, folder, Guid.Parse(id).ToString("D")+".json");
    internal void Accept(AgentJobRequest job)
    {
        lock (_gate)
        {
            string path = PathFor("inbox",job.Id);
            string json = JsonSerializer.Serialize(job, Json);
            if (Encoding.UTF8.GetByteCount(json) > 128*1024) throw new InvalidDataException("JOB_ENVELOPE_TOO_LARGE");
            if (File.Exists(path))
            {
                if (File.ReadAllText(path) != json) throw new InvalidDataException("JOB_ENVELOPE_CONFLICT");
                return;
            }
            if (File.Exists(PathFor("outcomes",job.Id))) return;
            FileInfo[] files = new DirectoryInfo(Path.Combine(_root,"inbox")).GetFiles("*.json");
            if (files.Length >= 32 || files.Sum(file=>file.Length)+json.Length > 2*1024*1024)
                throw new InvalidOperationException("JOB_INBOX_FULL");
            NightOwlFileStore.WriteAllTextDurable(path,json);
        }
    }
    internal IReadOnlyList<AgentJobRequest> Pending()
    {
        lock (_gate)
        {
            List<AgentJobRequest> jobs=new();
            foreach(string path in Directory.GetFiles(Path.Combine(_root,"inbox"),"*.json"))
                try
                {
                    if(new FileInfo(path).Length>128*1024) throw new InvalidDataException();
                    AgentJobRequest job=JsonSerializer.Deserialize<AgentJobRequest>(File.ReadAllText(path),Json) ?? throw new InvalidDataException();
                    if(!Guid.TryParse(job.Id,out Guid id) || Path.GetFileNameWithoutExtension(path)!=id.ToString("D")) throw new InvalidDataException();
                    jobs.Add(job);
                }
                catch(Exception error) when(error is JsonException or InvalidDataException) { Quarantine(path); }
            return jobs.OrderByDescending(job=>JobExecutionCoordinator.GetCategory(job.Type)==JobCategories.Exclusive)
                .ThenByDescending(job=>job.Priority).ThenBy(job=>job.CreatedAt).ToArray();
        }
    }
    internal void Result(JobExecutionResult result)
    {
        lock (_gate)
        {
            string path = PathFor("outcomes",result.JobId);
            if (File.Exists(path))
            {
                JobExecutionResult old = JsonSerializer.Deserialize<JobExecutionResult>(File.ReadAllText(path),Json)!;
                if (JobFinalStatuses.All.Contains(old.Status)) return;
            }
            string json=JsonSerializer.Serialize(result,Json);
            if(Encoding.UTF8.GetByteCount(json)>128*1024) throw new InvalidDataException("JOB_OUTCOME_TOO_LARGE");
            NightOwlFileStore.WriteAllTextDurable(path,json);
        }
    }
    internal IEnumerable<JobExecutionResult> Results()
    {
        lock (_gate)
        {
            List<JobExecutionResult> results=new();
            foreach(string path in Directory.GetFiles(Path.Combine(_root,"outcomes"),"*.json"))
                try
                {
                    if(new FileInfo(path).Length>128*1024) throw new InvalidDataException();
                    JobExecutionResult result=JsonSerializer.Deserialize<JobExecutionResult>(File.ReadAllText(path),Json) ?? throw new InvalidDataException();
                    if(!Guid.TryParse(result.JobId,out Guid id) || Path.GetFileNameWithoutExtension(path)!=id.ToString("D") ||
                        !(JobFinalStatuses.All.Contains(result.Status) || result.Status=="running")) throw new InvalidDataException();
                    results.Add(result);
                }
                catch(Exception error) when(error is JsonException or InvalidDataException) { Quarantine(path); }
            return results;
        }
    }
    private void Quarantine(string path)
    {
        string directory=Path.Combine(_root,"quarantine");
        Directory.CreateDirectory(directory);
        File.Move(path,Path.Combine(directory,Guid.NewGuid().ToString("N")+".json"));
        Quarantined++;
        FileInfo[] files=new DirectoryInfo(directory).GetFiles().OrderByDescending(file=>file.LastWriteTimeUtc).ToArray();
        long bytes=0;
        for(int index=0;index<files.Length;index++) {bytes+=files[index].Length;if(index>=128 || bytes>16*1024*1024)files[index].Delete();}
    }
    internal void Remove(AgentJobRequest job) { lock (_gate) File.Delete(PathFor("inbox",job.Id)); }
    internal void Acknowledged(string id) { lock (_gate) File.Delete(PathFor("outcomes",id)); }
}
