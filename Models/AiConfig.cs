using System.ComponentModel.DataAnnotations;

namespace chatbot.Models;

/// <summary>
/// Singleton runtime AI configuration — exactly one row, <c>Id = 1</c>. Source
/// of truth for the admin-selected provider/model + inference knobs, read by
/// <c>ChatService</c> on every query so changes take effect without restarting
/// the worker.
///
/// A <c>null</c> knob means "use the worker's own <c>.env</c> default", so a
/// fresh install (all nulls) behaves exactly as before the dashboard existed.
/// </summary>
public class AiConfig
{
    /// <summary>Fixed singleton key — always 1.</summary>
    public int Id { get; set; } = 1;

    [Required]
    [MaxLength(32)]
    public string ActiveProvider { get; set; } = "ollama";

    /// <summary>Selected model id, or null to use the worker default.</summary>
    [MaxLength(128)]
    public string? ActiveModel { get; set; }

    /// <summary>Sampling temperature, or null for the worker default.</summary>
    public double? Temperature { get; set; }

    /// <summary>Retrieval top-k, or null for the worker default.</summary>
    public int? TopK { get; set; }

    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    [MaxLength(450)]
    public string? UpdatedBy { get; set; }
}
