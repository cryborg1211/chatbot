using System.ComponentModel.DataAnnotations;

namespace chatbot.Models;

/// <summary>
/// Metadata record for an uploaded knowledge-base file.
/// One row per upload. The actual bytes live on disk under
/// <c>Storage:UploadsRoot</c>; vectors live in Qdrant keyed by
/// <c>{Id}:{chunkIndex}</c>.
/// </summary>
public class Document
{
    [Key]
    public Guid Id { get; set; } = Guid.NewGuid();

    /// <summary>Name the user uploaded (e.g. "Policy 2024.pdf"). Shown in UI.</summary>
    [Required]
    [MaxLength(255)]
    public string OriginalFileName { get; set; } = default!;

    /// <summary>
    /// Relative on-disk path returned by <c>IDocumentStorage.SaveAsync</c>
    /// (e.g. <c>"2024-06/8b3a9e0e-….pdf"</c>). Never expose to clients.
    /// </summary>
    [Required]
    [MaxLength(500)]
    public string StoredFileName { get; set; } = default!;

    [Required]
    [MaxLength(100)]
    public string MimeType { get; set; } = default!;

    public long SizeBytes { get; set; }

    // ---- Tenant ----
    [Required]
    [MaxLength(20)]
    public string DepartmentId { get; set; } = default!;
    public Department? Department { get; set; }

    // ---- Uploader (FK → AspNetUsers.Id) ----
    [Required]
    public string UploaderId { get; set; } = default!;
    public ApplicationUser? Uploader { get; set; }

    // ---- Ingestion lifecycle ----
    public DocumentStatus Status { get; set; } = DocumentStatus.Pending;

    /// <summary>Number of chunks the Python worker produced; 0 until Ready.</summary>
    public int ChunkCount { get; set; }

    public DateTime UploadedAt { get; set; } = DateTime.UtcNow;
    public DateTime? ProcessedAt { get; set; }

    [MaxLength(1000)]
    public string? ErrorMessage { get; set; }
}
