namespace chatbot.Services.Ai;

/// <summary>
/// Stores / retrieves third-party provider API keys, encrypted at rest via
/// ASP.NET Core Data Protection. Plaintext only ever leaves via
/// <see cref="GetPlaintextAsync"/> (server-side, for the worker hop) — never
/// to the browser.
/// </summary>
public interface IProviderKeyService
{
    Task SaveAsync(string provider, string plaintextKey, CancellationToken cancellationToken = default);

    Task DeleteAsync(string provider, CancellationToken cancellationToken = default);

    /// <summary>Decrypted key, or null if none / undecryptable.</summary>
    Task<string?> GetPlaintextAsync(string provider, CancellationToken cancellationToken = default);

    /// <summary>Set of providers that currently have a key configured.</summary>
    Task<IReadOnlySet<string>> GetConfiguredAsync(CancellationToken cancellationToken = default);

    Task MarkValidatedAsync(string provider, CancellationToken cancellationToken = default);
}
