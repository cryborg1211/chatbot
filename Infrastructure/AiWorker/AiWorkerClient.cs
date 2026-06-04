using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using chatbot.Infrastructure.AiWorker.Contracts;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace chatbot.Infrastructure.AiWorker;

/// <summary>
/// Typed <see cref="HttpClient"/> implementation of <see cref="IAiWorkerClient"/>.
/// Registered via <c>AddHttpClient&lt;IAiWorkerClient, AiWorkerClient&gt;</c>
/// so it gets the factory's connection pooling + DNS refresh.
/// </summary>
public sealed class AiWorkerClient : IAiWorkerClient
{
    private const string ApiKeyHeader = "X-Worker-Api-Key";
    private const string IngestPath   = "ingest";
    private const string QueryPath    = "query";

    /// <summary>
    /// Shared JSON options. snake_case to match Python's Pydantic models,
    /// camelCase-tolerant via <see cref="JsonSerializerDefaults.Web"/>.
    /// </summary>
    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy   = JsonNamingPolicy.SnakeCaseLower,
        DictionaryKeyPolicy    = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly HttpClient _http;
    private readonly AiWorkerOptions _options;
    private readonly ILogger<AiWorkerClient> _logger;

    public AiWorkerClient(
        HttpClient httpClient,
        IOptions<AiWorkerOptions> options,
        ILogger<AiWorkerClient> logger)
    {
        _options = options.Value;
        _logger  = logger;

        if (string.IsNullOrWhiteSpace(_options.BaseUrl))
            throw new InvalidOperationException("AiWorker:BaseUrl is not configured.");
        if (string.IsNullOrWhiteSpace(_options.ApiKey))
            throw new InvalidOperationException("AiWorker:ApiKey is not configured.");

        httpClient.BaseAddress = new Uri(_options.BaseUrl.TrimEnd('/') + "/");
        httpClient.Timeout     = TimeSpan.FromSeconds(_options.TimeoutSeconds);
        httpClient.DefaultRequestHeaders.Add(ApiKeyHeader, _options.ApiKey);

        _http = httpClient;
    }

    // ==================================================================
    //  Ingest (unary, multipart)
    // ==================================================================

    public async Task<IngestResult> IngestAsync(
        IngestRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);

        using var form = new MultipartFormDataContent();

        // ---- Scalar form fields (string parts) ----
        form.Add(new StringContent(request.DocumentId.ToString()),    "document_id");
        form.Add(new StringContent(request.DepartmentId),             "department_id");
        form.Add(new StringContent(request.OriginalName),             "original_name");
        form.Add(new StringContent(request.MimeType),                 "mime_type");

        // ---- File part ----
        // StreamContent does not buffer — it streams straight to the wire.
        var fileContent = new StreamContent(request.FileContent);
        fileContent.Headers.ContentType = new MediaTypeHeaderValue(request.MimeType);
        form.Add(fileContent, name: "file", fileName: request.OriginalName);

        HttpResponseMessage response;
        try
        {
            response = await _http.PostAsync(IngestPath, form, cancellationToken);
        }
        catch (HttpRequestException ex)
        {
            _logger.LogError(ex, "AI worker unreachable at {BaseUrl}", _http.BaseAddress);
            throw new AiWorkerException("AI worker is unreachable.", ex);
        }
        catch (TaskCanceledException ex) when (!cancellationToken.IsCancellationRequested)
        {
            _logger.LogError(ex, "AI worker timed out after {Seconds}s", _options.TimeoutSeconds);
            throw new AiWorkerException("AI worker timed out.", ex);
        }

        using (response)
        {
            // 200 OK + 422 Unprocessable both carry an IngestResult body — let the
            // caller decide. Everything else is a transport-level failure.
            if (response.StatusCode is HttpStatusCode.OK or HttpStatusCode.UnprocessableEntity)
            {
                var result = await response.Content.ReadFromJsonAsync<IngestResult>(
                    cancellationToken: cancellationToken);

                if (result is null)
                    throw new AiWorkerException("AI worker returned empty body.");

                return result;
            }

            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            _logger.LogError(
                "AI worker returned {Status}: {Body}",
                (int)response.StatusCode, body);
            throw new AiWorkerException(
                $"AI worker returned HTTP {(int)response.StatusCode}: {body}");
        }
    }

    // ==================================================================
    //  Query (streaming, SSE)
    // ==================================================================

    public async IAsyncEnumerable<QueryEvent> QueryAsync(
        QueryRequest request,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);

        // ---- Build POST with JSON body, ask for SSE ----
        using var httpRequest = new HttpRequestMessage(HttpMethod.Post, QueryPath)
        {
            Content = JsonContent.Create(request, options: JsonOpts),
        };
        httpRequest.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));

        // ResponseHeadersRead — return as soon as headers are in, don't buffer the body.
        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(
                httpRequest,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken);
        }
        catch (HttpRequestException ex)
        {
            _logger.LogError(ex, "AI worker unreachable at {BaseUrl} (query)", _http.BaseAddress);
            throw new AiWorkerException("AI worker is unreachable.", ex);
        }
        catch (TaskCanceledException ex) when (!cancellationToken.IsCancellationRequested)
        {
            _logger.LogError(ex, "AI worker timed out (query) after {Seconds}s",
                _options.TimeoutSeconds);
            throw new AiWorkerException("AI worker timed out.", ex);
        }

        using var _resp = response; // ensures dispose even if caller breaks out of foreach

        if (!response.IsSuccessStatusCode)
        {
            var body = await response.Content.ReadAsStringAsync(cancellationToken);
            _logger.LogError(
                "AI worker /query returned {Status}: {Body}",
                (int)response.StatusCode, body);
            throw new AiWorkerException(
                $"AI worker /query returned HTTP {(int)response.StatusCode}: {body}");
        }

        // ---- Stream parse ----
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        await foreach (var evt in ParseSseStreamAsync(stream, cancellationToken))
        {
            yield return evt;
        }
    }

    // ------------------------------------------------------------------
    //  SSE parser
    //
    //  Spec subset (https://html.spec.whatwg.org/multipage/server-sent-events.html):
    //    • Lines are LF- or CRLF-terminated.
    //    • A field is "<name>: <value>". We only honour `event:` and `data:`.
    //    • An empty line dispatches the accumulated event.
    //    • Multiple data: lines join with '\n'.
    //    • Lines starting with ':' are comments — used by some servers as
    //      keep-alives (e.g. ": ping"). Ignored.
    // ------------------------------------------------------------------

    private async IAsyncEnumerable<QueryEvent> ParseSseStreamAsync(
        Stream stream,
        [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        using var reader = new StreamReader(stream, Encoding.UTF8);

        string? currentEvent = null;
        var dataBuilder = new StringBuilder();

        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();

            string? line = await reader.ReadLineAsync(cancellationToken);
            if (line is null)
            {
                // End of stream — flush a trailing buffered event if any.
                var trailing = TryBuildEvent(currentEvent, dataBuilder);
                if (trailing is not null) yield return trailing;
                yield break;
            }

            if (line.Length == 0)
            {
                // Empty line → dispatch
                var evt = TryBuildEvent(currentEvent, dataBuilder);
                if (evt is not null) yield return evt;

                currentEvent = null;
                dataBuilder.Clear();
                continue;
            }

            if (line[0] == ':')
                continue;  // SSE comment / keep-alive

            var colonIdx = line.IndexOf(':');
            if (colonIdx < 0)
                continue;  // malformed — skip

            var field = line[..colonIdx];
            // Per spec: if space follows colon, strip it.
            var valueStart = colonIdx + 1;
            if (valueStart < line.Length && line[valueStart] == ' ') valueStart++;
            var value = line[valueStart..];

            switch (field)
            {
                case "event":
                    currentEvent = value;
                    break;
                case "data":
                    if (dataBuilder.Length > 0) dataBuilder.Append('\n');
                    dataBuilder.Append(value);
                    break;
                // id / retry / others: not used by us, skip
            }
        }
    }

    private QueryEvent? TryBuildEvent(string? eventName, StringBuilder data)
    {
        if (eventName is null || data.Length == 0)
            return null;

        var json = data.ToString();
        try
        {
            return eventName switch
            {
                "sources" => DeserializeOrNull<SourcesPayload>(json) is { } p
                    ? new QueryEvent.Sources(p.Documents ?? Array.Empty<SourceDocument>())
                    : null,

                "token"   => DeserializeOrNull<TokenPayload>(json) is { } p
                    ? new QueryEvent.Token(p.Content ?? string.Empty)
                    : null,

                "done"    => DeserializeOrNull<DonePayload>(json) is { } p
                    ? new QueryEvent.Done(
                        p.FinishReason ?? "stop",
                        p.LatencyMs,
                        p.PromptTokens,
                        p.CompletionTokens)
                    : null,

                "error"   => DeserializeOrNull<ErrorPayload>(json) is { } p
                    ? new QueryEvent.Error(p.Message ?? "Unknown error.")
                    : null,

                _ => null,   // unknown event name — drop silently (forward-compat)
            };
        }
        catch (JsonException ex)
        {
            _logger.LogWarning(ex,
                "Malformed SSE payload for event '{Event}': {Json}",
                eventName, json);
            return null;
        }
    }

    private static T? DeserializeOrNull<T>(string json) where T : class
        => JsonSerializer.Deserialize<T>(json, JsonOpts);

    // ---- Payload DTOs for SSE data: { ... } envelopes ----
    // Internal because they're transport-only — never escape this file.

    private sealed record SourcesPayload(IReadOnlyList<SourceDocument>? Documents);
    private sealed record TokenPayload(string? Content);
    private sealed record DonePayload(
        string? FinishReason,
        long    LatencyMs,
        int?    PromptTokens,
        int?    CompletionTokens);
    private sealed record ErrorPayload(string? Message);
}
