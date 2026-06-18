namespace chatbot.Infrastructure.Identity;

/// <summary>
/// Custom claim type constants used throughout the application.
/// Keep these centralised so the RAG layer and the auth layer agree
/// on the wire format.
/// </summary>
public static class AppClaimTypes
{
    /// <summary>Tenant key — the user's <c>DepartmentId</c>.</summary>
    public const string DepartmentId = "department_id";

    /// <summary>Display name for headers/UI greeting.</summary>
    public const string FullName = "full_name";

    /// <summary>Storage-relative path to the user's avatar blob, served via /api/account/avatar.</summary>
    public const string AvatarPath = "avatar_path";
}
