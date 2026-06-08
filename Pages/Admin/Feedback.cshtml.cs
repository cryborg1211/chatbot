using chatbot.Data;
using chatbot.Infrastructure.Authorization;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Pages.Admin;

/// <summary>
/// Admin-only feedback review. Joins <see cref="Feedback"/> with its
/// assistant <see cref="ChatMessage"/> and the user question that
/// preceded it in the same conversation.
/// </summary>
[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]
public sealed class FeedbackModel : PageModel
{
    public const int PageSize = 10;

    private readonly ApplicationDbContext _db;

    public FeedbackModel(ApplicationDbContext db) => _db = db;

    [BindProperty(SupportsGet = true)] public string Filter     { get; set; } = "all";   // "all" | "up" | "down"
    [BindProperty(SupportsGet = true)] public int    PageNumber { get; set; } = 1;

    public IList<FeedbackRow> Items { get; private set; } = new List<FeedbackRow>();

    public int TotalCount     { get; private set; }
    public int ThumbsUpCount  { get; private set; }
    public int ThumbsDownCount{ get; private set; }
    public int TotalPages     => Math.Max(1, (int)Math.Ceiling(TotalCount / (double)PageSize));

    public async Task OnGetAsync(CancellationToken cancellationToken)
    {
        ThumbsUpCount   = await _db.Feedbacks.CountAsync(f => f.Rating == FeedbackRating.ThumbsUp,   cancellationToken);
        ThumbsDownCount = await _db.Feedbacks.CountAsync(f => f.Rating == FeedbackRating.ThumbsDown, cancellationToken);

        var query = _db.Feedbacks.AsNoTracking().AsQueryable();
        query = Filter switch
        {
            "up"   => query.Where(f => f.Rating == FeedbackRating.ThumbsUp),
            "down" => query.Where(f => f.Rating == FeedbackRating.ThumbsDown),
            _      => query,
        };

        TotalCount = await query.CountAsync(cancellationToken);

        var skip = (Math.Max(1, PageNumber) - 1) * PageSize;
        var feedbackPage = await query
            .OrderByDescending(f => f.UpdatedAt)
            .Skip(skip)
            .Take(PageSize)
            .Select(f => new
            {
                f.Id,
                f.ChatMessageId,
                f.UserId,
                f.Rating,
                f.UpdatedAt,
            })
            .ToListAsync(cancellationToken);

        if (feedbackPage.Count == 0) return;

        // Fetch the assistant messages + their convo ids.
        var assistantIds = feedbackPage.Select(f => f.ChatMessageId).Distinct().ToList();
        var assistants = await _db.ChatMessages
            .AsNoTracking()
            .Where(m => assistantIds.Contains(m.Id))
            .Select(m => new { m.Id, m.ConversationId, m.Content, m.CreatedAt })
            .ToListAsync(cancellationToken);

        // Pull every message from those conversations once so we can
        // resolve "the user question that preceded the assistant reply"
        // entirely in memory.
        var convIds = assistants.Select(a => a.ConversationId).Distinct().ToList();
        var convoMessages = await _db.ChatMessages
            .AsNoTracking()
            .Where(m => convIds.Contains(m.ConversationId))
            .Select(m => new { m.ConversationId, m.Role, m.Content, m.CreatedAt })
            .ToListAsync(cancellationToken);

        var userIds = feedbackPage.Select(f => f.UserId).Distinct().ToList();
        var userMap = await _db.Users
            .AsNoTracking()
            .Where(u => userIds.Contains(u.Id))
            .Select(u => new { u.Id, u.FullName, u.DepartmentId })
            .ToDictionaryAsync(u => u.Id, u => (u.FullName, u.DepartmentId), cancellationToken);

        var assistantMap = assistants.ToDictionary(a => a.Id);

        Items = feedbackPage.Select(f =>
        {
            assistantMap.TryGetValue(f.ChatMessageId, out var asst);
            var assistantContent = asst?.Content ?? "(đã bị xoá)";
            var question = asst is null
                ? string.Empty
                : convoMessages
                    .Where(m => m.ConversationId == asst.ConversationId
                                && m.Role == ChatRole.User
                                && m.CreatedAt <= asst.CreatedAt)
                    .OrderByDescending(m => m.CreatedAt)
                    .Select(m => m.Content)
                    .FirstOrDefault() ?? string.Empty;

            userMap.TryGetValue(f.UserId, out var who);

            return new FeedbackRow(
                f.Id,
                who.FullName       ?? "(người dùng cũ)",
                who.DepartmentId   ?? string.Empty,
                question,
                assistantContent,
                f.Rating,
                f.UpdatedAt);
        }).ToList();
    }
}

public sealed record FeedbackRow(
    Guid           Id,
    string         UserFullName,
    string         DepartmentId,
    string         Question,
    string         Answer,
    FeedbackRating Rating,
    DateTime       At);
