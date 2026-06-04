using System.Text.Json.Serialization;

namespace chatbot.Infrastructure.AiWorker.Contracts;

/// <summary>
/// Body of <c>POST /api/query</c>. Serialised to JSON (snake_case) by
/// <see cref="AiWorkerClient"/> using a shared <see cref="System.Text.Json.JsonSerializerOptions"/>
/// with <see cref="System.Text.Json.JsonNamingPolicy.SnakeCaseLower"/>.
///
/// Wire shape:
/// <code>
/// {
///   "query":         "...",
///   "department_id": "IT",
///   "history":       [{ "role": "user", "content": "..." }, ...],
///   "user_id":       "..."
/// }
/// </code>
/// </summary>
public sealed record QueryRequest(
    string Query,
    string DepartmentId,
    IReadOnlyList<ChatHistoryItem> History,
    string UserId);

/// <summary>One prior turn of the conversation, as Python expects it.</summary>
public sealed record ChatHistoryItem(
    string Role,        // "user" | "assistant" | "system"
    string Content);
