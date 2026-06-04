namespace chatbot.Models;

/// <summary>
/// Author of a <see cref="ChatMessage"/>.
/// Stored as <c>int</c> in SQL so order matters — never renumber.
/// </summary>
public enum ChatRole
{
    /// <summary>The end user typing in the chat box.</summary>
    User      = 0,

    /// <summary>The LLM's reply (assembled from the streamed tokens).</summary>
    Assistant = 1,

    /// <summary>System / instruction message — reserved for future prompt-engineering needs.</summary>
    System    = 2,
}
