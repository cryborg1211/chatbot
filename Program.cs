using chatbot.Data;
using chatbot.Infrastructure.Identity;
using chatbot.Models;
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
//  5. MVC + Razor Pages
// ---------------------------------------------------------------------
builder.Services.AddRazorPages();
builder.Services.AddControllersWithViews();

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
app.MapDefaultControllerRoute();

app.Run();
