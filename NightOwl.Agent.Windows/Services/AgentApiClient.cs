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

    public async Task<AgentJobsPullResponse> PullJobsAsync(AgentConfig config, CancellationToken ct)
    {
        using HttpRequestMessage request = new(HttpMethod.Get, config.JobsPullUrl);
        AddAuth(config, request);
        using HttpClient client = _httpClientFactory.CreateClient();
        using HttpResponseMessage response = await client.SendAsync(request, ct);
        string body = await response.Content.ReadAsStringAsync(ct);
        EnsureSuccess(response, body, config.JobsPullUrl, HttpMethod.Get, "jobs.pull");
        return JsonSerializer.Deserialize<AgentJobsPullResponse>(body, JsonOptions) ?? new AgentJobsPullResponse();
    }

    public Task SendJobResultAsync(AgentConfig config, JobExecutionResult result, CancellationToken ct, string? idempotencyKey = null)
    {
        return SendJsonAsync(config, HttpMethod.Post, config.JobsResultUrl, result, ct, "jobs.result", idempotencyKey);
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
