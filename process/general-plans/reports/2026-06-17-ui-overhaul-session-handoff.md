# Session Handoff — UI Overhaul (Navbar / Profile / Dark Mode / Logo)

Date: 2026-06-17
Branch: `main` (all work UNCOMMITTED)
Scope: ASP.NET Core Razor Pages frontend + small worker tweak.

> **Resume in a new chat:** "Read `process/general-plans/reports/2026-06-17-ui-overhaul-session-handoff.md` and continue with the deferred tasks."

---

## ✅ Done this session

1. **Embedder → CPU** — `worker/app/services/embedder.py`: removed `device="cuda"` + `embed_batch_size`, kept only `model_name`. Removed the `PYTORCH_CUDA_ALLOC_CONF` env line. Runs on CPU now.
2. **Profile dropdown** in top-right navbar (name + role badge, Đổi ảnh đại diện, Đổi mật khẩu, theme toggle, divider, red Đăng xuất). On every page.
3. **Change Password** — `Pages/Account/ChangePassword.cshtml(.cs)` (Identity `ChangePasswordAsync` + `RefreshSignInAsync` + audit).
4. **Change Avatar** — `Pages/Account/Avatar.cshtml(.cs)`. Upload/replace/remove, validation (≤2 MB, png/jpg/webp/gif, ext from content-type), live preview.
   - Added `ApplicationUser.AvatarPath` (nullable), `AppClaimTypes.AvatarPath` ("avatar_path") claim, factory wiring. **EF migration `AddUserAvatarPath` was created AND applied to the dev DB.**
   - Avatars saved to `wwwroot/uploads/avatars/` (served by static files).
5. **Dark mode** — class-based (`localStorage 'ld3-theme'`, `tailwind.config.darkMode='class'`, pre-paint script) across shell + Chat (incl. `wwwroot/js/chat.js` streamed bubbles) + all Admin pages + Account pages.
6. **DRY refactor** — created **`Pages/Shared/_NavbarPartial.cshtml`** (dropdown + JS, self-computes name/role/avatar from claims) and **`Pages/Shared/_DarkModeHead.cshtml`** (pre-paint script). Wired into `_Layout`, `Chat/Index`, `Admin/Index`, `Admin/Documents`. Duplication removed.
7. **Feedback/Logs fixes** — themed the `_Layout` sidebar (was the grey-buttons mismatch vs `/admin`), lowercased nav routing to `/admin`, `/admin/feedback`, `/admin/logs`, `/admin/documents`; themed pagination buttons across all 4 admin pages.
8. **Cleanup batch** — removed: login social icons (+ Register), sidebar "Cài đặt" buttons, sidebar sign-out (kept in dropdown), 4 chat mock suggestion chips, "Quản trị" sidebar label in `/admin`. Unified navbar height (Chat/Documents `h-14`→`h-16`). All logos route to `/Chat`. Logs filter → Vietnamese ("Tất cả danh mục", "ví dụ:").
9. **Logo** — optimized `logo/IDT.png` (664×664, 950 KB) → **`wwwroot/logo/IDT.webp` (160×160, 15.6 KB, q90)**. All 6 navbar refs + Login card hero now use it (`h-10` navbar, `h-16` hero). Old `wwwroot/logo/IDT.png` deleted; repo-root `logo/IDT.png` kept as source.
10. **dotnet watch fix** — `chatbot.csproj` got `<Watch Remove="wwwroot\uploads\**\*" />`. Hot Reload was crashing (`HotReloadMSBuildWorkspace`) when the app wrote an uploaded avatar into the watched tree.
11. **Logs doc-id → filename** — `Logs.cshtml.cs` now joins `SystemLogs.ResourceId` (where `ResourceType == "Document"`) → `Documents.OriginalFileName` (batched per page; deleted docs fall back to UUID). View shows filename + file icon, raw UUID on hover (`title`).

Build: **clean (0 errors)** at last check. code-review-graph synced.

---

## ⏳ Deferred / remaining (NOT done)

- **API-key/model dashboard** (the big one): new admin page, reached from a new profile-menu item. Read/update the AI worker API key, **test connectivity (usable y/n)**, switch model, toggle local Ollama. Touches `Infrastructure/AiWorker/AiWorkerOptions` + worker config.
- **Notification button** logic: bell currently disabled. Should push: new user → `/admin`; user like/dislike → `/admin/feedback`. Needs a notifications/count data source.
- **Log file open/download**: the Logs name-mapping is done; the open/download action for admins is still TODO (needs a download endpoint — note `DocumentsController` has CRUD; the Documents page download button is also still a `#` placeholder).
- **User-owned**: the **"Từ chối" / "Khoá"** action buttons (Users + `/admin`) are still light-mode in dark — user said they'd do these.
- Register page: terms/privacy/support **text** links still present (only social icons were removed).
- (Optional) Move avatar storage **out of `wwwroot`** and serve via an auth'd endpoint (like documents use `App_Data` via `IDocumentStorage`) — removes the watch/publish smell that needed the `Watch Remove` workaround.
- (Optional) `/admin` (Admin/Index) duplicates the whole shell + content of `Admin/Users` — candidate for consolidation onto `_Layout`.

---

## ⚠️ Current state / must-do
- **Restart `dotnet watch` / the app** to see all changes (Razor is build-time compiled here; csproj change also needs a fresh start).
- **Nothing is committed.** Working tree is dirty with all of the above.
- Migration already applied — do NOT re-run; just `dotnet ef database update` is a no-op.

## Conventions / gotchas (for the next session)
- Caveman mode for replies; update code-review-graph after editing source (`build_or_update_graph_tool`).
- Standalone pages (`Layout = null`, own `<head>`): `Chat/Index`, `Admin/Index`, `Admin/Documents`, all `Account/*`. Each needs `<partial name="_DarkModeHead" />` in `<head>` and (where a navbar exists) `<partial name="_NavbarPartial" />`.
- `_Layout` is used by: `Admin/Users`, `Admin/Feedback`, `Admin/Logs`.
- `<img src="~/logo/IDT.webp">` resolves via the URL tag helper (active through `Pages/_ViewImports.cshtml`).
- Pillow (`python` has Pillow 12.2) + ffmpeg available locally for any image work. ImageMagick is NOT installed.
