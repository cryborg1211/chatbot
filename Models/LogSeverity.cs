namespace chatbot.Models;

/// <summary>
/// Severity of a <see cref="SystemLog"/> row. Stored as <c>tinyint</c>
/// in SQL — never renumber, the values are used in admin queries.
/// </summary>
public enum LogSeverity : byte
{
    Debug = 0,
    Info  = 1,
    Warn  = 2,
    Error = 3,
}
