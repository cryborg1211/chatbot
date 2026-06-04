using chatbot.Models;

namespace chatbot.Services.Documents;

/// <summary>
/// Discriminated result of <see cref="IDocumentService.CreateAsync"/>.
/// Lets the controller map outcomes to HTTP status codes without
/// resorting to exceptions for known business cases.
/// </summary>
public abstract record DocumentCreationResult
{
    /// <summary>Row written, file persisted, ready for the ingestion worker.</summary>
    public sealed record Created(Document Document) : DocumentCreationResult;

    /// <summary>Empty / zero-byte upload.</summary>
    public sealed record FileEmpty : DocumentCreationResult;

    /// <summary>Declared or stored size above <see cref="DocumentLimits.MaxFileSizeBytes"/>.</summary>
    public sealed record FileTooLarge(long ObservedBytes, long MaxBytes) : DocumentCreationResult;

    /// <summary>MIME type outside the allowlist (pdf / docx / txt).</summary>
    public sealed record InvalidMimeType(string MimeType) : DocumentCreationResult;
}
