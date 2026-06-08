using System.Security.Claims;
using chatbot.Data;
using chatbot.Infrastructure.Audit;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Controllers.Api;

/// <summary>
/// Thumbs-up / thumbs-down on an assistant <see cref="ChatMessage"/>.
/// Re-clicking the same direction is idempotent; clicking the opposite
/// direction flips the existing row.
///
/// Tenant rule (§5.5): the message must belong to a conversation owned
/// by the current user — a 403 is returned otherwise.
/// </summary>
[ApiController]
[Authorize]
[Route("api/feedback")]
public sealed class FeedbackController : ControllerBase
{
    private readonly ApplicationDbContext _db;
    private readonly IAuditLogger _audit;
    private readonly ILogger<FeedbackController> _logger;

    public FeedbackController(
        ApplicationDbContext db,
        IAuditLogger audit,
        ILogger<FeedbackController> logger)
    {
        _db     = db;
        _audit  = audit;
        _logger = logger;
    }

    // POST /api/feedback
    [HttpPost]
    public async Task<IActionResult> Submit(
        [FromBody] FeedbackRequest request,
        CancellationToken cancellationToken)
    {
        if (request is null)
            return BadRequest(new { error = "Body required." });

        if (request.Rating != 1 && request.Rating != -1)
            return BadRequest(new { error = "Rating must be +1 or -1." });

        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (string.IsNullOrWhiteSpace(userId))
            return Forbid();

        // ---- Verify the target message is an assistant message in a conversation the user owns ----
        var msgInfo = await _db.ChatMessages
            .AsNoTracking()
            .Where(m => m.Id == request.ChatMessageId)
            .Select(m => new { m.Id, m.Role, m.ConversationId })
            .FirstOrDefaultAsync(cancellationToken);

        if (msgInfo is null)
            return NotFound();

        if (msgInfo.Role != ChatRole.Assistant)
            return BadRequest(new { error = "Only assistant messages can be rated." });

        var convoOwnedByUser = await _db.Conversations
            .AnyAsync(c => c.Id == msgInfo.ConversationId && c.UserId == userId, cancellationToken);

        if (!convoOwnedByUser)
            return Forbid();

        // ---- Upsert on (UserId, ChatMessageId) ----
        var existing = await _db.Feedbacks
            .FirstOrDefaultAsync(
                f => f.UserId == userId && f.ChatMessageId == request.ChatMessageId,
                cancellationToken);

        var rating  = (FeedbackRating)request.Rating;
        var comment = string.IsNullOrWhiteSpace(request.Comment) ? null : request.Comment.Trim();

        bool wasNew = existing is null;
        FeedbackRating? oldRating = existing?.Rating;

        if (existing is null)
        {
            _db.Feedbacks.Add(new Feedback
            {
                Id            = Guid.NewGuid(),
                UserId        = userId,
                ChatMessageId = request.ChatMessageId,
                Rating        = rating,
                Comment       = comment,
                CreatedAt     = DateTime.UtcNow,
                UpdatedAt     = DateTime.UtcNow,
            });
        }
        else
        {
            existing.Rating    = rating;
            existing.Comment   = comment ?? existing.Comment;
            existing.UpdatedAt = DateTime.UtcNow;
        }

        await _db.SaveChangesAsync(cancellationToken);

        _logger.LogInformation(
            "feedback_saved user={UserId} msg={MsgId} rating={Rating}",
            userId, request.ChatMessageId, (int)rating);

        _ = _audit.LogAsync(
            wasNew ? "feedback.submit" : "feedback.update",
            "chat",
            resourceType: nameof(ChatMessage),
            resourceId:   request.ChatMessageId.ToString(),
            details: wasNew
                ? (object)new { chatMessageId = request.ChatMessageId, rating = (int)rating }
                : (object)new { chatMessageId = request.ChatMessageId, oldRating = (int?)oldRating, newRating = (int)rating });

        return NoContent();
    }
}

public sealed record FeedbackRequest(
    Guid    ChatMessageId,
    int     Rating,       // +1 or -1
    string? Comment);
