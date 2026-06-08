using System.ComponentModel.DataAnnotations;

namespace chatbot.Models;

/// <summary>
/// One append-only audit row. Specified in
/// <see href="file:.claude/audit_system.md">audit_system.md</see> §3.1.
///
/// <para><b>Append-only rule:</b> application code never UPDATEs or DELETEs
/// these rows — only the retention job (§7 of audit_system.md) prunes
/// them at 180 days.</para>
///
/// <para><b>PII rule:</b> <see cref="Details"/> MUST NOT contain raw chat
/// content, document text, or any PII beyond identifiers and metrics.
/// Audit rows reference live tables by id; admins join when needed.</para>
/// </summary>
public class SystemLog
{
    [Key]
    public long Id { get; set; }

    public DateTime Timestamp { get; set; } = DateTime.UtcNow;

    /// <summary>Stable code from the closed taxonomy — see audit_system.md §4.</summary>
    [Required]
    [MaxLength(64)]
    public string Action { get; set; } = default!;

    /// <summary>Bucket the action belongs to: "auth" | "user" | "doc" | "chat" | "sys".</summary>
    [Required]
    [MaxLength(32)]
    public string Category { get; set; } = default!;

    public LogSeverity Severity { get; set; } = LogSeverity.Info;

    /// <summary><c>AspNetUsers.Id</c>. Null for anonymous or system events.</summary>
    [MaxLength(450)]
    public string? UserId { get; set; }

    /// <summary>Tenant scope. Null for cross-tenant or system events.</summary>
    [MaxLength(20)]
    public string? DepartmentId { get; set; }

    [MaxLength(64)]
    public string? ResourceType { get; set; }

    [MaxLength(128)]
    public string? ResourceId { get; set; }

    [MaxLength(45)]   // IPv6-sized
    public string? IpAddress { get; set; }

    [MaxLength(500)]
    public string? UserAgent { get; set; }

    /// <summary>One HTTP request = one id, threads through worker calls.</summary>
    public Guid? CorrelationId { get; set; }

    /// <summary>JSON payload — per-action shape, see audit_system.md §4.</summary>
    public string? Details { get; set; }

    public bool Success { get; set; } = true;
}
