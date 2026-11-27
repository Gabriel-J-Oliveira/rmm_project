using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Security.Cryptography;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Jobs;

namespace NightOwl.Agent.Windows.Services;

public sealed class AgentApiClient : ISoftwareOperationTransport
{
    private readonly IHttpClientFactory _httpClientFactory;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public AgentApiClient(IHttpClientFactory httpClientFactory)
    {
        _httpClientFactory = httpClientFactory;
    }

    public Task PostHeartbeatAsync(AgentConfig config, AgentHeartbeatPayload payload, CancellationToken ct)
    {
        return SendJsonAsync(config, HttpMethod.Post, config.HeartbeatUrl, payload, ct, "heartbeat");
    }

    public Task PostCollectionAsync(AgentConfig config, AgentCollectPayload payload, CancellationToken ct)
    {
        return SendJsonAsync(config, HttpMethod.Post, config.CollectUrl, payload, ct, "collection");
    }

    public Task PostTelemetryAsync(AgentConfig config, TelemetryBatch payload, CancellationToken ct)
    {
        return SendJsonAsync(config, HttpMethod.Post, config.TelemetryUrl, payload, ct, "telemetry");
    }

    internal async Task<JsonElement> TelemetryControlAsync(AgentConfig config, CancellationToken ct)
        => await TelemetryExchangeAsync(config, "control/", null, ct);

    internal async Task TelemetryLiveAsync(AgentConfig config, object payload, CancellationToken ct)
        => _ = await TelemetryExchangeAsync(config, "live/", payload, ct);

    internal async Task<Guid[]> TelemetryWindowsAsync(AgentConfig config, object payload, CancellationToken ct)
    {
        JsonElement result = await TelemetryExchangeAsync(config, "", payload, ct);
        return result.GetProperty("accepted_window_ids").EnumerateArray().Select(item => item.GetGuid()).ToArray();
    }

    private async Task<JsonElement> TelemetryExchangeAsync(AgentConfig config, string suffix, object? payload, CancellationToken ct)
    {
        string url = config.ServerBaseUrl.TrimEnd('/') + "/api/agent/telemetry/" + suffix;
        using HttpRequestMessage request = new(payload is null ? HttpMethod.Get : HttpMethod.Post, url);
        AddAuth(config, request);
        if (payload is not null) request.Content = new StringContent(JsonSerializer.Serialize(payload, JsonOptions), Encoding.UTF8, "application/json");
        using HttpClient client = _httpClientFactory.CreateClient();
        using HttpResponseMessage response = await client.SendAsync(request, ct);
        string body = await response.Content.ReadAsStringAsync(ct);
        EnsureSuccess(response, body, url, request.Method, "telemetry");
        using JsonDocument document = JsonDocument.Parse(body);
        return document.RootElement.Clone();
    }

    public async Task<AgentJobsPullResponse> PullJobsAsync(AgentConfig config, CancellationToken ct, bool protocolV2 = false)
    {
        if (protocolV2) ValidateJobUrl(config, config.JobsPullUrl);
        using HttpRequestMessage request = new(HttpMethod.Get, config.JobsPullUrl);
        if (protocolV2) request.Headers.Add("X-NightOwl-Job-Protocol", "2");
        AddAuth(config, request);
        using HttpClient client = _httpClientFactory.CreateClient(protocolV2 ? "jobs-v2" : "");
        using HttpResponseMessage response = await client.SendAsync(request, ct);
        string body = await response.Content.ReadAsStringAsync(ct);
        EnsureSuccess(response, body, config.JobsPullUrl, HttpMethod.Get, "jobs.pull");
        return JsonSerializer.Deserialize<AgentJobsPullResponse>(body, JsonOptions) ?? new AgentJobsPullResponse();
    }

    public Task SendJobResultAsync(AgentConfig config, JobExecutionResult result, CancellationToken ct, string? idempotencyKey = null)
    {
        return SendJsonAsync(config, HttpMethod.Post, config.JobsResultUrl, result, ct, "jobs.result", idempotencyKey);
    }

    private async Task<JsonElement> JobExchange(AgentConfig config, string url, object? payload, string? key, CancellationToken ct)
    {
        ValidateJobUrl(config, url);
        using HttpRequestMessage request=new(payload is null ? HttpMethod.Get : HttpMethod.Post,url);
        AddAuth(config,request);
        if(key is not null) request.Headers.TryAddWithoutValidation("Idempotency-Key",key);
        if(payload is not null) request.Content=new StringContent(JsonSerializer.Serialize(payload,JsonOptions),Encoding.UTF8,"application/json");
        using HttpClient client=_httpClientFactory.CreateClient("jobs-v2");
        using HttpResponseMessage response=await client.SendAsync(request,ct);
        string body=await response.Content.ReadAsStringAsync(ct);
        EnsureSuccess(response,body,url,request.Method,"jobs.control");
        using JsonDocument doc=JsonDocument.Parse(body);
        return doc.RootElement.Clone();
    }
    internal async Task<bool> JobSignalAsync(AgentConfig config,CancellationToken ct)
    {
        JsonElement signal=await JobExchange(config,config.ServerBaseUrl.TrimEnd('/')+"/api/agent/jobs/signal/",null,null,ct);
        return signal.GetProperty("protocol").GetInt32()==2 && signal.GetProperty("available").GetBoolean();
    }
    internal Task<JsonElement> TerminalPollAsync(AgentConfig config, CancellationToken ct) =>
        JobExchange(config, config.ServerBaseUrl.TrimEnd('/') + "/api/agent/terminal/poll/",
            new { machine_id = config.MachineId }, null, ct);
    internal Task<JsonElement> TerminalConsentAsync(AgentConfig config, Guid id, string decision, TerminalTokenFacts? facts, CancellationToken ct) =>
        JobExchange(config, config.ServerBaseUrl.TrimEnd('/') + "/api/agent/terminal/consent/" + id.ToString("D") + "/",
            new { machine_id = config.MachineId, decision, identity = facts is null ? null : new { sid = facts.Sid,
                windows_session_id = facts.SessionId, integrity = facts.Integrity, elevated = facts.Elevated } }, null, ct);
    internal async Task JobStartedAsync(AgentConfig config,AgentJobRequest job,CancellationToken ct)
    {
        JsonElement ack=await JobExchange(config,config.ServerBaseUrl.TrimEnd('/')+"/api/agent/jobs/"+Guid.Parse(job.Id)+"/started/",
            new {machine_id=config.MachineId},null,ct);
        if(ack.GetProperty("job_id").GetString()!=job.Id || ack.GetProperty("state").GetString()!="running")
            throw new InvalidDataException("JOB_START_ACK_INVALID");
    }
    internal async Task SendJobResultWithAckAsync(AgentConfig config,JobExecutionResult result,string id,CancellationToken ct)
    {
        JsonElement ack=await JobExchange(config,config.JobsResultUrl,result,id,ct);
        if(ack.GetProperty("job_id").GetString()!=result.JobId || ack.GetProperty("result_id").GetString()!=id)
            throw new InvalidDataException("JOB_RESULT_ACK_INVALID");
    }

    internal async Task<JsonElement> GetSoftwareOperationProfileAsync(AgentConfig config, string jobId, CancellationToken ct)
    {
        if (!Guid.TryParse(jobId, out Guid id)) throw new InvalidDataException("SOFTWARE_OPERATION_JOB_ID_INVALID");
        string url = config.ServerBaseUrl.TrimEnd('/') + "/api/agent/software-operations/jobs/" + id.ToString("D") + "/profile/";
        ValidateJobUrl(config, url);
        using HttpRequestMessage request = new(HttpMethod.Get, url);
        AddAuth(config, request);
        using HttpClient client = _httpClientFactory.CreateClient("jobs-v2");
        // Bound this request by the caller's job cancellation token.
        client.Timeout = Timeout.InfiniteTimeSpan;
        using HttpResponseMessage response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
        if (!response.IsSuccessStatusCode)
            EnsureSuccess(response, "", url, HttpMethod.Get, "software.profile");
        if (response.Content.Headers.ContentLength is long length && (length < 1 || length > 64 * 1024))
            throw new InvalidDataException("SOFTWARE_PROFILE_SIZE_INVALID");
        await using Stream source = await response.Content.ReadAsStreamAsync(ct);
        using MemoryStream buffer = new();
        byte[] chunk = new byte[8192];
        int count;
        while ((count = await source.ReadAsync(chunk, ct)) > 0)
        {
            if (buffer.Length + count > 64 * 1024) throw new InvalidDataException("SOFTWARE_PROFILE_SIZE_INVALID");
            await buffer.WriteAsync(chunk.AsMemory(0, count), ct);
        }
        using JsonDocument document = JsonDocument.Parse(buffer.ToArray());
        return document.RootElement.Clone();
    }

    Task<JsonElement> ISoftwareOperationTransport.GetProfileAsync(AgentConfig config, string jobId, CancellationToken ct) =>
        GetSoftwareOperationProfileAsync(config, jobId, ct);

    internal async Task<(long Size, string Sha256)> DownloadSoftwareArtifactAsync(
        AgentConfig config, string jobId, string expectedSha256, int expectedRevision, long expectedSize, long maximumBytes,
        string destination, CancellationToken ct)
    {
        if (!Guid.TryParse(jobId, out Guid id) || expectedSize is < 1 || expectedSize > maximumBytes || maximumBytes > 1024L * 1024 * 1024)
            throw new InvalidDataException("SOFTWARE_ARTIFACT_BOUNDS_INVALID");
        string url = config.ServerBaseUrl.TrimEnd('/') + "/api/agent/software-operations/jobs/" + id.ToString("D") + "/artifact/";
        ValidateJobUrl(config, url);
        using HttpRequestMessage request = new(HttpMethod.Get, url);
        AddAuth(config, request);
        using HttpClient client = _httpClientFactory.CreateClient("jobs-v2");
        // The caller's job cancellation token is the authoritative 30-minute
        // budget. HttpClient's default 100-second timeout would truncate large
        // private artifacts independently of the bounded job timeout.
        client.Timeout = Timeout.InfiniteTimeSpan;
        using HttpResponseMessage response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
        if (!response.IsSuccessStatusCode) EnsureSuccess(response, "", url, HttpMethod.Get, "software.artifact");
        string headerHash = response.Headers.TryGetValues("X-Software-Artifact-SHA256", out IEnumerable<string>? hashes) ? hashes.SingleOrDefault() ?? "" : "";
        if (!headerHash.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase)
            || !response.Headers.TryGetValues("X-Software-Artifact-Revision", out IEnumerable<string>? revisions)
            || !int.TryParse(revisions.SingleOrDefault(), out int actualRevision) || actualRevision != expectedRevision
            || response.Content.Headers.ContentLength != expectedSize)
            throw new InvalidDataException("SOFTWARE_ARTIFACT_HEADERS_INVALID");
        await using Stream source = await response.Content.ReadAsStreamAsync(ct);
        await using FileStream target = new(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None,
            64 * 1024, FileOptions.Asynchronous | FileOptions.WriteThrough);
        using IncrementalHash digest = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        byte[] buffer = new byte[64 * 1024];
        long total = 0;
        int read;
        while ((read = await source.ReadAsync(buffer, ct)) > 0)
        {
            total += read;
            if (total > expectedSize || total > maximumBytes) throw new InvalidDataException("SOFTWARE_ARTIFACT_SIZE_INVALID");
            digest.AppendData(buffer, 0, read);
            await target.WriteAsync(buffer.AsMemory(0, read), ct);
        }
        await target.FlushAsync(ct);
        target.Flush(flushToDisk: true);
        string actual = Convert.ToHexString(digest.GetHashAndReset()).ToLowerInvariant();
        if (total != expectedSize || !actual.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("SOFTWARE_ARTIFACT_HASH_INVALID");
        return (total, actual);
    }

    Task<(long Size, string Sha256)> ISoftwareOperationTransport.DownloadArtifactAsync(AgentConfig config, string jobId,
        string sha256, int revision, long expectedSize, long maximumBytes, string destination, CancellationToken ct) =>
        DownloadSoftwareArtifactAsync(config, jobId, sha256, revision, expectedSize, maximumBytes, destination, ct);

    private async Task SendJsonAsync(AgentConfig config, HttpMethod method, string url, object payload, CancellationToken ct, string operation, string? idempotencyKey = null)
    {
        using HttpRequestMessage request = new(method, url);
        AddAuth(config, request);
        if (!string.IsNullOrWhiteSpace(idempotencyKey))
        {
            request.Headers.TryAddWithoutValidation("Idempotency-Key", idempotencyKey);
        }
        string json = JsonSerializer.Serialize(payload, JsonOptions);
        request.Content = new StringContent(json, Encoding.UTF8, "application/json");
        using HttpClient client = _httpClientFactory.CreateClient();
        using HttpResponseMessage response = await client.SendAsync(request, ct);
        string body = await response.Content.ReadAsStringAsync(ct);
        EnsureSuccess(response, body, url, method, operation);
    }

    private static void ValidateJobUrl(AgentConfig config, string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out Uri? uri) || uri.UserInfo != "" ||
            !Uri.TryCreate(config.ServerBaseUrl, UriKind.Absolute, out Uri? server) || uri.GetLeftPart(UriPartial.Authority) != server.GetLeftPart(UriPartial.Authority) ||
            (uri.Scheme != "https" && !(uri.IsLoopback && Environment.GetEnvironmentVariable("NIGHTOWL_TEST_SERVER") == uri.GetLeftPart(UriPartial.Authority))))
            throw new InvalidOperationException("JOB_HTTPS_ORIGIN_REQUIRED");
    }

    private static void AddAuth(AgentConfig config, HttpRequestMessage request)
    {
        if (!string.IsNullOrWhiteSpace(config.AgentToken))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", config.AgentToken);
        }
    }

    private static void EnsureSuccess(HttpResponseMessage response, string body, string url, HttpMethod method, string operation)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        int statusCode = (int)response.StatusCode;
        string reason = response.ReasonPhrase ?? "";
        string message = $"{operation} request failed with HTTP {statusCode} {reason}.";
        throw new AgentApiException(
            message,
            statusCode,
            reason,
            body,
            url,
            method.Method,
            operation);
    }
}
