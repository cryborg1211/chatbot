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
    string?      IpAddress,
    bool         Success);
