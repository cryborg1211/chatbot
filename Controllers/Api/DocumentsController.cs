using System.Security.Claims;
using chatbot.Data;
using chatbot.Infrastructure.Identity;
using chatbot.Models;
using chatbot.Services.Documents;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Controllers.Api;

/// <summary>
/// Browser-facing JSON API for knowledge-base documents.
/// Thin HTTP layer — delegates business logic to <see cref="IDocumentService"/>.
///
/// Tenant rule (§5.5): <c>DepartmentId</c> is read from the authenticated
/// principal's <see cref="AppClaimTypes.DepartmentId"/> claim — never from
/// the request.
/// </summary>
[ApiController]
[Authorize]
[Route("api/documents")]
public sealed class DocumentsController : ControllerBase
{
    private readonly ApplicationDbContext _db;
    private readonly IDocumentService _documents;

    public DocumentsController(ApplicationDbContext db, IDocumentService documents)
    {
        _db        = db;
        _documents = documents;
    }

    // ------------------------------------------------------------------
    //  GET /api/documents
    //  Tenant-scoped list, newest first.
    // ------------------------------------------------------------------
    [HttpGet]
    public async Task<ActionResult<IEnumerable<DocumentListItem>>> List(
        CancellationToken cancellationToken)
    {
        if (!TryGetDepartmentId(out var departmentId))
            return Forbid();

        var rows = await _db.Documents
            .AsNoTracking()
            .Where(d => d.DepartmentId == departmentId)
            .OrderByDescending(d => d.UploadedAt)
            .Select(d => new DocumentListItem(
                d.Id,
                d.OriginalFileName,
                d.MimeType,
                d.SizeBytes,
                d.Status.ToString(),
                d.ChunkCount,
                d.UploadedAt,
                d.ProcessedAt,
                d.ErrorMessage))
            .ToListAsync(cancellationToken);

        return Ok(rows);
    }

    // ------------------------------------------------------------------
    //  POST /api/documents   (multipart/form-data: file)
    //  Non-blocking: validate, save blob, insert Pending row, return 202.
    //  The DocumentIngestionWorker picks it up from there.
    // ------------------------------------------------------------------
    [HttpPost]
    [RequestSizeLimit(DocumentLimits.MaxFileSizeBytes + DocumentLimits.MultipartOverheadBytes)]
    [RequestFormLimits(
        MultipartBodyLengthLimit = DocumentLimits.MaxFileSizeBytes + DocumentLimits.MultipartOverheadBytes)]
    public async Task<IActionResult> Upload(
        IFormFile file,
        CancellationToken cancellationToken)
    {
        // ---- Controller-layer guards ----
        if (file is null || file.Length == 0)
            return BadRequest(new { error = "No file uploaded." });

        if (!TryGetDepartmentId(out var departmentId))
            return Forbid();

        var uploaderId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (string.IsNullOrWhiteSpace(uploaderId))
            return Forbid();

        // ---- Hand off to the service ----
        await using var stream = file.OpenReadStream();

        var result = await _documents.CreateAsync(
            new DocumentCreationRequest(
                FileContent:       stream,
                OriginalFileName:  file.FileName,
                MimeType:          file.ContentType,
                DeclaredSizeBytes: file.Length,
                DepartmentId:      departmentId,
                UploaderId:        uploaderId),
            cancellationToken);

        return result switch
        {
            DocumentCreationResult.Created created
                => AcceptedAtAction(
                    nameof(GetById),
                    new { id = created.Document.Id },
                    ToDetail(created.Document)),

            DocumentCreationResult.FileEmpty
                => BadRequest(new { error = "File is empty." }),

            DocumentCreationResult.FileTooLarge tooLarge
                => StatusCode(StatusCodes.Status413PayloadTooLarge, new
                {
                    error = "File exceeds size limit.",
                    observedBytes = tooLarge.ObservedBytes,
                    maxBytes      = tooLarge.MaxBytes
                }),

            DocumentCreationResult.InvalidMimeType bad
                => BadRequest(new
                {
                    error    = "MIME type not allowed. Use .pdf, .docx, or .txt.",
                    received = bad.MimeType
                }),

            _ => StatusCode(StatusCodes.Status500InternalServerError),
        };
    }

    // ------------------------------------------------------------------
    //  GET /api/documents/{id}
    // ------------------------------------------------------------------
    [HttpGet("{id:guid}")]
    public async Task<ActionResult<DocumentDetail>> GetById(
        Guid id,
        CancellationToken cancellationToken)
    {
        if (!TryGetDepartmentId(out var departmentId))
            return Forbid();

        var doc = await _db.Documents
            .AsNoTracking()
            .FirstOrDefaultAsync(
                d => d.Id == id && d.DepartmentId == departmentId,
                cancellationToken);

        return doc is null ? NotFound() : Ok(ToDetail(doc));
    }

    // ==================================================================
    //  Helpers
    // ==================================================================

    private bool TryGetDepartmentId(out string departmentId)
    {
        departmentId = User.FindFirstValue(AppClaimTypes.DepartmentId) ?? string.Empty;
        return !string.IsNullOrWhiteSpace(departmentId);
    }

    private static DocumentDetail ToDetail(Document d) => new(
        d.Id,
        d.OriginalFileName,
        d.MimeType,
        d.SizeBytes,
        d.DepartmentId,
        d.Status.ToString(),
        d.ChunkCount,
        d.UploadedAt,
        d.ProcessedAt,
        d.ErrorMessage);
}

// =====================================================================
//  Response DTOs
// =====================================================================

public sealed record DocumentListItem(
    Guid      Id,
    string    OriginalFileName,
    string    MimeType,
    long      SizeBytes,
    string    Status,
    int       ChunkCount,
    DateTime  UploadedAt,
    DateTime? ProcessedAt,
    string?   ErrorMessage);

public sealed record DocumentDetail(
    Guid      Id,
    string    OriginalFileName,
    string    MimeType,
    long      SizeBytes,
    string    DepartmentId,
    string    Status,
    int       ChunkCount,
    DateTime  UploadedAt,
    DateTime? ProcessedAt,
    string?   ErrorMessage);
