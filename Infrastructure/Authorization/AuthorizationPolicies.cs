namespace chatbot.Infrastructure.Authorization;

/// <summary>
/// Policy-name constants — used in <c>[Authorize(Policy = ...)]</c>
/// attributes and in <c>AddAuthorization(...)</c> at startup. Reference
/// the constant, never a string literal at the call site.
/// </summary>
public static class AuthorizationPolicies
{
    /// <summary>User must be in the <see cref="Roles.Admin"/> role.</summary>
    public const string RequireAdmin = "RequireAdmin";
}
