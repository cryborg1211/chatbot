using System.ComponentModel.DataAnnotations;
using chatbot.Infrastructure.Audit;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace chatbot.Pages.Account;

/// <summary>
/// Sign-in page. Uses <see cref="SignInManager{ApplicationUser}"/> so the
/// custom <c>ApplicationUserClaimsPrincipalFactory</c> runs and the
/// resulting cookie carries <c>department_id</c> / <c>full_name</c>.
/// </summary>
[AllowAnonymous]
public sealed class LoginModel : PageModel
{
    private readonly SignInManager<ApplicationUser> _signInManager;
    private readonly IAuditLogger _audit;
    private readonly ILogger<LoginModel> _logger;

    public LoginModel(
        SignInManager<ApplicationUser> signInManager,
        IAuditLogger audit,
        ILogger<LoginModel> logger)
    {
        _signInManager = signInManager;
        _audit         = audit;
        _logger        = logger;
    }

    [BindProperty]
    public InputModel Input { get; set; } = new();

    [BindProperty(SupportsGet = true)]
    public string? ReturnUrl { get; set; }

    public sealed class InputModel
    {
        [Required(ErrorMessage = "Vui lòng nhập email.")]
        [EmailAddress(ErrorMessage = "Email không hợp lệ.")]
        [Display(Name = "Email")]
        public string Email { get; set; } = string.Empty;

        [Required(ErrorMessage = "Vui lòng nhập mật khẩu.")]
        [DataType(DataType.Password)]
        [Display(Name = "Mật khẩu")]
        public string Password { get; set; } = string.Empty;
    }

    public IActionResult OnGet()
    {
        // Already signed in → straight to chat
        if (User.Identity?.IsAuthenticated == true)
            return LocalRedirect(ReturnUrl ?? "/Chat");

        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        if (!ModelState.IsValid)
            return Page();

        var result = await _signInManager.PasswordSignInAsync(
            userName:           Input.Email,
            password:           Input.Password,
            isPersistent:       false,
            lockoutOnFailure:   false);

        if (result.Succeeded)
        {
            _logger.LogInformation("User {Email} signed in.", Input.Email);
            var user = await _signInManager.UserManager.FindByEmailAsync(Input.Email);
            _ = _audit.LogAsync(
                "auth.login", "auth",
                overrideUserId:       user?.Id,
                overrideDepartmentId: user?.DepartmentId,
                details: new { email = Input.Email });
            return LocalRedirect(ReturnUrl ?? "/Chat");
        }

        if (result.IsLockedOut)
        {
            _ = _audit.LogAsync("auth.login_failed", "auth", LogSeverity.Warn,
                details: new { email = Input.Email, reason = "locked_out" }, success: false);
            ModelState.AddModelError(string.Empty, "Tài khoản đã bị khóa tạm thời.");
            return Page();
        }

        if (result.IsNotAllowed)
        {
            _ = _audit.LogAsync("auth.login_failed", "auth", LogSeverity.Warn,
                details: new { email = Input.Email, reason = "not_allowed" }, success: false);
            ModelState.AddModelError(string.Empty, "Tài khoản chưa được phép đăng nhập.");
            return Page();
        }

        _ = _audit.LogAsync("auth.login_failed", "auth", LogSeverity.Warn,
            details: new { email = Input.Email, reason = "wrong_password" }, success: false);
        ModelState.AddModelError(string.Empty, "Email hoặc mật khẩu không đúng.");
        return Page();
    }
}
