namespace chatbot.Infrastructure.AiWorker.Contracts;

/// <summary>
/// Input to <c>POST /ingest</c>. Ships as <c>multipart/form-data</c>:
/// scalar fields become form parts, <see cref="FileContent"/> becomes the
/// binary <c>file</c> part.
///
/// The caller owns <see cref="FileContent"/> and is responsible for
/// disposing it — typically wrapped in a <c>using</c> via
/// <c>IDocumentStorage.OpenReadAsync</c>.
/// </summary>
public sealed record IngestRequest(
    Guid    DocumentId,
    string  DepartmentId,
    string  OriginalName,
    string  MimeType,
    Stream  FileContent);
