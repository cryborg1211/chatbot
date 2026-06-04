using System.Text.Json.Serialization;

namespace chatbot.Infrastructure.AiWorker.Contracts;

/// <summary>
/// Response body from <c>POST /ingest</c>.
/// Same shape on success (HTTP 200) and failure (HTTP 422) — discriminated
/// by <see cref="Status"/>.
/// </summary>
public sealed record IngestResult
{
    [JsonPropertyName("document_id")]
    public Guid DocumentId { get; init; }

    /// <summary><c>"success"</c> or <c>"failed"</c>.</summary>
    [JsonPropertyName("status")]
    public string Status { get; init; } = default!;

    [JsonPropertyName("chunk_count")]
    public int ChunkCount { get; init; }

    [JsonPropertyName("elapsed_ms")]
    public long ElapsedMs { get; init; }

    // ---- Populated only when Status == "failed" ----

    [JsonPropertyName("error_code")]
    public string? ErrorCode { get; init; }

    [JsonPropertyName("message")]
    public string? Message { get; init; }

    public bool IsSuccess => string.Equals(Status, "success", StringComparison.OrdinalIgnoreCase);
}
