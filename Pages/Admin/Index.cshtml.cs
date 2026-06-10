using System.Security.Claims;
using chatbot.Data;
using chatbot.Infrastructure.Audit;
using chatbot.Infrastructure.Authorization;
using chatbot.Infrastructure.Identity;
using chatbot.Models;
using chatbot.Pages.Chat;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Pages.Admin;

/// <summary>
/// Unified admin page at <c>/admin</c>. Combines the Chat sidebar
/// (real conversation history) with the Users dashboard (stats + table + actions).
/// </summary>
[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]
public sealed class AdminIndexModel : PageModel
{
    private const int SidebarPageSize = 20;
    public  const int PageSize        = 10;

    private readonly ApplicationDbContext _db;
    private readonly UserManager<ApplicationUser> _userManager;
    private readonly IAuditLogger _audit;

    public AdminIndexModel(
        ApplicationDbContext db,
        UserManager<ApplicationUser> userManager,
        IAuditLogger audit)
    {
        _db          = db;
        _userManager = userManager;
        _audit       = audit;
    }

    // ---- Chat sidebar data ----
    public string FullName { get; private set; } = "Quản trị viên";
    public IReadOnlyList<ConversationSidebarItem> RecentConversations { get; private set; } = Array.Empty<ConversationSidebarItem>();

    // ---- Users filters (bind from query string) ----
    [BindProperty(SupportsGet = true)] public string? Search       { get; set; }
    [BindProperty(SupportsGet = true)] public string? DepartmentId { get; set; }
    [BindProperty(SupportsGet = true)] public int     PageNumber   { get; set; } = 1;

    // ---- Users render data ----
    public IList<UserRow>    Items       { get; private set; } = new List<UserRow>();
    public IList<Department> Departments { get; private set; } = new List<Department>();

    public int TotalCount   { get; private set; }
    public int TotalUsers   { get; private set; }
    public int PendingCount { get; private set; }
    public int ActiveCount  { get; private set; }
    public int LockedCount  { get; private set; }
    public int TotalPages   => Math.Max(1, (int)Math.Ceiling(TotalCount / (double)PageSize));

    public async Task OnGetAsync(CancellationToken cancellationToken)
    {
        // ---- Chat sidebar data ----
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        FullName = User.FindFirstValue(AppClaimTypes.FullName) ?? "Quản trị viên";

        if (!string.IsNullOrWhiteSpace(userId))
        {
            RecentConversations = await _db.Conversations
                .AsNoTracking()
                .Where(c => c.UserId == userId)
                .OrderByDescending(c => c.UpdatedAt)
                .Take(SidebarPageSize)
                .Select(c => new ConversationSidebarItem(c.Id, c.Title, c.UpdatedAt))
                .ToListAsync(cancellationToken);
        }

        // ---- Users dashboard data ----
        Departments = await _db.Departments
            .AsNoTracking()
            .OrderBy(d => d.Id)
            .ToListAsync(cancellationToken);

        var now = DateTimeOffset.UtcNow;
        TotalUsers   = await _userManager.Users.CountAsync(cancellationToken);
        PendingCount = await _userManager.Users.CountAsync(u => !u.EmailConfirmed, cancellationToken);
        LockedCount  = await _userManager.Users.CountAsync(u => u.LockoutEnd != null && u.LockoutEnd > now, cancellationToken);
        ActiveCount  = TotalUsers - PendingCount - LockedCount;

        var query = _userManager.Users.AsNoTracking().AsQueryable();

        if (!string.IsNullOrWhiteSpace(Search))
        {
            var s = Search.Trim();
            query = query.Where(u => u.FullName.Contains(s) || u.Email!.Contains(s));
        }
        if (!string.IsNullOrWhiteSpace(DepartmentId))
        {
            var deptId = DepartmentId.Trim().ToUpperInvariant();
            query = query.Where(u => u.DepartmentId == deptId);
        }

        TotalCount = await query.CountAsync(cancellationToken);

        var skip = (Math.Max(1, PageNumber) - 1) * PageSize;
        var pageItems = await query
            .OrderBy(u => u.FullName)
            .Skip(skip)
            .Take(PageSize)
            .Select(u => new
            {
                u.Id,
                u.FullName,
                u.Email,
                u.DepartmentId,
                u.EmailConfirmed,
                u.LockoutEnd,
            })
            .ToListAsync(cancellationToken);

        var deptMap = Departments.ToDictionary(d => d.Id, d => d.Name);

        Items = pageItems.Select(u => new UserRow(
            u.Id,
            u.FullName ?? string.Empty,
            u.Email    ?? string.Empty,
            u.DepartmentId,
            deptMap.GetValueOrDefault(u.DepartmentId, u.DepartmentId),
            ComputeStatus(u.EmailConfirmed, u.LockoutEnd, now))).ToList();
    }

    // ----------------------------------------------------------------
    //  POST handlers — Approve / Reject / Activate (copied from UsersModel)
    // ----------------------------------------------------------------

    public async Task<IActionResult> OnPostApproveAsync(string id, CancellationToken cancellationToken)
    {
        var user = await _userManager.FindByIdAsync(id);
        if (user is null) return NotFound();

        user.EmailConfirmed = true;
        user.LockoutEnd     = null;
        await _userManager.UpdateAsync(user);

        if (!await _userManager.IsInRoleAsync(user, Roles.User))
            await _userManager.AddToRoleAsync(user, Roles.User);

        _ = _audit.LogAsync(
            "user.approve", "user",
            resourceType: nameof(ApplicationUser),
            resourceId:   user.Id,
            overrideDepartmentId: user.DepartmentId,
            details: new { targetUserId = user.Id, email = user.Email });

        return RedirectToPage(new { Search, DepartmentId, PageNumber });
    }

    public async Task<IActionResult> OnPostRejectAsync(string id, CancellationToken cancellationToken)
    {
        var user = await _userManager.FindByIdAsync(id);
        if (user is null) return NotFound();

        user.LockoutEnabled = true;
        user.LockoutEnd     = DateTimeOffset.UtcNow.AddYears(100);
        await _userManager.UpdateAsync(user);

        _ = _audit.LogAsync(
            "user.reject", "user", LogSeverity.Warn,
            resourceType: nameof(ApplicationUser),
            resourceId:   user.Id,
            overrideDepartmentId: user.DepartmentId,
            details: new { targetUserId = user.Id, email = user.Email });

        return RedirectToPage(new { Search, DepartmentId, PageNumber });
    }

    public async Task<IActionResult> OnPostActivateAsync(string id, CancellationToken cancellationToken)
    {
        var user = await _userManager.FindByIdAsync(id);
        if (user is null) return NotFound();

        user.LockoutEnd = null;
        await _userManager.UpdateAsync(user);

        _ = _audit.LogAsync(
            "user.approve", "user",
            resourceType: nameof(ApplicationUser),
            resourceId:   user.Id,
            details: new { targetUserId = user.Id, reason = "reactivated" });

        return RedirectToPage(new { Search, DepartmentId, PageNumber });
    }

    private static UserStatus ComputeStatus(bool emailConfirmed, DateTimeOffset? lockoutEnd, DateTimeOffset now)
    {
        if (lockoutEnd is { } end && end > now) return UserStatus.Locked;
        if (!emailConfirmed)                     return UserStatus.Pending;
        return UserStatus.Active;
    }
}
