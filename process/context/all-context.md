# LD3 AI Chatbot - All Context

Last updated: 2026-06-09

This file is the root context entrypoint for the repo.

Use it for two things:

1. quick routing to the right context pack or root file
2. broad architecture and repository understanding

Start here before loading deeper context files.

---

## How This File Works (the `all-*.md` Convention)

Every `process/context/` directory has one `all-*.md` entrypoint that acts as an attachable quick router for that domain. This root file (`all-context.md`) is the top-level router. Context groups each have their own `all-{group}.md` entrypoint.

**The pattern:**

```
process/context/
  all-context.md                      <-- THIS FILE: root router
  planning/
    all-planning.md                   <-- group router for planning
  tests/
    all-tests.md                      <-- group router for tests
```

**How agents use it:**

1. Agent reads `all-context.md` first (this file)
2. Finds the relevant context group from the routing tables below
3. Reads that group's `all-{group}.md` entrypoint
4. Only then loads the specific deep doc needed

This layered routing keeps context windows small. Never load the whole `process/context/` tree.

**What each `all-{group}.md` must contain:**

- Scope (what the group covers and does NOT cover)
- Read-when rules (when an agent should load this group)
- Quick procedures or decision rules
- Source paths (list of deeper docs in the group)
- Update triggers (when to refresh this group's content)
- Routing to deeper docs within the group

---

## Quick Start

For most substantial tasks:

1. read this file first
2. choose the smallest relevant root file or context group from the tables below
3. only then load deeper files

---

## Current Root Entry Points

| File | Read when |
|---|---|
| `process/context/all-context.md` | any substantial planning, research, review, or implementation task |
| `process/context/tests/all-tests.md` | testing, verification, debugging test failures, execution planning |
| `process/context/planning/all-planning.md` | plan-shape calibration, planning examples, SIMPLE vs COMPLEX reference docs |

## Current Context Groups

| Group | Entry point | Scope |
|---|---|---|
| `planning/` | `process/context/planning/all-planning.md` | plan-shape calibration, planning examples, SIMPLE vs COMPLEX reference docs |
| `tests/` | `process/context/tests/all-tests.md` | test runners, commands, debugging, gaps |

## Task Routing Table

| If the task involves... | Start with |
|---|---|
| architecture or stack questions | this file |
| testing or verification | `process/context/tests/all-tests.md` |
| creating a new plan | `process/context/planning/all-planning.md` |
| C# backend changes (controllers, services, identity) | this file + relevant source under `Controllers/`, `Services/`, `Infrastructure/` |
| Python worker changes (RAG pipeline, chunking, embedding) | this file + relevant source under `worker/app/` |
| database/EF Core changes | this file + `data/ApplicationDbContext.cs` + `Migrations/` |
| UI/Razor Pages changes | this file + relevant source under `Pages/`, `wwwroot/` |
| context maintenance | `all-context.md` then run `audit-context` after edits |

## Context Group Lifecycle

Context groups are durable knowledge domains, not feature folders.

Create a group when:

- a topic has 3+ durable docs
- a single doc exceeds roughly 800 lines with separable subtopics
- multiple agents repeatedly need only one slice of a large context file
- the topic maps to a stable operational domain (tests, infra, database, auth, UI, workflows, etc.)

Do not create a group when:

- the content is a temporary report
- the content is a plan or execution artifact
- the topic is feature-specific and belongs in `process/features/...`

Move or split one group at a time. Use `all-{group}.md` entrypoints. Run the `audit-context` skill after every context organization change.

## Naming Convention

There are no `README.md` files inside `process/context/`.

Canonical entrypoints use `all-*.md`:

- root: `process/context/all-context.md`
- group: `process/context/{group}/all-{group}.md`

Each `all-{group}.md` file should act as the attachable quick router for that domain:

- tell the agent what the group covers
- give quick procedures and decision rules
- route to smaller deeper files

## Context Update Protocol

When durable project knowledge changes:

1. update the smallest relevant context file
2. update this file if routing, ownership, naming, or groups changed
3. update the owning `all-{group}.md` entrypoint when a group exists
4. run `audit-context`

---

## Project Overview

**LD3 AI Chatbot** is a specialized RAG chatbot built for the Trung Tam Doi Moi Sang Tao va Chuyen Doi So (Innovation & Digital Transformation Center) in Lam Dong province, Vietnam. Internal directors and state officials use it to instantly look up budgets, technical requirements, and target applications from messy Vietnamese government administrative documents (.doc/.docx/.pdf) — investment proposals, lab equipment specs, and regulatory filings.

**Solo developer project.** No team, no CI/CD pipeline. Manual deployment. Currently running on developer laptop, server deployment planned later.

---

## Repository Structure

```
chatbot/
  Controllers/
    Api/
      ChatController.cs       -- SSE streaming RAG chat endpoint
      DocumentsController.cs   -- document CRUD API
      FeedbackController.cs    -- user feedback API
  data/
    ApplicationDbContext.cs     -- EF Core DbContext (Identity + app tables)
    lamdong_docs/              -- sample .doc/.docx test documents (Vietnamese govt)
    lamdong_pdf/               -- sample .pdf test documents (Vietnamese govt)
  Hubs/
    DocumentHub.cs             -- SignalR hub for real-time document status updates
  Infrastructure/
    AiWorker/
      AiWorkerClient.cs        -- typed HttpClient to Python worker (SSE streaming)
      AiWorkerOptions.cs       -- config POCO (BaseUrl, ApiKey, Timeout)
      Contracts/               -- IngestRequest/Result, QueryRequest/Event DTOs
      IAiWorkerClient.cs       -- interface
    Audit/
      AuditLogger.cs           -- system audit logging to SystemLogs table
      IAuditLogger.cs
    Authorization/
      AuthorizationPolicies.cs -- RequireAdmin policy
      RoleSeeder.cs            -- seeds Admin/User roles on startup
      Roles.cs                 -- role constants
    Identity/
      AppClaimTypes.cs         -- custom claim: DepartmentId
      ApplicationUserClaimsPrincipalFactory.cs  -- injects DepartmentId into auth cookie
    Storage/
      IDocumentStorage.cs      -- blob storage interface
      LocalFileSystemStorage.cs -- local filesystem impl (App_Data/uploads/)
      StorageOptions.cs
  Migrations/                  -- 4 EF Core migrations (Identity, Documents, Chat, SystemLog)
  Models/
    ApplicationUser.cs         -- Identity user + FullName, DepartmentId
    ChatMessage.cs             -- message in a conversation (User/Assistant/System)
    ChatRole.cs                -- enum: User, Assistant, System
    Conversation.cs            -- chat conversation (per user)
    Department.cs              -- tenant/org unit
    Document.cs                -- uploaded document metadata
    DocumentLimits.cs          -- upload size/count limits
    DocumentStatus.cs          -- enum: Pending, Processing, Ready, Failed
    Feedback.cs                -- per-message feedback (thumbs up/down + comment)
    FeedbackRating.cs          -- enum: ThumbsUp, ThumbsDown
    LogSeverity.cs             -- enum: Info, Warning, Error
    SystemLog.cs               -- audit log entry
  Pages/
    Account/Login, Register    -- Razor Pages for auth
    Admin/Documents, Users, Feedback, Logs  -- admin dashboard pages
    Chat/Index                 -- main chat UI
    Shared/_Layout.cshtml      -- shared layout
  Services/
    Chat/
      ChatService.cs           -- conversation management + stream relay to worker
      IChatService.cs
    Documents/
      DocumentService.cs       -- document upload orchestration
      IDocumentService.cs
  Workers/
    DocumentIngestionWorker.cs -- background service polling Pending docs for ingestion
  worker/                      -- Python FastAPI AI worker (separate process)
    app/
      main.py                  -- FastAPI entry point + lifespan boot sequence
      config.py                -- pydantic-settings (env-based config)
      auth.py                  -- X-Worker-Api-Key validation
      queue_worker.py          -- arq Redis-backed async job worker
      api/
        health.py              -- /health endpoint
        ingest.py              -- /api/ingest (document → embed → store)
        query.py               -- /api/query (RAG chat via SSE streaming)
        documents.py           -- /api/documents/upload, /api/documents/delete
      schemas/
        ingest.py              -- Pydantic models for ingest
        query.py               -- Pydantic models for query
      services/
        chunker.py             -- Hybrid chunker: HierarchicalChunker primary + Markdown-aware oversized-chunk post-processor (SentenceSplitter fallback for prose)
        embedder.py            -- BAAI/bge-m3 singleton embedder
        llm_router.py          -- Ollama LLM with enforced anti-hallucination system prompt
        loader.py              -- Docling-based document loader (PDF, DOCX, legacy DOC)
        prompt_builder.py      -- Jinja2 RAG system prompt renderer
        retriever.py           -- tenant-filtered Qdrant vector search
        vectorstore.py         -- Qdrant collection management
        preprocessing/
          docx_processor.py    -- LibreOffice .doc → .docx conversion
        chunk_metadata.py      -- metadata enrichment for chunks
  wwwroot/
    js/chat.js                 -- browser-side chat UI (SSE consumer)
  Program.cs                   -- ASP.NET Core startup + DI registration
  appsettings.json             -- connection strings, AI worker config
  process/                     -- agent harness operational workspace
  docker/                      -- (empty, future containerization)
  src/
    crawler.py                 -- data collection script
    eda.py                     -- exploratory data analysis script
```

## Technology Stack

**Backend (.NET):**
- **Framework:** ASP.NET Core (.NET 10) with Razor Pages + Web API
- **Language:** C# 13, nullable reference types enabled, implicit usings
- **ORM:** Entity Framework Core 10.0 (SQL Server provider)
- **Auth:** ASP.NET Core Identity with custom claims (DepartmentId baked into cookie)
- **Real-time:** SignalR (`/hubs/document` for document status updates)
- **Streaming:** Server-Sent Events (SSE) for chat responses (ChatController → browser)

**AI Worker (Python):**
- **Framework:** FastAPI 0.115+ with uvicorn
- **RAG Engine:** LlamaIndex Core 0.11+
- **Document Parsing:** IBM Docling 2.0+ (PDF/DOCX → Markdown with table structure)
- **Embedding Model:** BAAI/bge-m3 (1024-dim dense vectors) via sentence-transformers / HuggingFace
- **Vector DB:** Qdrant 1.11+ (local instance, collection: `ld3_knowledge`)
- **LLM:** Ollama (local) — default model: Qwen 2.5:3b (configurable, API-selectable planned)
- **Chunking:** Docling `HierarchicalChunker` as primary splitter (table-aware); custom Markdown-aware post-processor re-attaches column headers to continuation chunks; `SentenceSplitter` fallback for oversized prose. `DoclingResult` dataclass bridges loader → chunker boundary.
- **Prompt Templates:** Jinja2-based RAG system prompt
- **Job Queue:** arq (Redis-backed async worker for document upload processing)
- **Config:** pydantic-settings (env-based, `.env` file)
- **Python version:** 3.11+

**Database:**
- **Primary:** SQL Server (local SQLEXPRESS) via EF Core
- **Vector Store:** Qdrant (local, HTTP port 6333)
- **Cache/Queue:** Redis (for arq job queue)

**Frontend:**
- Server-rendered Razor Pages (no SPA framework)
- Vanilla JavaScript (`wwwroot/js/chat.js`) consuming SSE stream
- No CSS framework detected (likely custom or Bootstrap via layout)

**Infrastructure:**
- Local development (developer laptop)
- No CI/CD pipeline
- No Docker containers yet (`docker/` exists but empty)
- Manual deployment

## Key Patterns and Conventions

**Multi-tenant isolation:** Every query is scoped by `DepartmentId`. The claim is injected into the auth cookie at sign-in via `ApplicationUserClaimsPrincipalFactory`. The Python retriever enforces tenant filtering on every Qdrant query — never returns cross-department data.

**SSE streaming pipeline:** Browser → `ChatController` (SSE) → `ChatService` → `AiWorkerClient` (HTTP SSE to Python) → FastAPI `/api/query` → Embedder → Retriever → PromptBuilder → LlmRouter (Ollama streaming). Events: `sources`, `token` (N times), `done` or `error`.

**Anti-hallucination system prompt:** `LlmRouter.ENFORCED_SYSTEM_PROMPT` is always injected at index 0 of every conversation. It forces the LLM to read tables row-by-row, extract before calculating, never invent numbers, and state when data is missing.

**Document ingestion flow:** Upload via .NET API → stored to `App_Data/uploads/` → `DocumentIngestionWorker` (background service) polls Pending docs → sends to Python `/api/ingest` → Docling parses to Markdown + returns `DoclingResult` → `HierarchicalChunker` primary split → Markdown-aware oversized-chunk post-processing (re-attaches table headers) → bge-m3 embeds → Qdrant stores. SignalR pushes status updates to browser. Plain-text (`.txt`) path still uses `SentenceSplitter` directly.

**Legacy .doc support:** Binary `.doc` files are converted to `.docx` via LibreOffice headless (`convert_doc_to_docx`), then processed through Docling like normal DOCX.

**Naming conventions:**
- C#: PascalCase types/methods, camelCase locals, `I`-prefixed interfaces
- Python: snake_case everywhere, PascalCase for Pydantic models and classes
- JSON wire format: snake_case (C# uses `JsonNamingPolicy.SnakeCaseLower`)
- Files: C# PascalCase `.cs`, Python snake_case `.py`
- Comments currently in English; may switch to Vietnamese later. Function/variable names stay English.

**Error handling:**
- C#: exceptions with typed error classes (`AiWorkerException`), structured logging
- Python: `LoaderError` for parse failures (→ HTTP 422), broad try/except with SSE error events for stream failures
- Chat stream always persists partial replies in `finally` block, even on cancellation

**Configuration:**
- .NET: `appsettings.json` / `appsettings.Development.json` + options pattern (`IOptions<T>`)
- Python: pydantic-settings (`Settings` class) from `.env` file

## Environment and Configuration

**C# (.NET) config files:** `appsettings.json`, `appsettings.Development.json`

**Env var groups (names only, never values):**
- Database: `ConnectionStrings:DefaultConnection` (SQL Server)
- AI Worker: `AiWorker:BaseUrl`, `AiWorker:ApiKey`, `AiWorker:TimeoutSeconds`
- Storage: `Storage:UploadsRoot`

**Python worker config (via pydantic-settings / `.env`):**
- Inter-service auth: `WORKER_API_KEY`
- Qdrant: `QDRANT_URL`, `QDRANT_API_KEY`, `COLLECTION_NAME`, `VECTOR_SIZE`
- Embedding: `EMBED_MODEL` (default: `BAAI/bge-m3`)
- Chunking: `CHUNK_SIZE` (default: 1024), `CHUNK_OVERLAP` (default: 250)
- LLM: `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (default: `gemma2:2b`), `OLLAMA_TIMEOUT`, `OLLAMA_TEMPERATURE`
- Retrieval: `RETRIEVAL_TOP_K` (default: 12)
- Redis: `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`
- Logging: `LOG_LEVEL`

**Key service locations:**
- .NET backend: `http://localhost:5000` (or Kestrel default)
- Python worker: `http://localhost:8001/api` (configured in `AiWorker:BaseUrl`)
- Qdrant: `http://localhost:6333`
- Ollama: `http://localhost:11434`
- Redis: `localhost:6379`
- SQL Server: `localhost\SQLEXPRESS`

## Database Schema (EF Core)

**Tables:**
- `AspNetUsers` (Identity) + custom fields: `FullName`, `DepartmentId` (FK → Departments)
- `AspNetRoles`, `AspNetUserRoles`, etc. (Identity standard)
- `Departments` — tenant/org unit (Id: string PK max 20, Name unique). Seeded: IT, HR, ADMIN
- `Documents` — uploaded doc metadata (Status: Pending/Processing/Ready/Failed, DepartmentId FK)
- `Conversations` — per-user chat sessions (Title auto-generated from first message)
- `ChatMessages` — message timeline (Role: User/Assistant/System, Content nvarchar(max), SourceDocumentIdsJson)
- `Feedbacks` — per-message thumbs up/down + comment (unique per user+message)
- `SystemLogs` — audit trail (Action, Category, Severity, UserId, DepartmentId, Details JSON)

**Key indexes:** tenant+status on Documents, userId+updatedAt on Conversations, conversationId+createdAt on ChatMessages, timestamp descending on SystemLogs.

## Agent Preferences

**Token efficiency:** Always use **caveman mode** for responses. The `/caveman` skill is installed — use compressed communication to minimize token output while keeping full technical accuracy.

**Code review:** Always use the **code-review-graph** MCP server for code reviews and codebase understanding. Tools available under `mcp__code-review-graph__*` — build/update the graph, detect changes, get review context, find affected flows, semantic search, etc. Prefer graph-based context over full file reads when reviewing or navigating code. **Always update the code-review-graph after editing any source file** — run `code-review-graph update` in the terminal so the graph stays in sync with the codebase.

## Current Active Work

**Hybrid chunker shipped (2026-06-09).** The `HierarchicalChunker` + Markdown-aware post-processor replaced the old `SentenceSplitter`-only chunker. All documents re-ingested. No active blocking problem.

**Known issue (low priority):** `code-review-graph` pre-commit hook fails with `UnicodeEncodeError` (cp1252 codec) on Windows when changed files contain Vietnamese text. Non-blocking — commit still succeeds. Tracked in `process/general-plans/backlog/code-review-graph-unicode_BACKLOG.md`.

## Current Features

No feature folders created yet. Potential future feature areas:
- `document-ingestion` — parsing pipeline, chunking, Docling integration
- `chat-rag` — query pipeline, retrieval, LLM routing, streaming
- `admin-dashboard` — user/doc/feedback/log management pages

## Scan Metadata

- Generated: 2026-06-09
- Last updated: 2026-06-09 (hybrid-chunker implementation complete)
- HEAD: 77ea6ad (main) — hybrid chunker + Markdown post-processor shipped
- Mode: incremental update
- Package manager: dotnet (C#) + pip/hatch (Python worker)
