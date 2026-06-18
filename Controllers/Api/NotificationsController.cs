using System.Globalization;
using chatbot.Data;
using chatbot.Infrastructure.Authorization;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace chatbot.Controllers.Api;

/// <summary>
/// Unseen-activity counts for the admin navbar bell. Reads the append-only
/// <see cref="Models.SystemLog"/> audit trail and counts new registrations
/// and feedback since a client-supplied "last seen" marker. Admin-only —
/// counts are cross-tenant (the bell lives on admin pages only).
/// </summary>
[ApiController]
[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]
[Route("api/admin/notifications")]
public sealed class NotificationsController : ControllerBase
{
    private readonly ApplicationDbContext _db;

    public NotificationsController(ApplicationDbContext db) => _db = db;

    // GET /api/admin/notifications?sinceIso=2026-06-17T08:00:00Z
    [HttpGet]
    public async Task<ActionResult<NotificationCounts>> Get(
        [FromQuery] string? sinceIso,
        CancellationToken cancellationToken)
    {
        // Parse the caller's last-seen marker; fall back to a 7-day window when
        // it's absent or malformed so the bell still shows something useful.
        var since = DateTime.UtcNow.AddDays(-7);
        if (!string.IsNullOrWhiteSpace(sinceIso) &&
            DateTimeOffset.TryParse(
                sinceIso,
                CultureInfo.InvariantCulture,
                DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                out var parsed))
        {
            since = parsed.UtcDateTime;
        }

        var newUsers = await _db.SystemLogs
            .CountAsync(l => l.Action == "auth.register" && l.Timestamp > since, cancellationToken);

        var newFeedback = await _db.SystemLogs
            .CountAsync(
                l => (l.Action == "feedback.submit" || l.Action == "feedback.update")
                     && l.Timestamp > since,
                cancellationToken);

        return Ok(new NotificationCounts(newUsers, newFeedback));
    }
}

public sealed record NotificationCounts(int NewUsers, int NewFeedback);
