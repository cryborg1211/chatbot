using chatbot.Data;
using chatbot.Infrastructure.AiWorker;
using chatbot.Infrastructure.AiWorker.Contracts;
using chatbot.Infrastructure.Storage;
using chatbot.Models;
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
        "text/plain",
    };

    private readonly ApplicationDbContext _db;
    private readonly IDocumentStorage _storage;
    private readonly IAiWorkerClient _worker;
    private readonly ILogger<DocumentService> _logger;

    public DocumentService(
        ApplicationDbContext db,
        IDocumentStorage storage,
        IAiWorkerClient worker,
        ILogger<DocumentService> logger)
    {
        _db      = db;
        _storage = storage;
        _worker  = worker;
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

        if (!AllowedMimeTypes.Contains(request.MimeType))
            return new DocumentCreationResult.InvalidMimeType(request.MimeType);

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
                await MarkReadyAsync(document.Id, result.ChunkCount, cancellationToken);
            else
                await MarkFailedAsync(
                    document.Id,
                    $"{result.ErrorCode}: {result.Message}",
                    cancellationToken);
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

    private Task<int> MarkReadyAsync(Guid id, int chunkCount, CancellationToken ct) =>
        _db.Documents
            .Where(d => d.Id == id)
            .ExecuteUpdateAsync(s => s
                .SetProperty(d => d.Status,      DocumentStatus.Ready)
                .SetProperty(d => d.ChunkCount,  chunkCount)
                .SetProperty(d => d.ProcessedAt, DateTime.UtcNow)
                .SetProperty(d => d.ErrorMessage, (string?)null),
                ct);

    private Task<int> MarkFailedAsync(Guid id, string message, CancellationToken ct)
    {
        var truncated = message.Length > 1000 ? message[..1000] : message;
        return _db.Documents
            .Where(d => d.Id == id)
            .ExecuteUpdateAsync(s => s
                .SetProperty(d => d.Status,       DocumentStatus.Failed)
                .SetProperty(d => d.ErrorMessage, truncated)
                .SetProperty(d => d.ProcessedAt,  DateTime.UtcNow),
                ct);
    }
}
