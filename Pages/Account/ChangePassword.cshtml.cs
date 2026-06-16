using System.ComponentModel.DataAnnotations;
using chatbot.Infrastructure.Audit;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace chatbot.Pages.Account;

/// <summary>
/// Lets a signed-in user change their own password. Uses Identity's
/// <see cref="UserManager{TUser}.ChangePasswordAsync"/> (verifies the
/// current password) and then refreshes the sign-in so the security
/// stamp / cookie stays valid.
/// </summary>
[Authorize]
public sealed class ChangePasswordModel : PageModel
{
    private readonly UserManager<ApplicationUser> _userManager;
    private readonly SignInManager<ApplicationUser> _signInManager;
    private readonly IAuditLogger _audit;
    private readonly ILogger<ChangePasswordModel> _logger;

    public ChangePasswordModel(
        UserManager<ApplicationUser> userManager,
        SignInManager<ApplicationUser> signInManager,
        IAuditLogger audit,
        ILogger<ChangePasswordModel> logger)
    {
        _userManager   = userManager;
        _signInManager = signInManager;
        _audit         = audit;
        _logger        = logger;
    }

    [BindProperty]
    public InputModel Input { get; set; } = new();

    /// <summary>Set after a successful change so the view can show a banner.</summary>
    public bool Succeeded { get; private set; }

    public sealed class InputModel
    {
        [Required(ErrorMessage = "Vui lòng nhập mật khẩu hiện tại.")]
        [DataType(DataType.Password)]
        [Display(Name = "Mật khẩu hiện tại")]
        public string CurrentPassword { get; set; } = string.Empty;

        [Required(ErrorMessage = "Vui lòng nhập mật khẩu mới.")]
        [StringLength(100, MinimumLength = 6,
            ErrorMessage = "Mật khẩu phải từ {2} đến {1} ký tự.")]
        [DataType(DataType.Password)]
        [Display(Name = "Mật khẩu mới")]
        public string NewPassword { get; set; } = string.Empty;

        [Required(ErrorMessage = "Vui lòng xác nhận mật khẩu mới.")]
        [DataType(DataType.Password)]
        [Compare(nameof(NewPassword), ErrorMessage = "Mật khẩu nhập lại không khớp.")]
        [Display(Name = "Nhập lại mật khẩu mới")]
        public string ConfirmPassword { get; set; } = string.Empty;
    }

    public void OnGet() { }

    public async Task<IActionResult> OnPostAsync()
    {
        if (!ModelState.IsValid)
            return Page();

        var user = await _userManager.GetUserAsync(User);
        if (user is null)
            return RedirectToPage("/Account/Login");

        var result = await _userManager.ChangePasswordAsync(
            user, Input.CurrentPassword, Input.NewPassword);

        if (!result.Succeeded)
        {
            foreach (var err in result.Errors)
            {
                // Map the wrong-current-password error onto its field.
                var key = err.Code == "PasswordMismatch"
                    ? nameof(Input.CurrentPassword)
                    : string.Empty;
                ModelState.AddModelError(key, err.Description);
            }
            return Page();
        }

        // Keep the current session valid after the security stamp changes.
        await _signInManager.RefreshSignInAsync(user);

        _logger.LogInformation("User {UserId} changed their password.", user.Id);
        _ = _audit.LogAsync(
            "auth.password_change", "auth",
            resourceType: nameof(ApplicationUser),
            resourceId:   user.Id);

        Succeeded = true;
        ModelState.Clear();
        Input = new InputModel();
        return Page();
    }
}
