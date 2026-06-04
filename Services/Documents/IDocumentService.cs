using chatbot.Models;

namespace chatbot.Services.Documents;

/// <summary>
/// Application service for the document lifecycle:
///   • <see cref="CreateAsync"/> — upload path: validate → save blob → insert Pending row.
///   • <see cref="IngestAsync"/> — worker path: atomic claim → call Python → flip Ready/Failed.
///
/// Both sides of the lifecycle live here so the validation, error mapping,
/// and status-transition rules sit in one place.
/// </summary>
public interface IDocumentService
{
    /// <summary>
    /// Persist a new upload. Does NOT call the Python worker —
    /// the row is left at <see cref="DocumentStatus.Pending"/> for the
    /// <see cref="Workers.DocumentIngestionWorker"/> to pick up.
    /// </summary>
    Task<DocumentCreationResult> CreateAsync(
        DocumentCreationRequest request,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// Run one document through the Python worker.
    /// Atomically claims the row (Pending → Processing) so concurrent
    /// background workers can't double-process it. Always leaves the row
    /// in a terminal state (Ready or Failed).
    /// </summary>
    Task IngestAsync(Document document, CancellationToken cancellationToken = default);
}
