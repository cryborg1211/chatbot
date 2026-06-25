using chatbot.Data;
using chatbot.Models;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Services.Ai;

/// <inheritdoc/>
public sealed class ProviderKeyService : IProviderKeyService
{
    private readonly ApplicationDbContext _db;
    private readonly IDataProtector _protector;

    public ProviderKeyService(ApplicationDbContext db, IDataProtectionProvider dataProtection)
    {
        _db = db;
        _protector = dataProtection.CreateProtector("ai-provider-keys.v1");
    }

    public async Task SaveAsync(string provider, string plaintextKey, CancellationToken cancellationToken = default)
    {
        provider = provider.Trim().ToLowerInvariant();
        var encrypted = _protector.Protect(plaintextKey);

        var row = await _db.AiProviderKeys.FirstOrDefaultAsync(k => k.Provider == provider, cancellationToken);
        if (row is null)
        {
            row = new AiProviderKey { Provider = provider };
            _db.AiProviderKeys.Add(row);
        }
        row.EncryptedKey = encrypted;
        row.UpdatedAt    = DateTime.UtcNow;
        row.ValidatedAt  = null;
        await _db.SaveChangesAsync(cancellationToken);
    }

    public async Task DeleteAsync(string provider, CancellationToken cancellationToken = default)
    {
        provider = provider.Trim().ToLowerInvariant();
        var row = await _db.AiProviderKeys.FirstOrDefaultAsync(k => k.Provider == provider, cancellationToken);
        if (row is not null)
        {
            _db.AiProviderKeys.Remove(row);
            await _db.SaveChangesAsync(cancellationToken);
        }
    }

    public async Task<string?> GetPlaintextAsync(string provider, CancellationToken cancellationToken = default)
    {
        provider = provider.Trim().ToLowerInvariant();
        var row = await _db.AiProviderKeys.AsNoTracking()
            .FirstOrDefaultAsync(k => k.Provider == provider, cancellationToken);
        if (row is null) return null;
        try { return _protector.Unprotect(row.EncryptedKey); }
        catch { return null; }   // key rotation / corrupted blob → treat as not set
    }

    public async Task<IReadOnlySet<string>> GetConfiguredAsync(CancellationToken cancellationToken = default)
    {
        var providers = await _db.AiProviderKeys.AsNoTracking()
            .Select(k => k.Provider)
            .ToListAsync(cancellationToken);
        return providers.ToHashSet();
    }

    public async Task MarkValidatedAsync(string provider, CancellationToken cancellationToken = default)
    {
        provider = provider.Trim().ToLowerInvariant();
        var row = await _db.AiProviderKeys.FirstOrDefaultAsync(k => k.Provider == provider, cancellationToken);
        if (row is not null)
        {
            row.ValidatedAt = DateTime.UtcNow;
            await _db.SaveChangesAsync(cancellationToken);
        }
    }
}
