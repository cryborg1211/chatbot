using chatbot.Infrastructure.Audit;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace chatbot.Pages.Account;

/// <summary>
/// Lets a signed-in user upload / replace / remove their avatar image.
/// Files land in <c>wwwroot/uploads/avatars/</c> so they are served
/// directly by the static-file middleware (no streaming controller needed).
/// The new path is persisted on <see cref="ApplicationUser.AvatarPath"/> and
/// the sign-in is refreshed so the navbar's avatar claim updates immediately.
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
    private const string AvatarFolder = "uploads/avatars";

    private readonly UserManager<ApplicationUser> _userManager;
    private readonly SignInManager<ApplicationUser> _signInManager;
    private readonly IWebHostEnvironment _env;
    private readonly IAuditLogger _audit;
    private readonly ILogger<AvatarModel> _logger;

    public AvatarModel(
        UserManager<ApplicationUser> userManager,
        SignInManager<ApplicationUser> signInManager,
        IWebHostEnvironment env,
        IAuditLogger audit,
        ILogger<AvatarModel> logger)
    {
        _userManager   = userManager;
        _signInManager = signInManager;
        _env           = env;
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

    public async Task<IActionResult> OnPostAsync()
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

        // wwwroot may not exist on a fresh checkout; WebRootPath can be null.
        var webRoot = _env.WebRootPath
                      ?? Path.Combine(_env.ContentRootPath, "wwwroot");
        var targetDir = Path.Combine(webRoot, "uploads", "avatars");
        Directory.CreateDirectory(targetDir);

        var fileName = $"{Guid.NewGuid():N}{ext}";
        var absolutePath = Path.Combine(targetDir, fileName);

        await using (var stream = new FileStream(
            absolutePath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            await Upload.CopyToAsync(stream);
        }

        var oldPath = user.AvatarPath;

        user.AvatarPath = $"/{AvatarFolder}/{fileName}";
        var result = await _userManager.UpdateAsync(user);
        if (!result.Succeeded)
        {
            // Roll back the just-written file so we don't leak orphans.
            TryDelete(absolutePath);
            foreach (var err in result.Errors)
                ModelState.AddModelError(string.Empty, err.Description);
            return Page();
        }

        // Best-effort cleanup of the previous avatar file.
        DeleteWebRelative(webRoot, oldPath);

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
            var webRoot = _env.WebRootPath
                          ?? Path.Combine(_env.ContentRootPath, "wwwroot");
            DeleteWebRelative(webRoot, user.AvatarPath);

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

    private static void DeleteWebRelative(string webRoot, string? webPath)
    {
        if (string.IsNullOrWhiteSpace(webPath))
            return;

        // webPath is like "/uploads/avatars/abc.png" — strip the leading slash
        // and resolve under webRoot. Guard against escaping the avatar folder.
        var relative = webPath.TrimStart('/').Replace('/', Path.DirectorySeparatorChar);
        var combined = Path.GetFullPath(Path.Combine(webRoot, relative));
        var avatarRoot = Path.GetFullPath(Path.Combine(webRoot, "uploads", "avatars"))
                         + Path.DirectorySeparatorChar;

        if (combined.StartsWith(avatarRoot, StringComparison.OrdinalIgnoreCase))
            TryDelete(combined);
    }

    private static void TryDelete(string absolutePath)
    {
        try
        {
            if (System.IO.File.Exists(absolutePath))
                System.IO.File.Delete(absolutePath);
        }
        catch
        {
            // Best-effort — a leftover avatar file is harmless.
        }
    }
}
