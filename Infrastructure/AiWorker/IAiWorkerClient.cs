using chatbot.Infrastructure.AiWorker.Contracts;

namespace chatbot.Infrastructure.AiWorker;

/// <summary>
/// Single-source-of-truth client for the Python FastAPI worker.
/// All HTTP calls to Python go through this — never raw <c>HttpClient</c>
/// at call sites. Keeps the wire contract centralised so a contract
/// change touches one file.
/// </summary>
public interface IAiWorkerClient
{
    /// <summary>
    /// Ship one document to <c>POST /api/ingest</c> as <c>multipart/form-data</c>.
    /// Caller streams <c>request.FileContent</c> and disposes it after this returns.
    /// </summary>
    /// <returns>
    /// The worker's response. Throws <see cref="AiWorkerException"/> on transport
    /// errors or non-JSON 5xx; returns <see cref="IngestResult"/> with
    /// <c>Status="failed"</c> on application-level 422 errors.
    /// </returns>
    Task<IngestResult> IngestAsync(
        IngestRequest request,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// Open a streaming RAG query against <c>POST /api/query</c>.
    /// Returns an async sequence of <see cref="QueryEvent"/>s parsed from
    /// the SSE response — caller consumes with <c>await foreach</c>.
    ///
    /// Throws <see cref="AiWorkerException"/> on connect/HTTP failure
    /// (before the stream opens). Errors that occur mid-stream surface
    /// as <see cref="QueryEvent.Error"/> when the Python side emits them,
    /// or propagate as exceptions if the TCP connection drops.
    /// </summary>
    IAsyncEnumerable<QueryEvent> QueryAsync(
        QueryRequest request,
        CancellationToken cancellationToken = default);
}

/// <summary>Thrown when the worker is unreachable or returns an unparseable response.</summary>
public sealed class AiWorkerException : Exception
{
    public AiWorkerException(string message) : base(message) { }
    public AiWorkerException(string message, Exception inner) : base(message, inner) { }
}
