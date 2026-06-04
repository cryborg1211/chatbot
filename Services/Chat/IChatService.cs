using chatbot.Infrastructure.AiWorker.Contracts;
using chatbot.Models;

namespace chatbot.Services.Chat;

/// <summary>
/// Application service for the chat lifecycle. Split in two so the
/// caller (controller) can flush conversation metadata to the browser
/// (e.g. via a response header) BEFORE the SSE body starts streaming.
/// </summary>
public interface IChatService
{
    /// <summary>
    /// Resolve or create the target conversation, persist the user's
    /// message into it, and return the live entity.  Auto-titles a new
    /// conversation from the first ~40 chars of the user message.
    /// </summary>
    Task<Conversation> PrepareConversationAsync(
        Guid?  conversationId,
        string userMessage,
        string userId,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// Stream the assistant's reply for <paramref name="conversation"/>.
    /// Yields every <see cref="QueryEvent"/> verbatim so the controller
    /// can pipe them straight to the browser.  When the stream ends
    /// (cleanly or via cancellation) the assistant's full reply +
    /// source-document ids + latency are persisted as one
    /// <see cref="ChatMessage"/>.
    /// </summary>
    IAsyncEnumerable<QueryEvent> StreamReplyAsync(
        Conversation conversation,
        string       userMessage,
        string       departmentId,
        string       userId,
        CancellationToken cancellationToken = default);
}
