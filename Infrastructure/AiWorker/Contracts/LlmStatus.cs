using System.Text.Json.Serialization;

namespace chatbot.Infrastructure.AiWorker.Contracts;

/// <summary>
/// Response body from worker <c>GET /llm/status</c> — LLM backend health for the
/// admin AI-settings dashboard. Read-only.
/// </summary>
public sealed record LlmStatus
{
    [JsonPropertyName("provider")]
    public string Provider { get; init; } = "ollama";

    [JsonPropertyName("active_model")]
    public string ActiveModel { get; init; } = default!;

    [JsonPropertyName("base_url")]
    public string BaseUrl { get; init; } = default!;

    [JsonPropertyName("ollama_reachable")]
    public bool OllamaReachable { get; init; }

    [JsonPropertyName("installed_models")]
    public IReadOnlyList<string> InstalledModels { get; init; } = Array.Empty<string>();
}
