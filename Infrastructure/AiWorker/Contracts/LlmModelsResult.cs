using System.Text.Json.Serialization;

namespace chatbot.Infrastructure.AiWorker.Contracts;

/// <summary>Result of worker <c>POST /llm/models</c> — a live, text-only model list.</summary>
public sealed record LlmModelsResult
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("models")]
    public IReadOnlyList<string> Models { get; init; } = Array.Empty<string>();

    [JsonPropertyName("error")]
    public string? Error { get; init; }
}
