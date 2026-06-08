using System.Security.Claims;
using System.Text.Json;
using System.Text.Json.Serialization;
using chatbot.Data;
using chatbot.Infrastructure.Identity;
using chatbot.Models;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace chatbot.Infrastructure.Audit;

/// <inheritdoc/>
///
/// <remarks>
/// Audit writes always happen in a freshly-created DI scope so the row
/// can survive the caller's scope being disposed (typical with the
/// fire-and-forget <c>_ = _audit.LogAsync(...)</c> pattern after an
/// HTTP request returns or a background-service tick ends).
///
/// Snapshotting request-scoped values (claims, IP, UA, correlation id)
/// is done synchronously BEFORE the new scope spins up — that data must
/// be captured while the original <see cref="HttpContext"/> is still alive.
/// </remarks>
public sealed class AuditLogger : IAuditLogger
{
    private const string CorrelationHeader = "X-Correlation-Id";

    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy   = JsonNamingPolicy.SnakeCaseLower,
        DictionaryKeyPolicy    = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly IServiceScopeFactory _scopeFactory;
    private readonly IHttpContextAccessor _httpAccessor;
    private readonly ILogger<AuditLogger> _logger;

    public AuditLogger(
        IServiceScopeFactory scopeFactory,
        IHttpContextAccessor httpAccessor,
        ILogger<AuditLogger> logger)
    {
        _scopeFactory = scopeFactory;
        _httpAccessor = httpAccessor;
        _logger       = logger;
    }

    public async Task LogAsync(
        string  action,
        string  category,
        LogSeverity severity = LogSeverity.Info,
        string? resourceType = null,
        string? resourceId   = null,
        object? details      = null,
        bool    success      = true,
        string? overrideUserId       = null,
        string? overrideDepartmentId = null,
        CancellationToken cancellationToken = default)
    {
        // ---- Snapshot ambient request context BEFORE the new scope ----
        // The HttpContext belongs to the *caller's* scope; once we create
        // our own scope the original may already be disposed.
        var ctx   = _httpAccessor.HttpContext;
        var princ = ctx?.User;

        var userId       = overrideUserId       ?? princ?.FindFirstValue(ClaimTypes.NameIdentifier);
        var departmentId = overrideDepartmentId ?? princ?.FindFirstValue(AppClaimTypes.DepartmentId);

        var ip = ctx?.Connection?.RemoteIpAddress?.ToString();
        var ua = ctx?.Request?.Headers.UserAgent.ToString();
        if (string.IsNullOrWhiteSpace(ua)) ua = null;

        Guid? corr = null;
        if (ctx is not null
            && ctx.Request.Headers.TryGetValue(CorrelationHeader, out var hdr)
            && Guid.TryParse(hdr.ToString(), out var parsed))
        {
            corr = parsed;
        }

        string? detailsJson = null;
        if (details is not null)
        {
            try { detailsJson = JsonSerializer.Serialize(details, JsonOpts); }
            catch (Exception jx)
            {
                _logger.LogWarning(jx, "audit_details_serialise_failed action={Action}", action);
            }
        }

        var row = new SystemLog
        {
            Timestamp     = DateTime.UtcNow,
            Action        = action,
            Category      = category,
            Severity      = severity,
            UserId        = string.IsNullOrWhiteSpace(userId)       ? null : userId,
            DepartmentId  = string.IsNullOrWhiteSpace(departmentId) ? null : departmentId,
            ResourceType  = resourceType,
            ResourceId    = resourceId,
            IpAddress     = ip,
            UserAgent     = Truncate(ua, 500),
            CorrelationId = corr,
            Details       = detailsJson,
            Success       = success,
        };

        // ---- Persist in a fresh scope so the caller's DbContext can dispose
        //      without taking our write down with it. ----
        try
        {
            await using var scope = _scopeFactory.CreateAsyncScope();
            var db = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();

            db.SystemLogs.Add(row);
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (Exception ex)
        {
            // Audit write must never break the caller. Fall back to ILogger.
            _logger.LogError(ex,
                "audit_write_failed action={Action} category={Category} resource={ResourceType}/{ResourceId}",
                action, category, resourceType, resourceId);
        }
    }

    private static string? Truncate(string? s, int max)
        => string.IsNullOrEmpty(s) ? s : (s.Length <= max ? s : s[..max]);
}
