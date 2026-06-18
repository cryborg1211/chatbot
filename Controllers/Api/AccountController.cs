using chatbot.Infrastructure.Identity;
using chatbot.Infrastructure.Storage;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace chatbot.Controllers.Api;

/// <summary>
/// Serves the current user's own avatar image. The blob lives outside wwwroot
/// (via <see cref="IDocumentStorage"/>); this authenticated endpoint is the only
/// way to read it back. The relative path comes from the
/// <see cref="AppClaimTypes.AvatarPath"/> claim — no DB hit, no cross-user access.
/// </summary>
[ApiController]
[Authorize]
[Route("api/account")]
public sealed class AccountController : ControllerBase
{
    private readonly IDocumentStorage _storage;
    private readonly ILogger<AccountController> _logger;

    public AccountController(IDocumentStorage storage, ILogger<AccountController> logger)
    {
        _storage = storage;
        _logger  = logger;
    }

    // GET /api/account/avatar
    [HttpGet("avatar")]
    public async Task<IActionResult> Avatar(CancellationToken cancellationToken)
    {
        var path = User.FindFirst(AppClaimTypes.AvatarPath)?.Value;
        if (string.IsNullOrWhiteSpace(path))
            return NotFound();

        Stream stream;
        try
        {
            stream = await _storage.OpenReadAsync(path, cancellationToken);
        }
        catch (Exception ex) when (ex is FileNotFoundException
                                      or DirectoryNotFoundException
                                      or UnauthorizedAccessException)
        {
            // Missing blob or a stale/legacy path value — fall back to the icon.
            _logger.LogWarning(ex, "avatar_blob_unavailable path={Path}", path);
            return NotFound();
        }

        return File(stream, ContentTypeFor(path));
    }

    private static string ContentTypeFor(string path) =>
        Path.GetExtension(path).ToLowerInvariant() switch
        {
            ".png"            => "image/png",
            ".jpg" or ".jpeg" => "image/jpeg",
            ".webp"           => "image/webp",
            ".gif"            => "image/gif",
            _                 => "application/octet-stream",
        };
}
