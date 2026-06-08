using chatbot.Data;
using chatbot.Hubs;
using chatbot.Infrastructure.AiWorker;
using chatbot.Infrastructure.AiWorker.Contracts;
using chatbot.Infrastructure.Audit;
using chatbot.Infrastructure.Storage;
using chatbot.Models;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace chatbot.Services.Documents;

/// <inheritdoc/>
public sealed class DocumentService : IDocumentService
{
    private static readonly HashSet<string> AllowedMimeTypes = new(StringComparer.OrdinalIgnoreCase)
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", // .docx
        "application/msword",                                                       // .doc (legacy)
        "text/plain",
    };

    /// <summary>
    /// Filename-extension fallback. Browsers / OS shells frequently lie about
    /// the MIME type of legacy Office files (Edge often sends
    /// <c>application/octet-stream</c> for <c>.doc</c>, some Office installs
    /// register no MIME at all → empty string). When the MIME type fails the
    /// strict check, we still accept the upload if the filename ends with one
    /// of these extensions — the loader on the Python side validates the
    /// actual byte signature, so this is a safe widening.
    /// </summary>
    private static readonly HashSet<string> AllowedExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".pdf",
        ".docx",
        ".doc",
        ".txt",
    };

    // One-shot diagnostic — fires the first time the type is touched.
    static DocumentService()
    {
        Console.WriteLine(
            "[startup] mime_allowlist_loaded count={0} mimes=[{1}] exts=[{2}]",
            AllowedMimeTypes.Count,
            string.Join(", ", AllowedMimeTypes),
            string.Join(", ", AllowedExtensions));
    }

    /// <summary>
    /// True when EITHER the MIME type is on the allowlist, OR the file's
    /// extension is on the extension allowlist. The OR is deliberate —
    /// browsers can't reliably sniff legacy Office formats.
    /// </summary>
    private static bool IsAcceptedFile(string mimeType, string originalFileName)
    {
        if (!string.IsNullOrWhiteSpace(mimeType) && AllowedMimeTypes.Contains(mimeType))
            return true;

        var ext = Path.GetExtension(originalFileName);   // includes the leading '.'
        return !string.IsNullOrEmpty(ext) && AllowedExtensions.Contains(ext);
    }

    /// <summary>
    /// Best-effort canonical MIME type for the given filename extension.
    /// Returned only when the browser sent something useless
    /// (empty / <c>application/octet-stream</c>).
    /// </summary>
    private static string? MimeFromExtension(string originalFileName) =>
        Path.GetExtension(originalFileName).ToLowerInvariant() switch
        {
            ".pdf"  => "application/pdf",
            ".docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc"  => "application/msword",
            ".txt"  => "text/plain",
            _        => null,
        };

    private readonly ApplicationDbContext _db;
    private readonly IDocumentStorage _storage;
    private readonly IAiWorkerClient _worker;
    private readonly IAuditLogger _audit;
    private readonly IHubContext<DocumentHub> _hub;
    private readonly ILogger<DocumentService> _logger;

    public DocumentService(
        ApplicationDbContext db,
        IDocumentStorage storage,
        IAiWorkerClient worker,
        IAuditLogger audit,
        IHubContext<DocumentHub> hub,
        ILogger<DocumentService> logger)
    {
        _db      = db;
        _storage = storage;
        _worker  = worker;
        _audit   = audit;
        _hub     = hub;
        _logger  = logger;
    }

    // ==================================================================
    //  Upload path
    // ==================================================================

    public async Task<DocumentCreationResult> CreateAsync(
        DocumentCreationRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);

        // ---- 1. Cheap validations first ----
        if (request.DeclaredSizeBytes <= 0)
            return new DocumentCreationResult.FileEmpty();

        if (request.DeclaredSizeBytes > DocumentLimits.MaxFileSizeBytes)
            return new DocumentCreationResult.FileTooLarge(
                request.DeclaredSizeBytes,
                DocumentLimits.MaxFileSizeBytes);

        if (!IsAcceptedFile(request.MimeType, request.OriginalFileName))
        {
            _logger.LogWarning(
                "mime_rejected mime='{Mime}' file='{File}' mime_allowlist=[{Mimes}] ext_allowlist=[{Exts}]",
                request.MimeType,
                request.OriginalFileName,
                string.Join(", ", AllowedMimeTypes),
                string.Join(", ", AllowedExtensions));
            return new DocumentCreationResult.InvalidMimeType(request.MimeType);
        }

        // ---- Normalise empty / generic MIMEs based on the file extension ----
        // The Python worker also has a fallback, but normalising here means
        // the downstream `IngestRequest.MimeType` carries the *real* type.
        if (string.IsNullOrWhiteSpace(request.MimeType)
            || string.Equals(request.MimeType, "application/octet-stream",
                             StringComparison.OrdinalIgnoreCase))
        {
            var fixedMime = MimeFromExtension(request.OriginalFileName);
            if (fixedMime is not null)
            {
                _logger.LogInformation(
                    "mime_normalised file='{File}' from='{From}' to='{To}'",
                    request.OriginalFileName, request.MimeType, fixedMime);
                request = request with { MimeType = fixedMime };
            }
        }

        // ---- 2. Save blob to disk ----
        var stored = await _storage.SaveAsync(
            request.FileContent,
            request.OriginalFileName,
            cancellationToken);

        // Safety net: client lied about size, or chunked transfer revealed more bytes
        if (stored.SizeBytes > DocumentLimits.MaxFileSizeBytes)
        {
            await _storage.DeleteAsync(stored.RelativePath);
            return new DocumentCreationResult.FileTooLarge(
                stored.SizeBytes,
                DocumentLimits.MaxFileSizeBytes);
        }

        // ---- 3. Insert Pending row ----
        var document = new Document
        {
            Id               = Guid.NewGuid(),
            OriginalFileName = request.OriginalFileName,
            StoredFileName   = stored.RelativePath,
            MimeType         = request.MimeType,
            SizeBytes        = stored.SizeBytes,
            DepartmentId     = request.DepartmentId,
            UploaderId       = request.UploaderId,
            Status           = DocumentStatus.Pending,
            UploadedAt       = DateTime.UtcNow,
        };

        _db.Documents.Add(document);
        await _db.SaveChangesAsync(cancellationToken);

        _logger.LogInformation(
            "Document {DocumentId} accepted for tenant {DepartmentId} ({Size} bytes, mime={Mime}).",
            document.Id, document.DepartmentId, document.SizeBytes, document.MimeType);

        return new DocumentCreationResult.Created(document);
    }

    // ==================================================================
    //  Worker path
    // ==================================================================

    public async Task IngestAsync(Document document, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(document);

        // ---- 1. Atomic claim: Pending → Processing.
        //         If another worker already claimed it, rowsAffected == 0 and we bail.
        var claimed = await _db.Documents
            .Where(d => d.Id == document.Id && d.Status == DocumentStatus.Pending)
            .ExecuteUpdateAsync(s => s
                .SetProperty(d => d.Status, DocumentStatus.Processing),
                cancellationToken);

        if (claimed == 0)
        {
            _logger.LogDebug("Document {DocumentId} already claimed by another worker; skipping.",
                document.Id);
            return;
        }

        _logger.LogInformation("Ingest start for {DocumentId} (dept={Dept}).",
            document.Id, document.DepartmentId);

        // ---- 2. Open blob + call Python.
        try
        {
            await using var blobStream = await _storage.OpenReadAsync(
                document.StoredFileName, cancellationToken);

            var result = await _worker.IngestAsync(new IngestRequest(
                DocumentId:   document.Id,
                DepartmentId: document.DepartmentId,
                OriginalName: document.OriginalFileName,
                MimeType:     document.MimeType,
                FileContent:  blobStream
            ), cancellationToken);

            if (result.IsSuccess)
            {
                await MarkReadyAsync(document.Id, result.ChunkCount, cancellationToken);
                _ = _audit.LogAsync(
                    "doc.ingest_success", "doc",
                    resourceType: nameof(Document),
                    resourceId:   document.Id.ToString(),
                    overrideUserId:       document.UploaderId,
                    overrideDepartmentId: document.DepartmentId,
                    details: new { chunkCount = result.ChunkCount, elapsedMs = result.ElapsedMs });
            }
            else
            {
                await MarkFailedAsync(
                    document.Id,
                    $"{result.ErrorCode}: {result.Message}",
                    cancellationToken);
                _ = _audit.LogAsync(
                    "doc.ingest_failed", "doc", LogSeverity.Error,
                    resourceType: nameof(Document),
                    resourceId:   document.Id.ToString(),
                    overrideUserId:       document.UploaderId,
                    overrideDepartmentId: document.DepartmentId,
                    details: new { errorCode = result.ErrorCode, message = result.Message },
                    success: false);
            }
        }
        catch (AiWorkerException ex)
        {
            _logger.LogError(ex, "Worker call failed for {DocumentId}.", document.Id);
            await MarkFailedAsync(document.Id, ex.Message, CancellationToken.None);
        }
        catch (FileNotFoundException ex)
        {
            _logger.LogError(ex, "Blob missing for {DocumentId} at {Path}.",
                document.Id, document.StoredFileName);
            await MarkFailedAsync(document.Id, $"BLOB_MISSING: {ex.Message}", CancellationToken.None);
        }
        catch (Exception ex) when (!cancellationToken.IsCancellationRequested)
        {
            _logger.LogError(ex, "Unexpected ingest failure for {DocumentId}.", document.Id);
            await MarkFailedAsync(document.Id, $"INTERNAL_ERROR: {ex.Message}", CancellationToken.None);
        }
    }

    // ------------------------------------------------------------------
    //  Status-transition helpers (use ExecuteUpdate to avoid change tracking)
    // ------------------------------------------------------------------

    private async Task<int> MarkReadyAsync(Guid id, int chunkCount, CancellationToken ct)
    {
        var n = await _db.Documents
            .Where(d => d.Id == id)
            .ExecuteUpdateAsync(s => s
                .SetProperty(d => d.Status,      DocumentStatus.Ready)
                .SetProperty(d => d.ChunkCount,  chunkCount)
                .SetProperty(d => d.ProcessedAt, DateTime.UtcNow)
                .SetProperty(d => d.ErrorMessage, (string?)null),
                ct);
        await BroadcastAsync(id, DocumentStatus.Ready);
        return n;
    }

    private async Task<int> MarkFailedAsync(Guid id, string message, CancellationToken ct)
    {
        var truncated = message.Length > 1000 ? message[..1000] : message;
        var n = await _db.Documents
            .Where(d => d.Id == id)
            .ExecuteUpdateAsync(s => s
                .SetProperty(d => d.Status,       DocumentStatus.Failed)
                .SetProperty(d => d.ErrorMessage, truncated)
                .SetProperty(d => d.ProcessedAt,  DateTime.UtcNow),
                ct);
        await BroadcastAsync(id, DocumentStatus.Failed);
        return n;
    }

    private async Task BroadcastAsync(Guid documentId, DocumentStatus newStatus)
    {
        try
        {
            await _hub.Clients.All.SendAsync(
                "DocumentUpdated",
                documentId.ToString(),
                newStatus.ToString());
        }
        catch (Exception ex)
        {
            // SignalR push is best-effort — never block the worker.
            _logger.LogWarning(ex, "signalr_broadcast_failed doc={DocId}", documentId);
        }
    }
}
