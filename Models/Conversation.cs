using System.ComponentModel.DataAnnotations;

namespace chatbot.Models;

/// <summary>
/// A single chat thread between one user and the assistant.
/// Owns an ordered collection of <see cref="ChatMessage"/>.
///
/// Tenant rule (§5.5): conversations are not tagged with a department —
/// the tenant filter is enforced at retrieval time via the user's
/// <c>DepartmentId</c> claim. A user only ever sees their own conversations
/// (filtered by <see cref="UserId"/>).
/// </summary>
public class Conversation
{
    [Key]
    public Guid Id { get; set; } = Guid.NewGuid();

    /// <summary>FK → <c>AspNetUsers.Id</c>. Owner of the conversation.</summary>
    [Required]
    public string UserId { get; set; } = default!;
    public ApplicationUser? User { get; set; }

    /// <summary>
    /// Human-friendly title shown in the sidebar.
    /// Defaults to "Cuộc trò chuyện mới"; the service layer may rewrite this
    /// from the first user message (e.g. first 40 chars).
    /// </summary>
    [Required]
    [MaxLength(200)]
    public string Title { get; set; } = "Cuộc trò chuyện mới";

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>Bumped by the service every time a message is appended — drives sidebar sort.</summary>
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    // ---- Navigation ----
    public ICollection<ChatMessage> Messages { get; set; } = new List<ChatMessage>();
}
