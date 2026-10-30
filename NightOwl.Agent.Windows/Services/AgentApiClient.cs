using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Services;

public sealed class AgentApiClient
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
