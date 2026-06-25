using System.ComponentModel.DataAnnotations;

namespace chatbot.Models;

/// <summary>
/// One encrypted third-party LLM provider API key (one row per provider).
/// <see cref="EncryptedKey"/> holds a Data-Protection-protected blob — the raw
/// key is never stored and never returned to the browser.
/// </summary>
public class AiProviderKey
{
    /// <summary>Provider id, lowercase: "openai" | "anthropic" | "gemini".</summary>
    [Key]
    [MaxLength(32)]
    public string Provider { get; set; } = default!;

    /// <summary>IDataProtector-protected key blob.</summary>
    [Required]
    public string EncryptedKey { get; set; } = default!;

    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>Last time a connectivity test against this key succeeded.</summary>
    public DateTime? ValidatedAt { get; set; }
}
