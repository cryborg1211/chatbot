using chatbot.Models;
using Microsoft.Extensions.Options;

namespace chatbot.Infrastructure.Storage;

/// <summary>
/// Local-disk implementation of <see cref="IDocumentStorage"/>.
/// Layout: <c>{ContentRoot}/{UploadsRoot}/{yyyy-MM}/{guid}{.ext}</c>.
/// Month-bucketing keeps directories small on Windows.
/// </summary>
public sealed class LocalFileSystemStorage : IDocumentStorage
{
    private readonly string _rootPath;

    public LocalFileSystemStorage(
        IOptions<StorageOptions> options,
        IWebHostEnvironment env)
    {
        var configured = options.Value.UploadsRoot;
        var basePath = Path.IsPathRooted(configured)
            ? configured
            : Path.Combine(env.ContentRootPath, configured);

        // Normalize ONCE: flips '/' → OS separator on Windows, collapses '.'/'..',
        // expands to absolute form. Without this, _rootPath can be a mix of
        // '\' (from ContentRootPath) and '/' (from the config value), which then
        // breaks the StartsWith check in ResolveAbsolute().
        //
        // Trailing separator is critical: prevents sibling-folder bypass where
        // "C:\…\uploads" would also match "C:\…\uploadsEvil\file.pdf".
        _rootPath = Path.GetFullPath(basePath);
        if (!_rootPath.EndsWith(Path.DirectorySeparatorChar))
            _rootPath += Path.DirectorySeparatorChar;

        Directory.CreateDirectory(_rootPath);
    }

    public async Task<StoredFile> SaveAsync(
        Stream content,
        string originalFileName,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(content);

        // Build relative path: 2024-06/8b3a…ext
        var ext = Path.GetExtension(originalFileName);          // ".pdf"
        var bucket = DateTime.UtcNow.ToString("yyyy-MM");
        var name = $"{Guid.NewGuid():N}{ext}";
        var relative = Path.Combine(bucket, name).Replace('\\', '/');

        var absoluteDir = Path.Combine(_rootPath, bucket);
        Directory.CreateDirectory(absoluteDir);
        var absoluteFile = Path.Combine(absoluteDir, name);

        await using var fileStream = new FileStream(
            absoluteFile,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            bufferSize: 81920,
            useAsync: true);

        await content.CopyToAsync(fileStream, cancellationToken);
        var size = fileStream.Length;

        return new StoredFile(relative, size);
    }

    public Task<Stream> OpenReadAsync(
        string storedFileName,
        CancellationToken cancellationToken = default)
    {
        var path = ResolveAbsolute(storedFileName);
        Stream stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            bufferSize: 81920,
            useAsync: true);
        return Task.FromResult(stream);
    }

    public Task DeleteAsync(string storedFileName)
    {
        var path = ResolveAbsolute(storedFileName);
        if (File.Exists(path))
            File.Delete(path);
        return Task.CompletedTask;
    }

    private string ResolveAbsolute(string relative)
    {
        // Defensive: prevent path traversal (e.g. "../../etc/passwd").
        // Path.Combine accepts both '/' and '\' on Windows; Path.GetFullPath
        // normalizes the result to the OS separator and collapses ".." segments.
        // _rootPath was normalized in the ctor and already ends with the OS
        // separator, so a simple prefix check is now safe.
        var combined = Path.GetFullPath(Path.Combine(_rootPath, relative));

        var comparison = OperatingSystem.IsWindows()
            ? StringComparison.OrdinalIgnoreCase   // NTFS is case-insensitive
            : StringComparison.Ordinal;            // POSIX is case-sensitive

        if (!combined.StartsWith(_rootPath, comparison))
            throw new UnauthorizedAccessException("Path escapes storage root.");

        return combined;
    }
}
