using chatbot.Data;
using chatbot.Infrastructure.Audit;
using chatbot.Infrastructure.Authorization;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Pages.Admin;

/// <summary>
/// Admin-only user management. Lists every user with search + dept filter,
/// shows status (Active / Pending / Locked), supports approve and reject
/// handlers. Audit-logged via <see cref="IAuditLogger"/>.
/// </summary>
[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]
public sealed class UsersModel : PageModel
{
    public const int PageSize = 10;

    private readonly ApplicationDbContext _db;
    private readonly UserManager<ApplicationUser> _userManager;
    private readonly IAuditLogger _audit;

    public UsersModel(
        ApplicationDbContext db,
        UserManager<ApplicationUser> userManager,
        IAuditLogger audit)
    {
        _db          = db;
        _userManager = userManager;
        _audit       = audit;
    }

    // ---- Filters (bind from query string) ----
    [BindProperty(SupportsGet = true)] public string? Search       { get; set; }
    [BindProperty(SupportsGet = true)] public string? DepartmentId { get; set; }
    [BindProperty(SupportsGet = true)] public int     PageNumber   { get; set; } = 1;

    // ---- Render data ----
    public IList<UserRow>   Items        { get; private set; } = new List<UserRow>();
    public IList<Department> Departments { get; private set; } = new List<Department>();

    public int TotalCount    { get; private set; }
    public int TotalUsers    { get; private set; }
    public int PendingCount  { get; private set; }
    public int ActiveCount   { get; private set; }
    public int LockedCount   { get; private set; }
    public int TotalPages    => Math.Max(1, (int)Math.Ceiling(TotalCount / (double)PageSize));

    public async Task OnGetAsync(CancellationToken cancellationToken)
    {
        Departments = await _db.Departments
            .AsNoTracking()
            .OrderBy(d => d.Id)
            .ToListAsync(cancellationToken);

        // Counters across the WHOLE table (not filtered).
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
    //  Handlers — Approve / Reject. Posted as forms with antiforgery.
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

public enum UserStatus { Pending = 0, Active = 1, Locked = 2 }

public sealed record UserRow(
    string     Id,
    string     FullName,
    string     Email,
    string     DepartmentId,
    string     DepartmentName,
    UserStatus Status);
