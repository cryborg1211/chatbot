using chatbot.Infrastructure.AiWorker;
using chatbot.Infrastructure.Authorization;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace chatbot.Pages.Admin;

/// <summary>
/// Admin "AI settings" dashboard at <c>/admin/ai-settings</c>.
///
/// Phase 1 = live read-only status (active model + Ollama reachability +
/// installed models) from the worker's <c>GET /llm/status</c>. Provider keys,
/// model switching, and cloud routing are mocked in the view until later
/// phases. See <c>process/features/ai-settings/</c> for the program plan.
/// </summary>
[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]
public sealed class AiSettingsModel : PageModel
{
    private readonly IAiWorkerClient _worker;
    private readonly ILogger<AiSettingsModel> _logger;

    public AiSettingsModel(IAiWorkerClient worker, ILogger<AiSettingsModel> logger)
    {
        _worker = worker;
        _logger = logger;
    }

    /// <summary>False when the Python worker itself could not be reached.</summary>
    public bool WorkerReachable { get; private set; }

    /// <summary>True when the worker reports the local Ollama instance is up.</summary>
    public bool OllamaReachable { get; private set; }

    public string ActiveModel { get; private set; } = "—";
    public string BaseUrl { get; private set; } = "—";
    public IReadOnlyList<string> InstalledModels { get; private set; } = Array.Empty<string>();

    public async Task OnGetAsync(CancellationToken cancellationToken)
    {
        try
        {
            var status = await _worker.GetLlmStatusAsync(cancellationToken);
            WorkerReachable = true;
            OllamaReachable = status.OllamaReachable;
            ActiveModel = string.IsNullOrWhiteSpace(status.ActiveModel) ? "—" : status.ActiveModel;
            BaseUrl = status.BaseUrl;
            InstalledModels = status.InstalledModels;
        }
        catch (AiWorkerException ex)
        {
            // Worker down — render an offline state rather than 500 the page.
            _logger.LogWarning(ex, "ai_settings_worker_unreachable");
            WorkerReachable = false;
        }
    }
}
