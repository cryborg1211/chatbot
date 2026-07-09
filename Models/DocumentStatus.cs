namespace chatbot.Models;

/// <summary>
/// Lifecycle states of a knowledge-base document.
/// Stored as <c>int</c> in SQL so order matters — never renumber.
/// </summary>
public enum DocumentStatus
{
    /// <summary>File saved to disk; waiting for ingestion worker.</summary>
    Pending    = 0,

    /// <summary>Worker has claimed the row and is currently calling Python.</summary>
    Processing = 1,

    /// <summary>Chunks embedded and upserted to Qdrant; queryable.</summary>
    Ready      = 2,

    /// <summary>Ingestion failed; see <c>ErrorMessage</c>.</summary>
    Failed     = 3,

    /// <summary>Ingestion succeeded but some pages/content were dropped (e.g. OCR
    /// retry still missing pages). See <c>ErrorMessage</c> for details.</summary>
    PartiallyIngested = 4,
}
