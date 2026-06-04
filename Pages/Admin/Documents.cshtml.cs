using System.Security.Claims;
using chatbot.Data;
using chatbot.Infrastructure.Identity;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Pages.Admin;

/// <summary>
/// Page model for the document-management UI at <c>/Admin/Documents</c>.
/// Read-only view bound to the current user's tenant.
///
/// Tenant rule (§5.5): the list is filtered by <c>DepartmentId</c> taken
/// from <see cref="AppClaimTypes.DepartmentId"/> on the authenticated
/// principal — never from query string, body, or route.
/// </summary>
[Authorize]
public sealed class DocumentsModel : PageModel
{
    private const int PageSize = 50;

    private readonly ApplicationDbContext _db;

    public DocumentsModel(ApplicationDbContext db) => _db = db;

    // ---- Render data ----
    public IReadOnlyList<Document> Documents { get; private set; } = Array.Empty<Document>();
    public int TotalCount      { get; private set; }
    public int ReadyCount      { get; private set; }
    public int ProcessingCount { get; private set; }   // Pending + Processing
    public int FailedCount     { get; private set; }

    public async Task<IActionResult> OnGetAsync(CancellationToken cancellationToken)
    {
        var departmentId = User.FindFirstValue(AppClaimTypes.DepartmentId);
        if (string.IsNullOrWhiteSpace(departmentId))
            return Forbid();

        var tenantQuery = _db.Documents
            .AsNoTracking()
            .Where(d => d.DepartmentId == departmentId);

        // ---- 1. List (latest first) ----
        Documents = await tenantQuery
            .OrderByDescending(d => d.UploadedAt)
            .Take(PageSize)
            .ToListAsync(cancellationToken);

        // ---- 2. Status counters in a single round trip ----
        var counts = await tenantQuery
            .GroupBy(d => d.Status)
            .Select(g => new { Status = g.Key, Count = g.Count() })
            .ToListAsync(cancellationToken);

        int CountFor(DocumentStatus s) =>
            counts.FirstOrDefault(c => c.Status == s)?.Count ?? 0;

        TotalCount      = counts.Sum(c => c.Count);
        ReadyCount      = CountFor(DocumentStatus.Ready);
        ProcessingCount = CountFor(DocumentStatus.Pending) + CountFor(DocumentStatus.Processing);
        FailedCount     = CountFor(DocumentStatus.Failed);

        return Page();
    }
}
