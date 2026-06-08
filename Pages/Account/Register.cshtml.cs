using System.ComponentModel.DataAnnotations;
using chatbot.Data;
using chatbot.Infrastructure.Audit;
using chatbot.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Pages.Account;

/// <summary>
/// New-user registration. Validates the department code against the
/// seeded <c>Departments</c> table before creating the user, otherwise
/// the FK insert would blow up.  On success: signs the user in so the
/// custom claims factory injects the tenant claim immediately.
/// </summary>
[AllowAnonymous]
public sealed class RegisterModel : PageModel
{
    private readonly UserManager<ApplicationUser> _userManager;
    private readonly SignInManager<ApplicationUser> _signInManager;
    private readonly ApplicationDbContext _db;
    private readonly IAuditLogger _audit;
    private readonly ILogger<RegisterModel> _logger;

    public RegisterModel(
        UserManager<ApplicationUser> userManager,
        SignInManager<ApplicationUser> signInManager,
        ApplicationDbContext db,
        IAuditLogger audit,
        ILogger<RegisterModel> logger)
    {
        _userManager   = userManager;
        _signInManager = signInManager;
        _db            = db;
        _audit         = audit;
        _logger        = logger;
    }

    [BindProperty]
    public InputModel Input { get; set; } = new();

    public sealed class InputModel
    {
        [Required(ErrorMessage = "Vui lòng nhập họ và tên.")]
        [StringLength(200)]
        [Display(Name = "Họ và tên")]
        public string FullName { get; set; } = string.Empty;

        [Required(ErrorMessage = "Vui lòng nhập email.")]
        [EmailAddress(ErrorMessage = "Email không hợp lệ.")]
        [Display(Name = "Email công vụ")]
        public string Email { get; set; } = string.Empty;

        [Required(ErrorMessage = "Vui lòng nhập mật khẩu.")]
        [StringLength(100, MinimumLength = 6,
            ErrorMessage = "Mật khẩu phải từ {2} đến {1} ký tự.")]
        [DataType(DataType.Password)]
        [Display(Name = "Mật khẩu")]
        public string Password { get; set; } = string.Empty;

        [Required(ErrorMessage = "Vui lòng xác nhận mật khẩu.")]
        [DataType(DataType.Password)]
        [Compare(nameof(Password), ErrorMessage = "Mật khẩu nhập lại không khớp.")]
        [Display(Name = "Nhập lại mật khẩu")]
        public string ConfirmPassword { get; set; } = string.Empty;

        [Required(ErrorMessage = "Vui lòng nhập mã định danh phòng ban.")]
        [StringLength(20, MinimumLength = 2,
            ErrorMessage = "Mã định danh phải từ {2} đến {1} ký tự.")]
        [Display(Name = "Mã định danh Phòng ban/Đơn vị")]
        public string DepartmentId { get; set; } = string.Empty;
    }

    public IActionResult OnGet()
    {
        if (User.Identity?.IsAuthenticated == true)
            return LocalRedirect("/Chat");
        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        if (!ModelState.IsValid)
            return Page();

        // Normalize to match seed values (IT/HR/ADMIN).
        var departmentId = Input.DepartmentId.Trim().ToUpperInvariant();

        var deptExists = await _db.Departments
            .AsNoTracking()
            .AnyAsync(d => d.Id == departmentId);

        if (!deptExists)
        {
            ModelState.AddModelError(nameof(Input.DepartmentId),
                "Mã định danh không tồn tại. Liên hệ quản trị viên.");
            return Page();
        }

        var user = new ApplicationUser
        {
            UserName       = Input.Email,
            Email          = Input.Email,
            FullName       = Input.FullName,
            DepartmentId   = departmentId,
            EmailConfirmed = true,   // we disabled the confirmation flow in Program.cs
        };

        var createResult = await _userManager.CreateAsync(user, Input.Password);
        if (!createResult.Succeeded)
        {
            foreach (var err in createResult.Errors)
                ModelState.AddModelError(string.Empty, err.Description);
            return Page();
        }

        _logger.LogInformation(
            "User {Email} registered for tenant {Dept}.", user.Email, user.DepartmentId);

        _ = _audit.LogAsync(
            "auth.register", "auth",
            overrideUserId:       user.Id,
            overrideDepartmentId: user.DepartmentId,
            resourceType:         nameof(ApplicationUser),
            resourceId:           user.Id,
            details: new { email = user.Email, departmentId = user.DepartmentId });

        // SignInAsync triggers ApplicationUserClaimsPrincipalFactory →
        // cookie now carries department_id + full_name.
        await _signInManager.SignInAsync(user, isPersistent: false);

        return LocalRedirect("/Chat");
    }
}
