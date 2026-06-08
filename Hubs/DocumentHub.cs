using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;

namespace chatbot.Hubs;

[Authorize]
public sealed class DocumentHub : Hub
{
    // No server-bound methods — clients only subscribe.
}
