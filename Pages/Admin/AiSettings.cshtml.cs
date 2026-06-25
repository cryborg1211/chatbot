using chatbot.Infrastructure.AiWorker;
using chatbot.Infrastructure.Audit;
using chatbot.Infrastructure.Authorization;
using chatbot.Infrastructure.Identity;
using chatbot.Services.Ai;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace chatbot.Pages.Admin;

/// <summary>
/// Admin "AI settings" dashboard at <c>/admin/ai-settings</c>.
///
/// The initial GET is fast — it loads only the persisted <see cref="Models.AiConfig"/>
/// and renders the shell. Live worker/Ollama status (a network round-trip) is
/// fetched asynchronously by the page JS via the <c>Status</c> handler, so a slow
/// or offline worker never blocks the page. Phase 4: encrypted provider keys,
/// per-provider connectivity tests, and cloud/local routing.
/// </summary>
[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]
public sealed class AiSettingsModel : PageModel
{
    private readonly IAiWorkerClient _worker;
    private readonly IAiConfigService _aiConfig;
    private readonly IProviderKeyService _keys;
    private readonly IAuditLogger _audit;
    private readonly ILogger<AiSettingsModel> _logger;

    public AiSettingsModel(
        IAiWorkerClient worker,
        IAiConfigService aiConfig,
        IProviderKeyService keys,
        IAuditLogger audit,
        ILogger<AiSettingsModel> logger)
    {
        _worker   = worker;
        _aiConfig = aiConfig;
        _keys     = keys;
        _audit    = audit;
        _logger   = logger;
    }

    // ---- Initial render (fast — config only) ----
    public string ActiveProvider { get; private set; } = "ollama";
    public string? ActiveModel { get; private set; }
    public DateTime? UpdatedAt { get; private set; }
    public string? UpdatedBy { get; private set; }
    public bool Saved { get; private set; }

    // ---- Save form bindings ----
    [BindProperty] public string? SelectedProvider { get; set; }
    [BindProperty] public string? SelectedModel { get; set; }

    public async Task OnGetAsync(CancellationToken cancellationToken)
    {
        Saved = TempData["AiSaved"] is true;
        var c = await _aiConfig.GetAsync(cancellationToken);
        ActiveProvider = c.ActiveProvider;
        ActiveModel    = c.ActiveModel;
        UpdatedAt      = c.UpdatedAt;
        UpdatedBy      = c.UpdatedBy;
    }

    // GET ?handler=Status — live worker/Ollama status + key flags, for the page JS.
    public async Task<IActionResult> OnGetStatusAsync(CancellationToken cancellationToken)
    {
        bool workerOk = false, ollamaOk = false;
        string baseUrl = "";
        string[] models = Array.Empty<string>();
        string? workerModel = null;
        try
        {
            var s = await _worker.GetLlmStatusAsync(cancellationToken);
            workerOk    = true;
            ollamaOk    = s.OllamaReachable;
            baseUrl     = s.BaseUrl;
            models      = s.InstalledModels.ToArray();
            workerModel = s.ActiveModel;
        }
        catch (AiWorkerException)
        {
            workerOk = false;
        }

        var configured = await _keys.GetConfiguredAsync(cancellationToken);
        var cfg = await _aiConfig.GetAsync(cancellationToken);
        var effectiveModel = !string.IsNullOrWhiteSpace(cfg.ActiveModel) ? cfg.ActiveModel : workerModel;

        return new JsonResult(new
        {
            workerReachable = workerOk,
            ollamaReachable = ollamaOk,
            baseUrl,
            installedModels = models,
            activeProvider  = cfg.ActiveProvider,
            activeModel     = effectiveModel,
            configured      = configured.ToArray(),
        });
    }

    // POST ?handler=SaveKey — encrypt + store a provider key (AJAX). Never echoes it.
    public async Task<IActionResult> OnPostSaveKeyAsync(
        [FromForm] string provider, [FromForm] string key, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(provider) || string.IsNullOrWhiteSpace(key))
            return new JsonResult(new { ok = false, error = "Thiếu nhà cung cấp hoặc khóa." });

        await _keys.SaveAsync(provider, key.Trim(), cancellationToken);
        _ = _audit.LogAsync("ai.key_change", "sys",
            resourceType: nameof(Models.AiProviderKey),
            resourceId:   provider.Trim().ToLowerInvariant(),
            details: new { provider = provider.Trim().ToLowerInvariant() });

        return new JsonResult(new { ok = true });
    }

    // POST ?handler=Models — live, text-only model list for a provider (AJAX).
    public async Task<IActionResult> OnPostModelsAsync(
        [FromForm] string provider, CancellationToken cancellationToken)
    {
        provider = (provider ?? "ollama").Trim().ToLowerInvariant();

        string? key = null;
        if (provider != "ollama")
        {
            key = await _keys.GetPlaintextAsync(provider, cancellationToken);
            if (key is null)
                return new JsonResult(new { ok = false, error = "Chưa có khóa." });
        }

        var res = await _worker.GetProviderModelsAsync(provider, key, cancellationToken);
        return new JsonResult(new { ok = res.Ok, models = res.Models, error = res.Error });
    }

    // POST (default) — save the active provider + model.
    public async Task<IActionResult> OnPostAsync(CancellationToken cancellationToken)
    {
        var provider = string.IsNullOrWhiteSpace(SelectedProvider) ? "ollama" : SelectedProvider.Trim().ToLowerInvariant();
        var model = SelectedModel == "—" || string.IsNullOrWhiteSpace(SelectedModel) ? null : SelectedModel;
        var updatedBy = User.FindFirst(AppClaimTypes.FullName)?.Value ?? User.Identity?.Name;

        await _aiConfig.UpdateAsync(provider, model, updatedBy, cancellationToken);
        _ = _audit.LogAsync("ai.provider_switch", "sys", details: new { provider, model });

        TempData["AiSaved"] = true;
        return RedirectToPage();
    }
}
