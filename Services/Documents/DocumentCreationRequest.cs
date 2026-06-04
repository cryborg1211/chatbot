namespace chatbot.Services.Documents;

/// <summary>
/// Stripped-down DTO the controller hands to <see cref="IDocumentService.CreateAsync"/>.
/// Deliberately decoupled from <see cref="IFormFile"/> so the service stays
/// free of HTTP-layer dependencies.
/// </summary>
public sealed record DocumentCreationRequest(
    Stream FileContent,
    string OriginalFileName,
    string MimeType,
    long   DeclaredSizeBytes,
    string DepartmentId,
    string UploaderId);
