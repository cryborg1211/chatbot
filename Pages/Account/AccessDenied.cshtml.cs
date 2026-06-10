using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace chatbot.Pages.Account;

/// <summary>
/// Shown when an authenticated user tries to access a resource they lack
/// permission for (e.g. non-admin hitting an admin-only page). Configured
/// in <c>Program.cs</c> via <c>options.AccessDeniedPath</c>.
/// </summary>
[AllowAnonymous]
public sealed class AccessDeniedModel : PageModel
{
    public void OnGet() { }
}
