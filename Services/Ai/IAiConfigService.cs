using chatbot.Models;

namespace chatbot.Services.Ai;

/// <summary>Reads / writes the singleton <see cref="AiConfig"/> row (Id = 1).</summary>
public interface IAiConfigService
{
    /// <summary>Get the current config, recreating the seed row if it is missing.</summary>
    Task<AiConfig> GetAsync(CancellationToken cancellationToken = default);

    /// <summary>
    /// Persist admin changes. Null knobs are stored as null, meaning "use the
    /// worker's own default" for that value.
    /// </summary>
    Task UpdateAsync(
        string activeProvider,
        string? activeModel,
        string? updatedBy,
        CancellationToken cancellationToken = default);
}
