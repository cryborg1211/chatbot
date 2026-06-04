using chatbot.Data;
using chatbot.Models;
using chatbot.Services.Documents;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace chatbot.Workers;

/// <summary>
/// Background poller for the document ingestion queue.
/// Every <see cref="PollInterval"/> it fetches up to <see cref="BatchSize"/>
/// <see cref="DocumentStatus.Pending"/> rows (oldest first) and asks
/// <see cref="IDocumentService.IngestAsync"/> to drive each one to a terminal
/// state.  The service performs the atomic claim, so running multiple
/// replicas of this worker is safe (no row will be double-processed).
///
/// Design notes
/// ------------
/// • Uses <see cref="IServiceScopeFactory"/> because <see cref="ApplicationDbContext"/>
///   and <see cref="IDocumentService"/> are scoped.
/// • Each tick gets its own scope (and therefore its own DbContext) — keeps
///   change tracking small and survives cancellation cleanly.
/// • Tick errors are caught + logged so one bad batch doesn't kill the worker.
/// </summary>
public sealed class DocumentIngestionWorker : BackgroundService
{
    private static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(5);
    private const int BatchSize = 5;

    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<DocumentIngestionWorker> _logger;

    public DocumentIngestionWorker(
        IServiceScopeFactory scopeFactory,
        ILogger<DocumentIngestionWorker> logger)
    {
        _scopeFactory = scopeFactory;
        _logger       = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _logger.LogInformation(
            "DocumentIngestionWorker started. Poll={Interval}s, Batch={Batch}.",
            PollInterval.TotalSeconds, BatchSize);

        using var timer = new PeriodicTimer(PollInterval);

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                if (!await timer.WaitForNextTickAsync(stoppingToken))
                    break;

                await ProcessBatchAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                // graceful shutdown
                break;
            }
            catch (Exception ex)
            {
                // Never let an exception escape ExecuteAsync — that kills the worker permanently.
                _logger.LogError(ex, "Document ingestion tick failed; will retry next interval.");
            }
        }

        _logger.LogInformation("DocumentIngestionWorker stopped.");
    }

    private async Task ProcessBatchAsync(CancellationToken cancellationToken)
    {
        await using var scope = _scopeFactory.CreateAsyncScope();
        var db          = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        var documents   = scope.ServiceProvider.GetRequiredService<IDocumentService>();

        // Fetch as no-tracking — IngestAsync uses ExecuteUpdate so we don't need tracking.
        var batch = await db.Documents
            .AsNoTracking()
            .Where(d => d.Status == DocumentStatus.Pending)
            .OrderBy(d => d.UploadedAt)
            .Take(BatchSize)
            .ToListAsync(cancellationToken);

        if (batch.Count == 0)
            return;

        _logger.LogInformation("Picked {Count} pending document(s).", batch.Count);

        // Process serially. Embedding is CPU-/GPU-bound on the Python side
        // and shipping 5 PDFs in parallel from one worker would only saturate
        // the same downstream — no benefit, harder to reason about.
        foreach (var doc in batch)
        {
            if (cancellationToken.IsCancellationRequested) break;
            await documents.IngestAsync(doc, cancellationToken);
        }
    }
}
