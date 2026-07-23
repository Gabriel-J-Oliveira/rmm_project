using System.Text.Json;
using System.Text.RegularExpressions;

namespace NightOwl.Agent.Shared;

public sealed class SanitizationResult
{
    public string Value { get; init; } = "";
    public bool RedactionApplied { get; init; }
    public bool UnsafeContentDetected { get; init; }
    public IReadOnlyList<string> Warnings { get; init; } = Array.Empty<string>();
}

public static partial class NightOwlSanitizer
{
    private static readonly string[] SensitiveFieldNames =
    {
        "authorization", "bearer", "agenttoken", "agent_token", "token", "password", "secret",
        "cookie", "api_key", "apikey", "enrollment", "enrollment_token", "manual_validation_token",
        "private_key", "client_secret"
    };

    public static SanitizationResult SanitizeText(string? value)
    {
        string text = value ?? "";
        bool redacted = false;
        foreach (Regex regex in RedactionRegexes)
        {
            string replaced = regex.Replace(text, match =>
            {
                redacted = true;
                if (match.Groups["prefix"].Success)
                {
                    return match.Groups["prefix"].Value + "[REDACTED]";
                }
                return "[REDACTED]";
            });
            text = replaced;
        }

        text = SanitizeUrlQueries(text, ref redacted);
        bool unsafeContent = ContainsSecretLikeContent(text);
        return new SanitizationResult
        {
            Value = text,
            RedactionApplied = redacted,
            UnsafeContentDetected = unsafeContent,
            Warnings = unsafeContent ? new[] { "DIAG_REDACTION_FAILED" } : Array.Empty<string>()
        };
    }

    public static SanitizationResult SanitizeJson(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return new SanitizationResult { Value = "{}" };
        }

        try
        {
            using JsonDocument document = JsonDocument.Parse(value);
            bool redacted = false;
            object? sanitized = SanitizeJsonElement(document.RootElement, ref redacted);
            string json = JsonSerializer.Serialize(sanitized, JsonOptions);
            SanitizationResult textResult = SanitizeText(json);
            return new SanitizationResult
            {
                Value = textResult.Value,
                RedactionApplied = redacted || textResult.RedactionApplied,
                UnsafeContentDetected = textResult.UnsafeContentDetected,
                Warnings = textResult.Warnings
            };
        }
        catch
        {
            return SanitizeText(value);
        }
    }

    public static string MaskIdentifier(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "";
        }
        string clean = value.Trim();
        string hash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(clean))).ToLowerInvariant();
        return clean.Length <= 8 ? $"sha256:{hash[..12]}" : $"{clean[..4]}...{clean[^4..]} sha256:{hash[..12]}";
    }

    public static string SanitizeUrl(string? url)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            return "";
        }
        if (Uri.TryCreate(url, UriKind.Absolute, out Uri? uri))
        {
            UriBuilder builder = new(uri) { Query = "" };
            return builder.Uri.ToString();
        }
        bool redacted = false;
        return SanitizeUrlQueries(url, ref redacted);
    }

    public static bool ContainsSecretLikeContent(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return false;
        }

        foreach (Regex regex in SecretDetectionRegexes)
        {
            foreach (Match match in regex.Matches(value))
            {
                if (!match.Value.Contains("[REDACTED]", StringComparison.OrdinalIgnoreCase)
                    && !match.Value.Contains("REDACTED", StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
        }
        return false;
    }

    private static object? SanitizeJsonElement(JsonElement element, ref bool redacted, string propertyName = "")
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                Dictionary<string, object?> obj = new(StringComparer.OrdinalIgnoreCase);
                foreach (JsonProperty property in element.EnumerateObject())
                {
                    if (IsSensitiveName(property.Name))
                    {
                        obj[property.Name] = "[REDACTED]";
                        redacted = true;
                    }
                    else
                    {
                        obj[property.Name] = SanitizeJsonElement(property.Value, ref redacted, property.Name);
                    }
                }
                return obj;
            case JsonValueKind.Array:
                List<object?> items = new();
                foreach (JsonElement item in element.EnumerateArray())
                {
                    items.Add(SanitizeJsonElement(item, ref redacted, propertyName));
                }
                return items.ToArray();
            case JsonValueKind.String:
                string raw = element.GetString() ?? "";
                if (IsSensitiveName(propertyName))
                {
                    redacted = true;
                    return "[REDACTED]";
                }
                SanitizationResult result = SanitizeText(raw);
                redacted |= result.RedactionApplied;
                return result.Value;
            case JsonValueKind.Number:
                return element.TryGetInt64(out long l) ? l : element.GetDouble();
            case JsonValueKind.True:
                return true;
            case JsonValueKind.False:
                return false;
            default:
                return null;
        }
    }

    private static bool IsSensitiveName(string name)
    {
        string normalized = name.Replace("-", "_", StringComparison.Ordinal).Replace(".", "_", StringComparison.Ordinal).ToLowerInvariant();
        return SensitiveFieldNames.Any(field => normalized.Contains(field, StringComparison.OrdinalIgnoreCase));
    }

    private static string SanitizeUrlQueries(string value, ref bool redacted)
    {
        bool localRedacted = false;
        string replaced = UrlWithQueryRegex().Replace(value, match =>
        {
            localRedacted = true;
            return match.Groups["url"].Value;
        });
        redacted |= localRedacted;
        return replaced;
    }

    private static readonly Regex[] RedactionRegexes =
    {
        AuthorizationRegex(),
        BearerRegex(),
        JsonLikeSecretRegex(),
        KeyValueSecretRegex(),
        CookieRegex()
    };

    private static readonly Regex[] SecretDetectionRegexes =
    {
        AuthorizationRegex(),
        BearerRegex(),
        JsonLikeSecretRegex(),
        KeyValueSecretRegex(),
        CookieRegex()
    };

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };

    [GeneratedRegex(@"(?i)(?<prefix>Authorization\s*[:=]\s*)(Bearer\s+)?[A-Za-z0-9._~+/=-]{12,}")]
    private static partial Regex AuthorizationRegex();

    [GeneratedRegex(@"(?i)(?<prefix>\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}")]
    private static partial Regex BearerRegex();

    [GeneratedRegex(@"(?i)(?<prefix>[""']?(agentToken|agent_token|token|password|secret|api_key|apikey|cookie|enrollment_token|manual_validation_token)[""']?\s*[:=]\s*[""']?)[^""'\s,;}\]]{4,}")]
    private static partial Regex JsonLikeSecretRegex();

    [GeneratedRegex(@"(?i)(?<prefix>\b(agentToken|agent_token|token|password|secret|api_key|apikey|cookie|enrollment_token|manual_validation_token)=)[^&\s]+")]
    private static partial Regex KeyValueSecretRegex();

    [GeneratedRegex(@"(?i)(?<prefix>\bCookie\s*[:=]\s*)[^\r\n;]+")]
    private static partial Regex CookieRegex();

    [GeneratedRegex(@"(?<url>https?://[^\s?""']+)\?[^""'\s]+")]
    private static partial Regex UrlWithQueryRegex();
}
