using chatbot.Infrastructure.Audit;
using chatbot.Infrastructure.Storage;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace chatbot.Pages.Account;

/// <summary>
/// Lets a signed-in user upload / replace / remove their avatar image.
/// Blobs are stored via <see cref="IDocumentStorage"/> under the app's blob
/// root (outside wwwroot) and served back through the authenticated
/// <c>GET /api/account/avatar</c> endpoint — never as public static files.
/// The stored relative path is persisted on <see cref="ApplicationUser.AvatarPath"/>
/// and the sign-in is refreshed so the navbar's avatar claim updates immediately.
/// </summary>
[Authorize]
public sealed class AvatarModel : PageModel
{
    // Keep this list closed — only browser-safe raster formats. The stored
    // extension is derived from the content type, never from the upload name.
    private static readonly IReadOnlyDictionary<string, string> AllowedTypes =
        new Dictionary<string, string>
        {
            ["image/png"]  = ".png",
            ["image/jpeg"] = ".jpg",
            ["image/webp"] = ".webp",
            ["image/gif"]  = ".gif",
        };

    private const long MaxBytes = 2 * 1024 * 1024; // 2 MB

    private readonly UserManager<ApplicationUser> _userManager;
    private readonly SignInManager<ApplicationUser> _signInManager;
    private readonly IDocumentStorage _storage;
    private readonly IAuditLogger _audit;
    private readonly ILogger<AvatarModel> _logger;

    public AvatarModel(
        UserManager<ApplicationUser> userManager,
        SignInManager<ApplicationUser> signInManager,
        IDocumentStorage storage,
        IAuditLogger audit,
        ILogger<AvatarModel> logger)
    {
        _userManager   = userManager;
        _signInManager = signInManager;
        _storage       = storage;
        _audit         = audit;
        _logger        = logger;
    }

    [BindProperty]
    public IFormFile? Upload { get; set; }

    public string? CurrentAvatarPath { get; private set; }
    public string DisplayName { get; private set; } = "Người dùng";
    public bool Succeeded { get; private set; }

    public async Task<IActionResult> OnGetAsync()
    {
        var user = await _userManager.GetUserAsync(User);
        if (user is null)
            return RedirectToPage("/Account/Login");

        Hydrate(user);
        return Page();
    }

    public async Task<IActionResult> OnPostAsync(CancellationToken cancellationToken)
    {
        var user = await _userManager.GetUserAsync(User);
        if (user is null)
            return RedirectToPage("/Account/Login");

        Hydrate(user);

        if (Upload is null || Upload.Length == 0)
        {
            ModelState.AddModelError(nameof(Upload), "Vui lòng chọn một tệp ảnh.");
            return Page();
        }

        if (Upload.Length > MaxBytes)
        {
            ModelState.AddModelError(nameof(Upload), "Ảnh vượt quá dung lượng tối đa 2 MB.");
            return Page();
        }

        if (!AllowedTypes.TryGetValue(Upload.ContentType, out var ext))
        {
            ModelState.AddModelError(nameof(Upload),
                "Định dạng không hợp lệ. Chỉ chấp nhận PNG, JPG, WEBP hoặc GIF.");
            return Page();
        }

        // Persist the blob outside wwwroot via the shared storage backend.
        // The extension drives the stored name; the upload's own name is ignored.
        StoredFile stored;
        await using (var uploadStream = Upload.OpenReadStream())
        {
            stored = await _storage.SaveAsync(uploadStream, $"avatar{ext}", cancellationToken);
        }

        var oldPath = user.AvatarPath;

        user.AvatarPath = stored.RelativePath;
        var result = await _userManager.UpdateAsync(user);
        if (!result.Succeeded)
        {
            // Roll back the just-written blob so we don't leak orphans.
            await _storage.DeleteAsync(stored.RelativePath);
            foreach (var err in result.Errors)
                ModelState.AddModelError(string.Empty, err.Description);
            return Page();
        }

        // Best-effort cleanup of the previous avatar blob.
        if (!string.IsNullOrWhiteSpace(oldPath))
            await _storage.DeleteAsync(oldPath);

        // Re-issue the cookie so AppClaimTypes.AvatarPath reflects the new image.
        await _signInManager.RefreshSignInAsync(user);

        _logger.LogInformation("User {UserId} updated their avatar.", user.Id);
        _ = _audit.LogAsync(
            "auth.avatar_change", "auth",
            resourceType: nameof(ApplicationUser),
            resourceId:   user.Id);

        Succeeded = true;
        CurrentAvatarPath = user.AvatarPath;
        ModelState.Clear();
        return Page();
    }

    public async Task<IActionResult> OnPostRemoveAsync()
    {
        var user = await _userManager.GetUserAsync(User);
        if (user is null)
            return RedirectToPage("/Account/Login");

        if (!string.IsNullOrWhiteSpace(user.AvatarPath))
        {
            await _storage.DeleteAsync(user.AvatarPath);

            user.AvatarPath = null;
            await _userManager.UpdateAsync(user);
            await _signInManager.RefreshSignInAsync(user);

            _ = _audit.LogAsync(
                "auth.avatar_remove", "auth",
                resourceType: nameof(ApplicationUser),
                resourceId:   user.Id);
        }

        Hydrate(user);
        Succeeded = true;
        return Page();
    }

    private void Hydrate(ApplicationUser user)
    {
        CurrentAvatarPath = user.AvatarPath;
        DisplayName = string.IsNullOrWhiteSpace(user.FullName) ? "Người dùng" : user.FullName;
    }
}
