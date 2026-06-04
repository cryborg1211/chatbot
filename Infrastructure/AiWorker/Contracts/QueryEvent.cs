namespace chatbot.Infrastructure.AiWorker.Contracts;

/// <summary>
/// One Server-Sent Event from <c>POST /api/query</c>.
/// Discriminated union — caller uses a <c>switch</c> on the runtime type.
///
/// Stream order on the wire (always):
///   1. exactly one <see cref="Sources"/>   — retrieved chunks + scores
///   2. many       <see cref="Token"/>      — assistant content, one chunk at a time
///   3. exactly one <see cref="Done"/>      — terminal marker with totals
///   4. on failure: one <see cref="Error"/> may appear in place of (3)
///
/// Unknown SSE event names are silently dropped by the parser — additive
/// changes on the Python side don't break older .NET clients.
/// </summary>
public abstract record QueryEvent
{
    /// <summary>Retrieved document chunks that grounded the upcoming reply.</summary>
    public sealed record Sources(IReadOnlyList<SourceDocument> Documents) : QueryEvent;

    /// <summary>One streamed chunk of the assistant's reply.</summary>
    public sealed record Token(string Content) : QueryEvent;

    /// <summary>End-of-stream marker. Always present on a clean stream.</summary>
    public sealed record Done(
        string FinishReason,                  // "stop" | "length" | "error"
        long   LatencyMs,
        int?   PromptTokens,
        int?   CompletionTokens) : QueryEvent;

    /// <summary>Out-of-band error from the worker. Stream terminates.</summary>
    public sealed record Error(string Message) : QueryEvent;
}

/// <summary>One retrieved chunk shown in the "sources" event.</summary>
/// <param name="Id">Chunk id (Qdrant point id, UUID5).</param>
/// <param name="DocumentId">Parent document id — what ChatService persists into <c>ChatMessage.SourceDocumentIdsJson</c> for citations.</param>
public sealed record SourceDocument(
    string Id,
    string DocumentId,
    string Title,
    string Snippet,
    double Score);
