using chatbot.Models;
using Microsoft.AspNetCore.Identity;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace chatbot.Infrastructure.Authorization;

/// <summary>
/// Idempotent startup seeder:
/// <list type="number">
///   <item>Ensures the <see cref="Roles.Admin"/> and <see cref="Roles.User"/>
///         rows exist in <c>AspNetRoles</c>.</item>
///   <item>If a <c>Bootstrap:AdminEmail</c> + <c>Bootstrap:AdminPassword</c>
///         config pair is present, creates that user (with dept
///         <c>Bootstrap:AdminDepartmentId</c>, default <c>ADMIN</c>) and
///         puts them in <see cref="Roles.Admin"/>. Re-running is safe —
///         existing users are only role-elevated, never recreated.</item>
/// </list>
/// Replaces the documented "manually update <c>AspNetUserRoles</c> after
/// first register" step in <c>RUNBOOK.md</c>.
/// </summary>
public sealed class RoleSeeder : IHostedService
{
    private const string DefaultAdminDepartmentId = "ADMIN";
    private const string DefaultAdminFullName     = "System Administrator";

    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<RoleSeeder>  _logger;

    public RoleSeeder(IServiceScopeFactory scopeFactory, ILogger<RoleSeeder> logger)
    {
        _scopeFactory = scopeFactory;
        _logger       = logger;
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        // Fire-and-forget so a slow seed doesn't block the rest of the host
        // from starting. Errors are caught + logged inside SeedAsync.
        _ = Task.Run(() => SeedAsync(cancellationToken), cancellationToken);
        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    private async Task SeedAsync(CancellationToken cancellationToken)
    {
        try
        {
            await using var scope = _scopeFactory.CreateAsyncScope();
            var sp        = scope.ServiceProvider;
            var roleMgr   = sp.GetRequiredService<RoleManager<IdentityRole>>();
            var userMgr   = sp.GetRequiredService<UserManager<ApplicationUser>>();
            var config    = sp.GetRequiredService<IConfiguration>();

            // ---- 1. Roles ----
            foreach (var role in new[] { Roles.Admin, Roles.User })
            {
                if (!await roleMgr.RoleExistsAsync(role))
                {
                    var r = await roleMgr.CreateAsync(new IdentityRole(role));
                    if (r.Succeeded) _logger.LogInformation("role_seeded name={Role}", role);
                    else             _logger.LogWarning   ("role_seed_failed name={Role} errors={Errs}",
                                                            role, string.Join(',', r.Errors));
                }
            }

            // ---- 2. Bootstrap admin (only if both config keys present) ----
            var email    = config["Bootstrap:AdminEmail"];
            var password = config["Bootstrap:AdminPassword"];
            if (string.IsNullOrWhiteSpace(email) || string.IsNullOrWhiteSpace(password))
            {
                _logger.LogInformation("bootstrap_admin_skipped reason=no_config");
                return;
            }

            var deptId   = config["Bootstrap:AdminDepartmentId"] ?? DefaultAdminDepartmentId;
            var fullName = config["Bootstrap:AdminFullName"]     ?? DefaultAdminFullName;

            var existing = await userMgr.FindByEmailAsync(email);
            if (existing is null)
            {
                existing = new ApplicationUser
                {
                    UserName       = email,
                    Email          = email,
                    FullName       = fullName,
                    DepartmentId   = deptId,
                    EmailConfirmed = true,
                };
                var createResult = await userMgr.CreateAsync(existing, password);
                if (!createResult.Succeeded)
                {
                    _logger.LogError("bootstrap_admin_create_failed email={Email} errors={Errs}",
                        email, string.Join(',', createResult.Errors));
                    return;
                }
                _logger.LogInformation("bootstrap_admin_created email={Email} dept={Dept}", email, deptId);
            }

            if (!await userMgr.IsInRoleAsync(existing, Roles.Admin))
            {
                var roleResult = await userMgr.AddToRoleAsync(existing, Roles.Admin);
                if (roleResult.Succeeded)
                    _logger.LogInformation("bootstrap_admin_role_granted email={Email}", email);
                else
                    _logger.LogError("bootstrap_admin_role_failed email={Email} errors={Errs}",
                        email, string.Join(',', roleResult.Errors));
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "role_seeder_failed");
        }
    }
}
