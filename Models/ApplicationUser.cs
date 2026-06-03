using System.ComponentModel.DataAnnotations;
using Microsoft.AspNetCore.Identity;

namespace chatbot.Models;

/// <summary>
/// Application user — extends the default <see cref="IdentityUser"/> with
/// the fields needed for multi-tenant RAG: a department (tenant) and a
/// display-friendly full name.
/// </summary>
public class ApplicationUser : IdentityUser
{
    [Required]
    [MaxLength(200)]
    public string FullName { get; set; } = default!;

    /// <summary>
    /// Foreign key to <see cref="Department.Id"/>. Used to scope which
    /// knowledge-base documents/vectors this user is allowed to query.
    /// </summary>
    [Required]
    [MaxLength(20)]
    public string DepartmentId { get; set; } = default!;

    // ---- Navigation ----
    public Department? Department { get; set; }
}
