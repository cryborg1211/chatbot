namespace chatbot.Infrastructure.AiWorker;

/// <summary>Strongly-typed binding for the <c>AiWorker</c> config section.</summary>
public sealed class AiWorkerOptions
{
    public const string SectionName = "AiWorker";

    /// <summary>Base URL of the Python FastAPI worker (no trailing slash).</summary>
    public string BaseUrl { get; set; } = "http://localhost:8000";

    /// <summary>Shared secret sent as <c>X-Worker-Api-Key</c>.</summary>
    public string ApiKey { get; set; } = string.Empty;

    /// <summary>HTTP request timeout. Embedding 20 MB PDFs is slow — give it room.</summary>
    public int TimeoutSeconds { get; set; } = 120;
}
