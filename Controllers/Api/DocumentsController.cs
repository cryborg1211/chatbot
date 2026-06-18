using System.Security.Claims;
using chatbot.Data;
using chatbot.Infrastructure.AiWorker;
using chatbot.Infrastructure.Audit;
using chatbot.Infrastructure.Authorization;
using chatbot.Infrastructure.Identity;
using chatbot.Infrastructure.Storage;
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
    private readonly IAuditLogger _audit;
    private readonly IDocumentStorage _storage;
    private readonly IAiWorkerClient _worker;
    private readonly ILogger<DocumentsController> _logger;

    public DocumentsController(
        ApplicationDbContext db,
        IDocumentService documents,
        IAuditLogger audit,
        IDocumentStorage storage,
        IAiWorkerClient worker,
        ILogger<DocumentsController> logger)
    {
        _db        = db;
        _documents = documents;
        _audit     = audit;
        _storage   = storage;
        _worker    = worker;
        _logger    = logger;
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
    //  POST /api/documents   (multipart/form-data: files[])
    //  Non-blocking: validate per file, save blob, insert Pending row,
    //  return 202 with a per-file result list. The DocumentIngestionWorker
    //  picks each Pending row up from there.
    // ------------------------------------------------------------------
    private const int MaxFilesPerBatch = 10;

    [HttpPost]
    [RequestSizeLimit(
        (DocumentLimits.MaxFileSizeBytes * MaxFilesPerBatch) + DocumentLimits.MultipartOverheadBytes)]
    [RequestFormLimits(
        MultipartBodyLengthLimit =
            (DocumentLimits.MaxFileSizeBytes * MaxFilesPerBatch) + DocumentLimits.MultipartOverheadBytes,
        ValueCountLimit = MaxFilesPerBatch * 4)]
    public async Task<IActionResult> Upload(
        IFormFileCollection files,
        CancellationToken cancellationToken)
    {
        // ---- Controller-layer guards ----
        if (files is null || files.Count == 0)
            return BadRequest(new { error = "No files uploaded." });

        if (files.Count > MaxFilesPerBatch)
            return BadRequest(new
            {
                error    = $"Too many files in one request. Max {MaxFilesPerBatch} per batch.",
                received = files.Count,
            });

        if (!TryGetDepartmentId(out var departmentId))
            return Forbid();

        var uploaderId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (string.IsNullOrWhiteSpace(uploaderId))
            return Forbid();

        // ---- Process each file independently — one bad file doesn't kill the batch ----
        var items = new List<UploadResultItem>(files.Count);
        foreach (var file in files)
        {
            if (file is null || file.Length == 0)
            {
                items.Add(new UploadResultItem(
                    FileName: file?.FileName ?? "(unknown)",
                    Success:  false,
                    Error:    "File is empty.",
                    DocumentId: null,
                    SizeBytes:  null));
                continue;
            }

            try
            {
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

                items.Add(ToResultItem(file, result));
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "doc_upload_failed file={FileName}", file.FileName);
                items.Add(new UploadResultItem(
                    FileName: file.FileName,
                    Success:  false,
                    Error:    "Internal error during upload.",
                    DocumentId: null,
                    SizeBytes:  null));
            }
        }

        // ---- Aggregate response ----
        var accepted = items.Count(i => i.Success);
        var failed   = items.Count - accepted;

        return StatusCode(StatusCodes.Status202Accepted, new UploadBatchResult(
            TotalAccepted: accepted,
            TotalFailed:   failed,
            Items:         items));
    }

    private UploadResultItem ToResultItem(IFormFile file, DocumentCreationResult result)
    {
        switch (result)
        {
            case DocumentCreationResult.Created created:
                _ = _audit.LogAsync(
                    "doc.upload", "doc",
                    resourceType: nameof(Document),
                    resourceId:   created.Document.Id.ToString(),
                    details: new
                    {
                        fileName  = created.Document.OriginalFileName,
                        sizeBytes = created.Document.SizeBytes,
                        mimeType  = created.Document.MimeType,
                    });
                return new UploadResultItem(
                    FileName:   file.FileName,
                    Success:    true,
                    DocumentId: created.Document.Id,
                    SizeBytes:  created.Document.SizeBytes,
                    Error:      null);

            case DocumentCreationResult.FileEmpty:
                return new UploadResultItem(file.FileName, false, null, null, "File is empty.");

            case DocumentCreationResult.FileTooLarge tooLarge:
                return new UploadResultItem(
                    file.FileName, false, null, tooLarge.ObservedBytes,
                    $"File exceeds {tooLarge.MaxBytes / (1024 * 1024)} MB limit.");

            case DocumentCreationResult.InvalidMimeType bad:
                return new UploadResultItem(
                    file.FileName, false, null, null,
                    $"MIME type '{bad.MimeType}' not allowed. Use .pdf, .docx, .doc, or .txt.");

            default:
                return new UploadResultItem(file.FileName, false, null, null, "Unknown failure.");
        }
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

    // ------------------------------------------------------------------
    //  GET /api/documents/{id}/download
    //  Streams the original stored blob back as an attachment. Admins (e.g.
    //  opening a document from the cross-tenant log viewer) may fetch any
    //  document; everyone else is tenant-scoped.
    // ------------------------------------------------------------------
    [HttpGet("{id:guid}/download")]
    public async Task<IActionResult> Download(
        Guid id,
        CancellationToken cancellationToken)
    {
        var query = _db.Documents.AsNoTracking().Where(d => d.Id == id);

        if (!User.IsInRole(Roles.Admin))
        {
            if (!TryGetDepartmentId(out var departmentId))
                return Forbid();
            query = query.Where(d => d.DepartmentId == departmentId);
        }

        var doc = await query.FirstOrDefaultAsync(cancellationToken);
        if (doc is null) return NotFound();

        Stream stream;
        try
        {
            stream = await _storage.OpenReadAsync(doc.StoredFileName, cancellationToken);
        }
        catch (Exception ex) when (ex is FileNotFoundException or DirectoryNotFoundException)
        {
            _logger.LogWarning(ex,
                "doc_download_blob_missing doc={DocId} path={Path}",
                doc.Id, doc.StoredFileName);
            return NotFound();
        }

        _ = _audit.LogAsync(
            "doc.download", "doc",
            resourceType: nameof(Document),
            resourceId:   doc.Id.ToString(),
            details: new { fileName = doc.OriginalFileName });

        // fileDownloadName → Content-Disposition: attachment; serves the
        // user-facing original name instead of the on-disk GUID.
        return File(stream, doc.MimeType, doc.OriginalFileName);
    }

    // ------------------------------------------------------------------
    //  DELETE /api/documents/{id}
    //  Tenant-scoped delete. Order:
    //    1. Verify row belongs to caller's department (else 404 / 403).
    //    2. Delete Qdrant chunks via Python worker.
    //    3. Best-effort delete the on-disk blob.
    //    4. Delete the SQL row.
    //    5. Audit log.
    //  Vector cleanup is intentionally fail-fast: if worker/Qdrant delete
    //  fails, keep the SQL row so the document can be retried/deleted later.
    // ------------------------------------------------------------------
    [HttpDelete("{id:guid}")]
    public async Task<IActionResult> Delete(
        Guid id,
        CancellationToken cancellationToken)
    {
        if (!TryGetDepartmentId(out var departmentId))
            return Forbid();

        var doc = await _db.Documents
            .FirstOrDefaultAsync(
                d => d.Id == id && d.DepartmentId == departmentId,
                cancellationToken);

        if (doc is null) return NotFound();

        // 1) Qdrant chunks — must succeed before SQL delete.
        try
        {
            await _worker.DeleteDocumentAsync(doc.Id, cancellationToken);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex,
                "doc_delete_worker_failed doc={DocId} — SQL row kept so delete can be retried",
                doc.Id);

            return StatusCode(StatusCodes.Status502BadGateway, new
            {
                error      = "Vector cleanup failed. Document was not deleted.",
                documentId = doc.Id,
            });
        }

        // 2) Disk blob — best-effort after vectors are gone.
        try
        {
            await _storage.DeleteAsync(doc.StoredFileName);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex,
                "doc_delete_blob_failed doc={DocId} path={Path}",
                doc.Id, doc.StoredFileName);
        }

        // 3) SQL row — final authoritative delete.
        _db.Documents.Remove(doc);
        await _db.SaveChangesAsync(cancellationToken);

        _ = _audit.LogAsync(
            "doc.delete", "doc", LogSeverity.Warn,
            resourceType: nameof(Document),
            resourceId:   doc.Id.ToString(),
            details: new { fileName = doc.OriginalFileName });

        return NoContent();
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

public sealed record UploadResultItem(
    string  FileName,
    bool    Success,
    Guid?   DocumentId,
    long?   SizeBytes,
    string? Error);

public sealed record UploadBatchResult(
    int TotalAccepted,
    int TotalFailed,
    IReadOnlyList<UploadResultItem> Items);
