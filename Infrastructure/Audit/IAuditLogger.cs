using chatbot.Models;

namespace chatbot.Infrastructure.Audit;

/// <summary>
/// Append-only audit writer. Specified in
/// <see href="file:.claude/audit_system.md">audit_system.md</see> §5.
///
/// <para><b>Contract:</b> Implementations MUST NEVER throw out to the
/// caller. A failure to write an audit row must not break a real request.
/// Callers should fire-and-forget: <c>_ = _audit.LogAsync(...);</c></para>
/// </summary>
public interface IAuditLogger
{
    /// <summary>
    /// Persist one audit event. User / IP / UA / correlation-id are
    /// resolved automatically from the ambient <c>HttpContext</c> when
    /// present; pass them via <paramref name="overrideUserId"/> /
    /// <paramref name="overrideDepartmentId"/> for background contexts
    /// (e.g. hosted services) that have no <c>HttpContext</c>.
    /// </summary>
    Task LogAsync(
        string  action,
        string  category,
        LogSeverity severity = LogSeverity.Info,
        string? resourceType = null,
        string? resourceId   = null,
        object? details      = null,
        bool    success      = true,
        string? overrideUserId       = null,
        string? overrideDepartmentId = null,
        CancellationToken cancellationToken = default);
}
