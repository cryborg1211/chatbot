using System.Security.Claims;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using chatbot.Infrastructure.AiWorker.Contracts;
using chatbot.Infrastructure.Identity;
using chatbot.Models;
using chatbot.Services.Chat;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace chatbot.Controllers.Api;

/// <summary>
/// SSE pipe-through for the RAG chat. Browser POSTs a message,
/// receives the assistant reply token-by-token as Server-Sent Events.
///
/// Tenant rule (§5.5): <c>DepartmentId</c> read from the authenticated
/// principal — never from request body.
/// </summary>
[ApiController]
[Authorize]
[Route("api/chat")]
public sealed class ChatController : ControllerBase
{
    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy   = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly IChatService _chat;
    private readonly ILogger<ChatController> _logger;

    public ChatController(IChatService chat, ILogger<ChatController> logger)
    {
        _chat   = chat;
        _logger = logger;
    }

    // ------------------------------------------------------------------
    //  POST /api/chat/send
    //  Request: { conversationId?: Guid, content: string }
    //  Response: text/event-stream (event: sources|token|done|error)
    //
    //  The browser reads `X-Conversation-Id` from the response headers
    //  to learn the conversation id (especially on first message).
    // ------------------------------------------------------------------
    [HttpPost("send")]
    public async Task SendAsync(
        [FromBody] SendMessageRequest request,
        CancellationToken cancellationToken)
    {
        // ---- 1. Claims & validation ----
        var departmentId = User.FindFirstValue(AppClaimTypes.DepartmentId);
        var userId       = User.FindFirstValue(ClaimTypes.NameIdentifier);

        if (string.IsNullOrWhiteSpace(departmentId) || string.IsNullOrWhiteSpace(userId))
        {
            Response.StatusCode = StatusCodes.Status403Forbidden;
            await Response.WriteAsync("Missing required claims.", cancellationToken);
            return;
        }

        if (string.IsNullOrWhiteSpace(request?.Content))
        {
            Response.StatusCode = StatusCodes.Status400BadRequest;
            await Response.WriteAsync("Message content is required.", cancellationToken);
            return;
        }

        // ---- 2. Prepare conversation (persists user message) ----
        Conversation conv;
        try
        {
            conv = await _chat.PrepareConversationAsync(
                request.ConversationId,
                request.Content,
                userId,
                cancellationToken);
        }
        catch (InvalidOperationException ex)
        {
            _logger.LogWarning(ex, "Bad conversation reference from user {UserId}", userId);
            Response.StatusCode = StatusCodes.Status404NotFound;
            await Response.WriteAsync("Conversation not found.", cancellationToken);
            return;
        }

        // ---- 3. SSE response headers ----
        Response.StatusCode = StatusCodes.Status200OK;
        Response.Headers.ContentType    = "text/event-stream";
        Response.Headers.CacheControl   = "no-cache";
        Response.Headers.Connection     = "keep-alive";
        Response.Headers["X-Accel-Buffering"]  = "no";
        Response.Headers["X-Conversation-Id"]  = conv.Id.ToString();

        await Response.Body.FlushAsync(cancellationToken);

        // ---- 4. Pipe worker events to the wire ----
        try
        {
            await foreach (var evt in _chat.StreamReplyAsync(
                conv, request.Content, departmentId, userId, cancellationToken))
            {
                await WriteSseEventAsync(evt, cancellationToken);
                await Response.Body.FlushAsync(cancellationToken);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            // Browser disconnected mid-stream — ChatService's finally clause
            // has already persisted whatever we got. Nothing else to do.
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "ChatController stream broke mid-flight for conv {ConvId}", conv.Id);
            // Best-effort emit final error/done so the UI can clean up.
            await TryWriteSseAsync("error", new { message = "Internal error during stream." });
            await TryWriteSseAsync("done",  new { finish_reason = "error", latency_ms = 0 });
        }
    }

    // ==================================================================
    //  SSE encoder
    // ==================================================================

    /// <summary>Maps a <see cref="QueryEvent"/> to its on-wire SSE form and writes it.</summary>
    private async Task WriteSseEventAsync(QueryEvent evt, CancellationToken ct)
    {
        var (name, payload) = evt switch
        {
            QueryEvent.Sources s => ("sources", (object)new { documents = s.Documents }),
            QueryEvent.Token   t => ("token",   (object)new { content   = t.Content }),
            QueryEvent.Done    d => ("done",    (object)new
            {
                finish_reason     = d.FinishReason,
                latency_ms        = d.LatencyMs,
                prompt_tokens     = d.PromptTokens,
                completion_tokens = d.CompletionTokens,
            }),
            QueryEvent.Error   e => ("error",   (object)new { message = e.Message }),
            _                    => ("unknown", (object)new { }),
        };

        var json = JsonSerializer.Serialize(payload, JsonOpts);
        var sse  = $"event: {name}\ndata: {json}\n\n";

        await Response.WriteAsync(sse, Encoding.UTF8, ct);
    }

    private async Task TryWriteSseAsync(string name, object payload)
    {
        try
        {
            var json = JsonSerializer.Serialize(payload, JsonOpts);
            var sse  = $"event: {name}\ndata: {json}\n\n";
            await Response.WriteAsync(sse, Encoding.UTF8, CancellationToken.None);
            await Response.Body.FlushAsync(CancellationToken.None);
        }
        catch { /* swallowed — client likely gone */ }
    }
}

// =====================================================================
//  Request DTO
// =====================================================================

public sealed record SendMessageRequest(
    Guid?  ConversationId,
    string Content);
