# PLAN: Unified Admin Page (`/admin`)

**Type:** SIMPLE
**Created:** 2026-06-10
**Status:** ACTIVE

---

## Goal

Create a new `/admin` Razor Page that unifies the Chat sidebar (real conversation history) with the Admin Users dashboard (stats + user table + actions). This is the admin-specific entry point. Existing `/Chat` and `/Admin/Users` pages remain untouched.

## Approach

**Standalone page** (`Layout = null`) that combines:
- Chat page model data (sidebar conversations, user info)
- Users page model data (stats, filters, user table, departments)
- Users POST handlers (Approve/Reject/Activate) duplicated in the new page model

No shared partials or ViewComponents -- KISS for solo dev project.

## Sidebar Structure (left panel, w-64)

```
+-----------------------------+
| Logo: LD3 AI Chatbot        |
+-----------------------------+
| [+ Cuoc tro chuyen moi]    |  --> links to /Chat (new conversation)
+-----------------------------+
| QUAN TRI (admin nav)        |
|  * Quan ly nguoi dung       |  --> /admin (self, active state)
|  * Phan hoi & Danh gia      |  --> /Admin/Feedback
|  * Nhat ky he thong          |  --> /Admin/Logs
+-----------------------------+
| --- divider ---             |
| GAN DAY (recent chats)      |
|  * [real conversation 1]    |  --> /Chat?id=xxx
|  * [real conversation 2]    |
|  * ...                       |
+-----------------------------+
| Cai dat he thong            |
| Dang xuat                    |
+-----------------------------+
```

## Route and Authorization

- Route: `@page "/admin"` (lowercase, clean URL)
- Auth: `[Authorize(Policy = AuthorizationPolicies.RequireAdmin)]`
- Namespace: `chatbot.Pages.Admin`

## File Changes

### 1. NEW: `Pages/Admin/Index.cshtml.cs`

Page model class `AdminIndexModel` with:

**Injected services:**
- `ApplicationDbContext` (conversations, departments)
- `UserManager<ApplicationUser>` (user queries + approve/reject/activate)
- `IAuditLogger` (audit logging for user actions)

**Properties (from Chat page model):**
- `FullName` (string, from claims)
- `RecentConversations` (IReadOnlyList<ConversationSidebarItem>, top 20, current user's)

**Properties (from Users page model):**
- `Search`, `DepartmentId`, `PageNumber` (bound from query string)
- `Items` (IList<UserRow>), `Departments` (IList<Department>)
- `TotalUsers`, `ActiveCount`, `PendingCount`, `LockedCount`, `TotalCount`, `TotalPages`

**OnGetAsync:**
1. Read user claims (userId, FullName)
2. Load recent conversations (same query as Chat IndexModel)
3. Load departments
4. Compute user stats (same as UsersModel)
5. Apply search/department filters
6. Paginate and build Items list

**POST handlers** (copy from UsersModel -- same logic):
- `OnPostApproveAsync(string id)`
- `OnPostRejectAsync(string id)`
- `OnPostActivateAsync(string id)`

Each redirects back to the `/admin` page preserving Search/DepartmentId/PageNumber query params.

**Reuse existing types:** `ConversationSidebarItem` (from Chat namespace), `UserRow`, `UserStatus` (from Admin namespace). No new DTOs needed.

### 2. NEW: `Pages/Admin/Index.cshtml`

Standalone HTML (`Layout = null`) with Tailwind CDN + Font Awesome.

**Structure:**
- `<html>` with full `<head>` (same CDN links as Chat page)
- `<body>` with flex layout: sidebar (left) + main area (right)

**Sidebar** (w-64, matches _Layout width):
- Logo header (same as _Layout)
- "New Chat" button linking to `/Chat`
- Admin nav section with active state for "Quan ly nguoi dung"
- Divider
- Recent conversations section (real DB data, links to `/Chat?id=xxx`)
- Bottom: Settings + Logout form

**Main area:**
- Top header bar with search + profile info (use real FullName from model, not hardcoded)
- Page content: exact same HTML as Users.cshtml (stats cards + filter form + user table + pagination)
- Footer

**Key differences from _Layout:**
- Uses real conversation data, not hardcoded placeholders
- Active nav highlights "Quan ly nguoi dung" by default
- Profile area shows real FullName

### 3. UPDATE: `Pages/Chat/Index.cshtml` (line 33)

Change the admin link from `/Admin/Users` to `/admin`:
```razor
<!-- Before -->
<a href="/Admin/Users" ...>Trang quan tri</a>

<!-- After -->
<a href="/admin" ...>Trang quan tri</a>
```

This makes the Chat page's "Trang quan tri" button send admins to the new unified page.

### 4. UPDATE: `Pages/Shared/_Layout.cshtml` (sidebar section)

Replace the hardcoded fake "Gan day" chat items (lines 42-58) with real conversation data.

This requires _Layout to have access to conversation data. Two sub-options:
- **Option A (simpler):** Use a ViewComponent `RecentChatsViewComponent` that queries DB
- **Option B (simpler still):** Just link the "New Chat" button to `/Chat` and remove the fake recent chats section entirely from _Layout, since _Layout is for admin sub-pages (Users/Feedback/Logs) that are secondary now

**Decision: Option B.** Remove fake chat items from _Layout. The _Layout sidebar keeps admin nav + New Chat button (links to /Chat) + Settings/Logout. The unified `/admin` page is the primary admin experience with real chat history. Existing sub-pages (/Admin/Feedback, /Admin/Logs) keep working with their simplified _Layout sidebar.

Changes to _Layout:
- Remove lines 42-58 (the hardcoded chat links)
- Keep the "Gan day" label but show a link "Xem tat ca" pointing to /Chat instead
- OR simply remove the entire "Gan day" section and keep it clean

## Touchpoints

- `Pages/Admin/Index.cshtml` (NEW)
- `Pages/Admin/Index.cshtml.cs` (NEW)
- `Pages/Chat/Index.cshtml` (UPDATE - admin link)
- `Pages/Shared/_Layout.cshtml` (UPDATE - remove fake chats)

## Public Contracts

- New route: `GET /admin` (admin-only, returns unified page)
- New POST handlers: `/admin?handler=Approve&id=xxx`, `?handler=Reject&id=xxx`, `?handler=Activate&id=xxx`
- All existing routes unchanged

## Blast Radius

- LOW. Two new files, two minor edits.
- Existing `/Admin/Users`, `/Admin/Feedback`, `/Admin/Logs` pages unaffected (they still use _Layout).
- Existing `/Chat` page unaffected (only admin link URL changes).
- No model/DB/service changes.

## Verification Evidence

1. Navigate to `/admin` as admin user -- see sidebar with admin nav + real chat history + Users dashboard
2. Navigate to `/admin` as regular user -- get 403/redirect
3. Test Approve/Reject/Activate buttons on `/admin` -- same behavior as `/Admin/Users`
4. Navigate to `/Chat` as admin -- "Trang quan tri" button goes to `/admin`
5. Navigate to `/Admin/Feedback` and `/Admin/Logs` -- still work normally with _Layout
6. _Layout no longer shows hardcoded fake chat items

## Resume and Execution Handoff

**Start with:** `Pages/Admin/Index.cshtml.cs` (page model) -- this is the data foundation.
**Then:** `Pages/Admin/Index.cshtml` (the HTML/Razor view).
**Then:** Minor edits to Chat/Index.cshtml and _Layout.cshtml.
**Test after each file** by running the app and navigating to `/admin`.
