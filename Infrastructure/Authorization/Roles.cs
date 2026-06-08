namespace chatbot.Infrastructure.Authorization;

/// <summary>
/// Built-in role names. Seeded into <c>AspNetRoles</c> at startup by
/// <see cref="RoleSeeder"/>. Keep this list closed — every role
/// addition requires a doc update and a policy review.
/// </summary>
public static class Roles
{
    /// <summary>Full access — sees every department's data, can approve / reject users.</summary>
    public const string Admin = "Admin";

    /// <summary>Default role assigned to every newly-registered account.</summary>
    public const string User  = "User";
}
