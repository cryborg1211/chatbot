using System.ComponentModel.DataAnnotations;

namespace chatbot.Models;

/// <summary>
/// Represents an organizational unit (Phòng ban / Đơn vị).
/// Used as the tenant boundary for RAG document/vector filtering.
/// </summary>
public class Department
{
    /// <summary>
    /// Short, human-readable department code (e.g. "IT", "HR", "VP").
    /// Acts as the primary key — kept as a string so it can be embedded
    /// directly in vector-store filters without an extra join.
    /// </summary>
    [Key]
    [Required]
    [MaxLength(20)]
    public string Id { get; set; } = default!;

    [Required]
    [MaxLength(200)]
    public string Name { get; set; } = default!;

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    // ---- Navigation ----
    public ICollection<ApplicationUser> Users { get; set; } = new List<ApplicationUser>();
}
