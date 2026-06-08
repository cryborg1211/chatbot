using System.Security.Claims;
using System.Text.Json;
using chatbot.Data;
using chatbot.Infrastructure.Identity;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Pages.Chat;

/// <summary>
/// Page model for <c>/Chat</c>. Renders:
///   • sidebar — current user's recent conversations
///   • main pane — messages of the currently selected conversation
///     (or the empty welcome state if no id in query string)
///
/// All data is scoped to the authenticated user's <c>NameIdentifier</c>
/// claim — never trust query-string ids; a user can never load another
/// user's conversation even by guessing its GUID.
/// </summary>
[Authorize]
public sealed class IndexModel : PageModel
{
    private const int SidebarPageSize = 20;

    private readonly ApplicationDbContext _db;

    public IndexModel(ApplicationDbContext db) => _db = db;

    // ---- Render data ----
    public string FullName { get; private set; } = "Người dùng";
    public IReadOnlyList<ConversationSidebarItem> RecentConversations { get; private set; } = Array.Empty<ConversationSidebarItem>();
    public Conversation? CurrentConversation { get; private set; }
    public IReadOnlyList<ChatMessage> Messages { get; private set; } = Array.Empty<ChatMessage>();

    /// <summary>Current user's thumbs vote per assistant message id (if any).</summary>
    public IReadOnlyDictionary<Guid, FeedbackRating> MyFeedback { get; private set; } =
        new Dictionary<Guid, FeedbackRating>();

    public async Task<IActionResult> OnGetAsync(Guid? id, CancellationToken cancellationToken)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (string.IsNullOrWhiteSpace(userId))
            return Forbid();

        FullName = User.FindFirstValue(AppClaimTypes.FullName) ?? "Người dùng";

        // ---- Sidebar: recent conversations ----
        RecentConversations = await _db.Conversations
            .AsNoTracking()
            .Where(c => c.UserId == userId)
            .OrderByDescending(c => c.UpdatedAt)
            .Take(SidebarPageSize)
            .Select(c => new ConversationSidebarItem(c.Id, c.Title, c.UpdatedAt))
            .ToListAsync(cancellationToken);

        // ---- Main pane: load selected conversation, if any ----
        if (id is Guid convoId)
        {
            CurrentConversation = await _db.Conversations
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    c => c.Id == convoId && c.UserId == userId,
                    cancellationToken);

            if (CurrentConversation is null)
                return NotFound();

            Messages = await _db.ChatMessages
                .AsNoTracking()
                .Where(m => m.ConversationId == convoId)
                .OrderBy(m => m.CreatedAt)
                .ToListAsync(cancellationToken);

            // ---- Preload this user's existing thumbs on assistant messages ----
            var assistantIds = Messages
                .Where(m => m.Role == ChatRole.Assistant)
                .Select(m => m.Id)
                .ToList();

            if (assistantIds.Count > 0)
            {
                MyFeedback = await _db.Feedbacks
                    .AsNoTracking()
                    .Where(f => f.UserId == userId && assistantIds.Contains(f.ChatMessageId))
                    .ToDictionaryAsync(f => f.ChatMessageId, f => f.Rating, cancellationToken);
            }
        }

        return Page();
    }

    // -----------------------------------------------------------------
    //  Server-side citation helper — parses the JSON we stored in
    //  ChatMessage.SourceDocumentIdsJson (shape: [{id, title}, ...])
    //  and tolerates legacy "list of ids only" storage as well.
    // -----------------------------------------------------------------

    public static IReadOnlyList<SourceCite> ParseSourceCites(string? json)
    {
        if (string.IsNullOrWhiteSpace(json))
            return Array.Empty<SourceCite>();

        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind != JsonValueKind.Array)
                return Array.Empty<SourceCite>();

            var result = new List<SourceCite>();
            foreach (var el in doc.RootElement.EnumerateArray())
            {
                if (el.ValueKind == JsonValueKind.Object)
                {
                    var id    = el.TryGetProperty("id",    out var idEl)    ? idEl.GetString()    ?? "" : "";
                    var title = el.TryGetProperty("title", out var titleEl) ? titleEl.GetString() ?? "Tài liệu" : "Tài liệu";
                    result.Add(new SourceCite(id, title));
                }
                else if (el.ValueKind == JsonValueKind.String)
                {
                    // Legacy: just an array of ids.
                    result.Add(new SourceCite(el.GetString() ?? "", "Tài liệu"));
                }
            }
            return result;
        }
        catch
        {
            return Array.Empty<SourceCite>();
        }
    }
}

// =====================================================================
//  View-model DTOs
// =====================================================================

public sealed record ConversationSidebarItem(Guid Id, string Title, DateTime UpdatedAt);

public sealed record SourceCite(string Id, string Title);
