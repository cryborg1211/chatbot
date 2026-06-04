namespace chatbot.Models;

/// <summary>
/// Hard limits for document uploads. Centralised so the controller's
/// <c>[RequestSizeLimit]</c> attribute (requires <c>const</c>) and the
/// service's runtime check stay in sync.
/// </summary>
public static class DocumentLimits
{
    /// <summary>Maximum file size: 20 MB.</summary>
    public const long MaxFileSizeBytes = 20L * 1024 * 1024;

    /// <summary>Slack added on top of the file size for multipart headers.</summary>
    public const long MultipartOverheadBytes = 4 * 1024;
}
