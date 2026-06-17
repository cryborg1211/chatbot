using chatbot.Data;
using chatbot.Infrastructure.Authorization;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Pages.Admin;

/// <summary>
/// Admin-only system log viewer. Paginated, filterable by action /
/// category / date range.  See <c>.claude/audit_system.md</c> §9 for
/// the query patterns.
/// </summary>
[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]
public sealed class LogsModel : PageModel
{
    public const int PageSize = 20;

    private readonly ApplicationDbContext _db;

    public LogsModel(ApplicationDbContext db) => _db = db;

    [BindProperty(SupportsGet = true)] public string?   Action     { get; set; }
    [BindProperty(SupportsGet = true)] public string?   Category   { get; set; }
    [BindProperty(SupportsGet = true)] public DateTime? FromDate   { get; set; }
    [BindProperty(SupportsGet = true)] public DateTime? ToDate     { get; set; }
    [BindProperty(SupportsGet = true)] public int       PageNumber { get; set; } = 1;

    public IList<LogRow> Items { get; private set; } = new List<LogRow>();

    public int Total24h    { get; private set; }
    public int TotalCount  { get; private set; }
    public int TotalPages  => Math.Max(1, (int)Math.Ceiling(TotalCount / (double)PageSize));

    public IReadOnlyList<string> KnownCategories { get; } = new[] { "auth", "user", "doc", "chat", "sys" };

    public async Task OnGetAsync(CancellationToken cancellationToken)
    {
        var since24h = DateTime.UtcNow.AddDays(-1);
        Total24h = await _db.SystemLogs.CountAsync(l => l.Timestamp >= since24h, cancellationToken);

        var query = _db.SystemLogs.AsNoTracking().AsQueryable();
        if (!string.IsNullOrWhiteSpace(Action))   query = query.Where(l => l.Action   == Action);
        if (!string.IsNullOrWhiteSpace(Category)) query = query.Where(l => l.Category == Category);
        if (FromDate.HasValue)                    query = query.Where(l => l.Timestamp >= FromDate.Value);
        if (ToDate.HasValue)                      query = query.Where(l => l.Timestamp <= ToDate.Value);

        TotalCount = await query.CountAsync(cancellationToken);

        var skip = (Math.Max(1, PageNumber) - 1) * PageSize;

        var pageItems = await query
            .OrderByDescending(l => l.Timestamp)
            .Skip(skip)
            .Take(PageSize)
            .Select(l => new
            {
                l.Id,
                l.Timestamp,
                l.Action,
                l.Category,
                l.Severity,
                l.UserId,
                l.DepartmentId,
                l.ResourceType,
                l.ResourceId,
                l.IpAddress,
                l.Success,
            })
            .ToListAsync(cancellationToken);

        // Lookup display names for the userIds that appear on this page.
        var userIds = pageItems
            .Where(r => !string.IsNullOrWhiteSpace(r.UserId))
            .Select(r => r.UserId!)
            .Distinct()
            .ToList();

        var userNameMap = userIds.Count == 0
            ? new Dictionary<string, string>()
            : await _db.Users
                .AsNoTracking()
                .Where(u => userIds.Contains(u.Id))
                .Select(u => new { u.Id, u.FullName })
                .ToDictionaryAsync(u => u.Id, u => u.FullName, cancellationToken);

        // Resolve document resource ids → original file names so the log shows
        // a human-readable name instead of the raw UUID. Deleted documents
        // fall back to the id (handled in the projection below).
        var docIds = pageItems
            .Where(r => r.ResourceType == nameof(Document) && !string.IsNullOrWhiteSpace(r.ResourceId))
            .Select(r => r.ResourceId!)
            .Distinct()
            .ToList();

        var docNameMap = new Dictionary<string, string>();
        if (docIds.Count > 0)
        {
            var guidIds = docIds
                .Select(s => Guid.TryParse(s, out var g) ? g : (Guid?)null)
                .Where(g => g.HasValue)
                .Select(g => g!.Value)
                .ToList();

            if (guidIds.Count > 0)
            {
                docNameMap = await _db.Documents
                    .AsNoTracking()
                    .Where(d => guidIds.Contains(d.Id))
                    .Select(d => new { d.Id, d.OriginalFileName })
                    .ToDictionaryAsync(d => d.Id.ToString(), d => d.OriginalFileName, cancellationToken);
            }
        }

        Items = pageItems.Select(r => new LogRow(
            r.Id,
            r.Timestamp,
            r.Action,
            r.Category,
            r.Severity,
            r.UserId is null ? null : userNameMap.GetValueOrDefault(r.UserId),
            r.UserId,
            r.DepartmentId,
            r.ResourceType,
            r.ResourceId,
            r.ResourceType == nameof(Document) && r.ResourceId is not null
                ? docNameMap.GetValueOrDefault(r.ResourceId)
                : null,
            r.IpAddress,
            r.Success)).ToList();
    }
}

public sealed record LogRow(
    long         Id,
    DateTime     Timestamp,
    string       Action,
    string       Category,
    LogSeverity  Severity,
    string?      UserFullName,
    string?      UserId,
    string?      DepartmentId,
    string?      ResourceType,
    string?      ResourceId,
    string?      ResourceName,
    string?      IpAddress,
    bool         Success);
