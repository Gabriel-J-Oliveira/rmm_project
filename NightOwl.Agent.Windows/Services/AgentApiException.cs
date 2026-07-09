namespace NightOwl.Agent.Windows.Services;

public sealed class AgentApiException : HttpRequestException
{
    public AgentApiException(
        string message,
        int statusCode,
        string? reasonPhrase,
        string responseBody,
        string url,
        string method,
        string operation)
        : base(message)
    {
        StatusCodeValue = statusCode;
        ReasonPhrase = reasonPhrase ?? "";
        ResponseBody = responseBody;
        Url = url;
        Method = method;
        Operation = operation;
    }

    public int StatusCodeValue { get; }
    public string ReasonPhrase { get; }
    public string ResponseBody { get; }
    public string Url { get; }
    public string Method { get; }
    public string Operation { get; }

    public override string ToString()
    {
        return $"{base.ToString()}{Environment.NewLine}HTTP {StatusCodeValue} {ReasonPhrase} {Method} {Url}{Environment.NewLine}{ResponseBody}";
    }
}
