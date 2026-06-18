using chatbot.Infrastructure.Authorization;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace chatbot.Pages.Admin;

/// <summary>
/// Admin "AI settings" dashboard at <c>/admin/ai-settings</c>.
///
/// Phase 1 = static shell with placeholder data (this file). Later phases
/// wire live worker/Ollama status, runtime model switching, and encrypted
/// provider-key management. See <c>process/features/ai-settings/</c> for the
/// program plan.
/// </summary>
[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]
public sealed class AiSettingsModel : PageModel
{
    public void OnGet()
    {
    }
}
