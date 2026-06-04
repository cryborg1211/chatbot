namespace chatbot.Infrastructure.Storage;

/// <summary>
/// Abstraction over the blob backend. Swap implementations
/// (local FS now → S3 / Azure Blob later) without touching callers.
/// </summary>
public interface IDocumentStorage
{
    /// <summary>
    /// Persist <paramref name="content"/> under a unique generated name
    /// and return the relative path. The caller stores this in
    /// <see cref="Models.Document.StoredFileName"/>.
    /// </summary>
    Task<StoredFile> SaveAsync(
        Stream content,
        string originalFileName,
        CancellationToken cancellationToken = default);

    /// <summary>Open a read stream for an already-stored file. Caller disposes.</summary>
    Task<Stream> OpenReadAsync(
        string storedFileName,
        CancellationToken cancellationToken = default);

    /// <summary>Best-effort delete. Missing file is not an error.</summary>
    Task DeleteAsync(string storedFileName);
}

/// <summary>Result of a successful <see cref="IDocumentStorage.SaveAsync"/>.</summary>
public sealed record StoredFile(string RelativePath, long SizeBytes);
