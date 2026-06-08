namespace chatbot.Models;

/// <summary>
/// User rating on an assistant message. Stored as int (signed) so SQL
/// queries can SUM/AVG directly for analytics.
/// </summary>
public enum FeedbackRating
{
    ThumbsDown = -1,
    ThumbsUp   =  1,
}
