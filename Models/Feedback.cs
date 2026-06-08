using System.ComponentModel.DataAnnotations;

namespace chatbot.Models;

/// <summary>
/// One user's rating of one assistant <see cref="ChatMessage"/>.
/// Unique on (UserId, ChatMessageId) — a re-click flips the rating
/// instead of inserting a duplicate row (handled in
/// <c>FeedbackController.Submit</c>).
/// </summary>
public class Feedback
{
    [Key]
    public Guid Id { get; set; } = Guid.NewGuid();

    [Required]
    public Guid ChatMessageId { get; set; }
    public ChatMessage? ChatMessage { get; set; }

    [Required]
    public string UserId { get; set; } = default!;
    public ApplicationUser? User { get; set; }

    public FeedbackRating Rating { get; set; }

    [MaxLength(1000)]
    public string? Comment { get; set; }

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
}
