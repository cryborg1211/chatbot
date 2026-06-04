using System.ComponentModel.DataAnnotations;

namespace chatbot.Models;

/// <summary>
/// A single message inside a <see cref="Conversation"/>.
/// User messages and assistant replies share this table; discriminate by <see cref="Role"/>.
/// </summary>
public class ChatMessage
{
    [Key]
    public Guid Id { get; set; } = Guid.NewGuid();

    [Required]
    public Guid ConversationId { get; set; }
    public Conversation? Conversation { get; set; }

    public ChatRole Role { get; set; }

    /// <summary>
    /// Full message text. For assistant rows this is the assembled stream
    /// (we don't persist token-by-token — only the final transcript).
    /// nvarchar(max) so large RAG answers don't truncate.
    /// </summary>
    [Required]
    public string Content { get; set; } = string.Empty;

    /// <summary>
    /// JSON array of source <see cref="Document.Id"/>s that grounded an
    /// assistant reply (empty / null for user rows).
    /// Stored as a string column to keep the schema portable across SQL
    /// editions; deserialise at the service layer.
    /// </summary>
    [MaxLength(4000)]
    public string? SourceDocumentIdsJson { get; set; }

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>End-to-end latency of an assistant reply in ms; <c>null</c> for user rows.</summary>
    public int? LatencyMs { get; set; }
}
