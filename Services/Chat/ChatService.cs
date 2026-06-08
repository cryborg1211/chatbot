using System.Text;
using System.Text.Json;
using chatbot.Data;
using chatbot.Infrastructure.AiWorker;
using chatbot.Infrastructure.AiWorker.Contracts;
using chatbot.Infrastructure.Audit;
using chatbot.Models;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace chatbot.Services.Chat;

/// <inheritdoc/>
public sealed class ChatService : IChatService
{
    /// <summary>Last N messages we feed back to the worker as history.</summary>
    private const int HistoryWindow = 6;

    /// <summary>Auto-title length cap for brand-new conversations.</summary>
    private const int AutoTitleMaxLen = 40;

    private readonly ApplicationDbContext _db;
    private readonly IAiWorkerClient _worker;
    private readonly IAuditLogger _audit;
    private readonly ILogger<ChatService> _logger;

    public ChatService(
        ApplicationDbContext db,
        IAiWorkerClient worker,
        IAuditLogger audit,
        ILogger<ChatService> logger)
    {
        _db     = db;
        _worker = worker;
        _audit  = audit;
        _logger = logger;
    }

    // ==================================================================
    //  1. Prepare conversation + persist user message
    // ==================================================================

    public async Task<Conversation> PrepareConversationAsync(
        Guid?  conversationId,
        string userMessage,
        string userId,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(userMessage);
        ArgumentException.ThrowIfNullOrWhiteSpace(userId);

        Conversation conversation;
        bool isNew = false;
        if (conversationId is Guid existingId)
        {
            conversation = await _db.Conversations
                .FirstOrDefaultAsync(
                    c => c.Id == existingId && c.UserId == userId,
                    cancellationToken)
                ?? throw new InvalidOperationException(
                    $"Conversation {existingId} not found for current user.");
        }
        else
        {
            conversation = new Conversation
            {
                Id        = Guid.NewGuid(),
                UserId    = userId,
                Title     = BuildAutoTitle(userMessage),
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow,
            };
            _db.Conversations.Add(conversation);
            isNew = true;
        }

        // Persist user message into the conversation.
        var userMsg = new ChatMessage
        {
            Id             = Guid.NewGuid(),
            ConversationId = conversation.Id,
            Role           = ChatRole.User,
            Content        = userMessage,
            CreatedAt      = DateTime.UtcNow,
        };
        _db.ChatMessages.Add(userMsg);

        conversation.UpdatedAt = DateTime.UtcNow;
        await _db.SaveChangesAsync(cancellationToken);

        if (isNew)
        {
            _ = _audit.LogAsync(
                "chat.start", "chat",
                resourceType: nameof(Conversation),
                resourceId:   conversation.Id.ToString(),
                details: new { conversationId = conversation.Id });
        }

        return conversation;
    }

    // ==================================================================
    //  2. Stream the assistant reply
    // ==================================================================

    public async IAsyncEnumerable<QueryEvent> StreamReplyAsync(
        Conversation conversation,
        string userMessage,
        string departmentId,
        string userId,
        [System.Runtime.CompilerServices.EnumeratorCancellation]
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(conversation);
        ArgumentException.ThrowIfNullOrWhiteSpace(departmentId);

        // ---- Build chat history (excluding the just-added user message) ----
        var history = await BuildHistoryAsync(conversation.Id, cancellationToken);

        var request = new QueryRequest(
            Query:        userMessage,
            DepartmentId: departmentId,
            History:      history,
            UserId:       userId);

        // ---- Accumulators (assembled across the streamed events) ----
        var fullReply        = new StringBuilder();
        var sourceDocumentIds = new HashSet<string>();
        long  latencyMs    = 0;
        string finishReason = "incomplete";

        // try/finally around yield is legal — persists the partial reply
        // even if the consumer cancels mid-stream.
        try
        {
            await foreach (var evt in _worker.QueryAsync(request, cancellationToken))
            {
                switch (evt)
                {
                    case QueryEvent.Sources s:
                        foreach (var doc in s.Documents)
                        {
                            if (!string.IsNullOrWhiteSpace(doc.DocumentId))
                                sourceDocumentIds.Add(doc.DocumentId);
                        }
                        break;

                    case QueryEvent.Token t:
                        fullReply.Append(t.Content);
                        break;

                    case QueryEvent.Done d:
                        latencyMs    = d.LatencyMs;
                        finishReason = d.FinishReason;
                        break;

                    case QueryEvent.Error e:
                        _logger.LogWarning(
                            "ChatService worker emitted error for conv {ConvId}: {Msg}",
                            conversation.Id, e.Message);
                        if (finishReason == "incomplete") finishReason = "error";
                        break;
                }

                yield return evt;
            }
        }
        finally
        {
            // Always persist *something* — even an empty assistant message — so
            // the UI list reflects the attempt. Use CancellationToken.None so
            // a user-cancelled stream still saves the partial reply.
            await PersistAssistantMessageAsync(
                conversation,
                fullReply.ToString(),
                sourceDocumentIds,
                latencyMs,
                finishReason);
        }
    }

    // ==================================================================
    //  Helpers
    // ==================================================================

    private async Task<IReadOnlyList<ChatHistoryItem>> BuildHistoryAsync(
        Guid conversationId, CancellationToken ct)
    {
        // Last HistoryWindow messages chronologically. We pull them with a
        // descending order + Take, then reverse client-side for chronological.
        var recent = await _db.ChatMessages
            .AsNoTracking()
            .Where(m => m.ConversationId == conversationId)
            .OrderByDescending(m => m.CreatedAt)
            .Take(HistoryWindow)
            .Select(m => new { m.Role, m.Content, m.CreatedAt })
            .ToListAsync(ct);

        return recent
            .OrderBy(m => m.CreatedAt)
            .Select(m => new ChatHistoryItem(
                Role:    RoleToWire(m.Role),
                Content: m.Content))
            .ToList();
    }

    private async Task PersistAssistantMessageAsync(
        Conversation conversation,
        string       fullReply,
        HashSet<string> sourceDocumentIds,
        long latencyMs,
        string finishReason)
    {
        try
        {
            var assistant = new ChatMessage
            {
                Id                    = Guid.NewGuid(),
                ConversationId        = conversation.Id,
                Role                  = ChatRole.Assistant,
                Content               = fullReply,
                SourceDocumentIdsJson = sourceDocumentIds.Count == 0
                                            ? null
                                            : JsonSerializer.Serialize(sourceDocumentIds),
                CreatedAt             = DateTime.UtcNow,
                LatencyMs             = latencyMs > 0 ? (int)Math.Min(int.MaxValue, latencyMs) : null,
            };

            _db.ChatMessages.Add(assistant);
            conversation.UpdatedAt = DateTime.UtcNow;
            await _db.SaveChangesAsync(CancellationToken.None);

            _logger.LogInformation(
                "chat_reply_saved conv={ConvId} chars={Chars} sources={N} finish={Reason}",
                conversation.Id, fullReply.Length, sourceDocumentIds.Count, finishReason);

            // ---- Audit: chat.message (success) or chat.error ----
            var ok = finishReason != "error";
            _ = _audit.LogAsync(
                ok ? "chat.message" : "chat.error",
                "chat",
                ok ? LogSeverity.Info : LogSeverity.Error,
                resourceType: nameof(ChatMessage),
                resourceId:   assistant.Id.ToString(),
                overrideUserId: conversation.UserId,
                details: new
                {
                    conversationId = conversation.Id,
                    chatMessageId  = assistant.Id,
                    latencyMs      = latencyMs,
                    chunkCount     = sourceDocumentIds.Count,
                    finishReason   = finishReason,
                },
                success: ok);
        }
        catch (Exception ex)
        {
            // Never let persistence failure escape the iterator. The stream
            // already completed from the consumer's perspective.
            _logger.LogError(ex,
                "ChatService failed to persist assistant message for conv {ConvId}",
                conversation.Id);
        }
    }

    private static string BuildAutoTitle(string firstMessage)
    {
        var trimmed = firstMessage.Trim();
        if (trimmed.Length <= AutoTitleMaxLen) return trimmed;
        return trimmed[..AutoTitleMaxLen].TrimEnd() + "…";
    }

    private static string RoleToWire(ChatRole r) => r switch
    {
        ChatRole.User      => "user",
        ChatRole.Assistant => "assistant",
        ChatRole.System    => "system",
        _                  => "user",
    };
}
