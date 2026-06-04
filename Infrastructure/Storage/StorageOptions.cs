namespace chatbot.Infrastructure.Storage;

/// <summary>Strongly-typed binding for the <c>Storage</c> config section.</summary>
public sealed class StorageOptions
{
    public const string SectionName = "Storage";

    /// <summary>
    /// Root folder for blob storage, relative to the app's ContentRoot.
    /// Default: <c>App_Data/uploads</c>.
    /// </summary>
    public string UploadsRoot { get; set; } = "App_Data/uploads";
}
