using System.Security.Claims;
using chatbot.Models;
using Microsoft.AspNetCore.Identity;
using Microsoft.Extensions.Options;

namespace chatbot.Infrastructure.Identity;

/// <summary>
/// Custom claims factory — runs once at sign-in (when Identity builds
/// the auth cookie) and embeds the user's <c>DepartmentId</c> and
/// <c>FullName</c> into the <see cref="ClaimsPrincipal"/>.
///
/// Why a factory and not <c>IClaimsTransformation</c>?  The factory
/// runs once per login, the transformation runs once per request.
/// Tenant id is static for the user, so doing it at login is cheaper.
///
/// Downstream RAG code reads the claim via:
/// <code>
///     User.FindFirstValue(AppClaimTypes.DepartmentId)
/// </code>
/// and uses it as a metadata filter on the vector store.
/// </summary>
public class ApplicationUserClaimsPrincipalFactory
    : UserClaimsPrincipalFactory<ApplicationUser, IdentityRole>
{
    public ApplicationUserClaimsPrincipalFactory(
        UserManager<ApplicationUser> userManager,
        RoleManager<IdentityRole> roleManager,
        IOptions<IdentityOptions> optionsAccessor)
        : base(userManager, roleManager, optionsAccessor)
    {
    }

    protected override async Task<ClaimsIdentity> GenerateClaimsAsync(ApplicationUser user)
    {
        var identity = await base.GenerateClaimsAsync(user);

        // Tenant claim — primary purpose of this factory.
        if (!string.IsNullOrWhiteSpace(user.DepartmentId))
        {
            identity.AddClaim(new Claim(AppClaimTypes.DepartmentId, user.DepartmentId));
        }

        // Display-name claim — convenience for the UI layer.
        if (!string.IsNullOrWhiteSpace(user.FullName))
        {
            identity.AddClaim(new Claim(AppClaimTypes.FullName, user.FullName));
        }

        return identity;
    }
}
