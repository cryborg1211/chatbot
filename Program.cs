using chatbot.Data;
using chatbot.Hubs;
using chatbot.Infrastructure.AiWorker;
using chatbot.Infrastructure.Audit;
using chatbot.Infrastructure.Authorization;
using chatbot.Infrastructure.Identity;
using chatbot.Infrastructure.Storage;
using chatbot.Models;
using chatbot.Services.Ai;
using chatbot.Services.Chat;
using chatbot.Services.Documents;
using chatbot.Workers;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// ---------------------------------------------------------------------
//  1. Database (EF Core + SQL Server)
// ---------------------------------------------------------------------
var connectionString = builder.Configuration.GetConnectionString("DefaultConnection")
    ?? throw new InvalidOperationException(
        "Connection string 'DefaultConnection' not found in appsettings.json.");

builder.Services.AddDbContext<ApplicationDbContext>(options =>
    options.UseSqlServer(connectionString));

// ---------------------------------------------------------------------
//  2. ASP.NET Core Identity
// ---------------------------------------------------------------------
builder.Services
    .AddIdentity<ApplicationUser, IdentityRole>(options =>
    {
        // ---- Sign-in: relaxed for quick testing ----
        options.SignIn.RequireConfirmedEmail        = false;
        options.SignIn.RequireConfirmedAccount      = false;
        options.SignIn.RequireConfirmedPhoneNumber  = false;

        // ---- Password rules (dev defaults) ----
        options.Password.RequiredLength         = 6;
        options.Password.RequireDigit           = true;
        options.Password.RequireLowercase       = false;
        options.Password.RequireUppercase       = false;
        options.Password.RequireNonAlphanumeric = false;

        // ---- User ----
        options.User.RequireUniqueEmail = true;
    })
    .AddEntityFrameworkStores<ApplicationDbContext>()
    .AddDefaultTokenProviders();

// ---------------------------------------------------------------------
//  3. Claims transformation
//     Inject DepartmentId into the auth cookie at sign-in so the RAG
//     pipeline can filter vectors by tenant without an extra DB hit.
// ---------------------------------------------------------------------
builder.Services.AddScoped<
    IUserClaimsPrincipalFactory<ApplicationUser>,
    ApplicationUserClaimsPrincipalFactory>();

// ---------------------------------------------------------------------
//  4. Auth cookie paths (point at the existing Razor pages)
// ---------------------------------------------------------------------
builder.Services.ConfigureApplicationCookie(options =>
{
    options.LoginPath        = "/Account/Login";
    options.LogoutPath       = "/Account/Logout";
    options.AccessDeniedPath = "/Account/AccessDenied";
    options.ExpireTimeSpan   = TimeSpan.FromHours(8);
    options.SlidingExpiration = true;
});

// ---------------------------------------------------------------------
//  5. Storage (blob backend for uploaded documents)
// ---------------------------------------------------------------------
builder.Services.Configure<StorageOptions>(
    builder.Configuration.GetSection(StorageOptions.SectionName));
builder.Services.AddSingleton<IDocumentStorage, LocalFileSystemStorage>();

// ---------------------------------------------------------------------
//  6. AI Worker (typed HttpClient → Python FastAPI)
// ---------------------------------------------------------------------
builder.Services.Configure<AiWorkerOptions>(
    builder.Configuration.GetSection(AiWorkerOptions.SectionName));
builder.Services.AddHttpClient<IAiWorkerClient, AiWorkerClient>();

// ---------------------------------------------------------------------
//  7. Document orchestrator + background ingestion worker
// ---------------------------------------------------------------------
builder.Services.AddScoped<IDocumentService, DocumentService>();
builder.Services.AddScoped<IChatService,     ChatService>();
builder.Services.AddScoped<IAiConfigService, AiConfigService>();
builder.Services.AddScoped<IProviderKeyService, ProviderKeyService>();
builder.Services.AddDataProtection();
builder.Services.AddHostedService<DocumentIngestionWorker>();

// ---------------------------------------------------------------------
//  8. Audit (see audit_system.md §5)
// ---------------------------------------------------------------------
builder.Services.AddHttpContextAccessor();
builder.Services.AddScoped<IAuditLogger, AuditLogger>();

// ---------------------------------------------------------------------
//  Authorization policies + role seeder
// ---------------------------------------------------------------------
builder.Services.AddAuthorization(options =>
{
    options.AddPolicy(AuthorizationPolicies.RequireAdmin,
        policy => policy.RequireRole(Roles.Admin));
});
builder.Services.AddHostedService<RoleSeeder>();

// ---------------------------------------------------------------------
//  9. MVC + Razor Pages
// ---------------------------------------------------------------------
builder.Services.AddRazorPages();
builder.Services.AddControllersWithViews();
builder.Services.AddSignalR();

var app = builder.Build();

// ---------------------------------------------------------------------
//  Pipeline
// ---------------------------------------------------------------------
if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error");
    app.UseHsts();
    app.UseHttpsRedirection();
}

app.UseStaticFiles();
app.UseRouting();

app.UseAuthentication();   // MUST be before UseAuthorization
app.UseAuthorization();

app.MapRazorPages();
app.MapControllers();              // attribute-routed APIs (e.g. /api/documents)
app.MapDefaultControllerRoute();
app.MapHub<DocumentHub>("/hubs/document");

// ---------------------------------------------------------------------
//  Lifecycle audit (system.startup / system.shutdown)
// ---------------------------------------------------------------------
app.Lifetime.ApplicationStarted.Register(() =>
{
    _ = Task.Run(async () =>
    {
        using var scope = app.Services.CreateScope();
        var audit = scope.ServiceProvider.GetRequiredService<IAuditLogger>();
        await audit.LogAsync("system.startup", "sys",
            details: new { version = "0.1.0" });
    });
});

app.Lifetime.ApplicationStopped.Register(() =>
{
    using var scope = app.Services.CreateScope();
    var audit = scope.ServiceProvider.GetRequiredService<IAuditLogger>();
    audit.LogAsync("system.shutdown", "sys").GetAwaiter().GetResult();
});

app.Run();
