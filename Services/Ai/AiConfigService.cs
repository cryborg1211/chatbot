using chatbot.Data;
using chatbot.Models;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Services.Ai;

/// <inheritdoc/>
public sealed class AiConfigService : IAiConfigService
{
    private readonly ApplicationDbContext _db;

    public AiConfigService(ApplicationDbContext db) => _db = db;

    public async Task<AiConfig> GetAsync(CancellationToken cancellationToken = default)
    {
        var config = await _db.AiConfigs.FirstOrDefaultAsync(c => c.Id == 1, cancellationToken);
        if (config is null)
        {
            // The seed row should exist via migration; recreate defensively if not.
            config = new AiConfig { Id = 1, ActiveProvider = "ollama", UpdatedAt = DateTime.UtcNow };
            _db.AiConfigs.Add(config);
            await _db.SaveChangesAsync(cancellationToken);
        }
        return config;
    }

    public async Task UpdateAsync(
        string activeProvider,
        string? activeModel,
        string? updatedBy,
        CancellationToken cancellationToken = default)
    {
        var config = await GetAsync(cancellationToken);
        config.ActiveProvider = string.IsNullOrWhiteSpace(activeProvider) ? "ollama" : activeProvider.Trim().ToLowerInvariant();
        config.ActiveModel    = string.IsNullOrWhiteSpace(activeModel) ? null : activeModel.Trim();
        config.UpdatedBy      = updatedBy;
        config.UpdatedAt      = DateTime.UtcNow;
        await _db.SaveChangesAsync(cancellationToken);
    }
}
