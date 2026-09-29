# SPIDEY — Personal AI Agent

SPIDEY is a personal AI assistant built as an **AI operating layer**, not a chat app
with a fancy interface. The LLM reasons, memory personalizes, RAG supplies knowledge,
tools supply capabilities, the workflow engine executes, the UI makes execution visible,
and the database persists it all.

Core loop:

```
USER → SPIDEY → UNDERSTAND → REMEMBER → PLAN → USE TOOLS → EXECUTE → VERIFY → RESPOND → REMEMBER
```

Architecture:

```
             ┌──────────────┐
             │     USER     │
             └──────┬───────┘
                    ↓
             ┌──────────────┐
             │    SPIDEY    │
             │ ORCHESTRATOR │
             └──────┬───────┘
                    ↓
          ┌─────────┴─────────┐
          ↓                   ↓
      🧠 MEMORY             📚 RAG
          ↓                   ↓
          └─────────┬─────────┘
                    ↓
                🧠 PLANNER
                    ↓
             ┌──────┴──────┐
             ↓             ↓
          TOOLS         WORKFLOWS
             ↓             ↓
             └──────┬──────┘
                    ↓
                VERIFY
                    ↓
                 RESULT
                    ↓
               SAVE MEMORY
                    ↓
                 USER
```

## Phase 1 — Core (implemented)

- FastAPI backend + React + Vite + TypeScript + Tailwind frontend
- Basic chat with Server-Sent Events (SSE) live updates
- AI provider abstraction (`rule_based` default; `openai`, `ollama` optional)
- `SpideyAgent`: intent classification → memory retrieval → plan → tool
  execution → verification → response → memory update (`MAX_AGENT_STEPS=9`;
  the Phase 4 resume pipeline uses 9 visible stages)
- Tool system with common interface: `calculator`, `memory`, `tasks`
- Workflow engine: every agent run is a `WorkflowRun` of `WorkflowStep`s
  (WAITING / RUNNING / COMPLETED / FAILED) with timing, visible live in the UI
- PostgreSQL + SQLAlchemy 2.0 persistence layer (see Phase 2 below);
  workflow-run and task history DB persistence lands in Phase 7

## Phase 2 — Persistent memory (implemented)

- Live backend: **PostgreSQL 16 + pgvector 0.6.0** at `127.0.0.1:5432`
  (database `spidey`), SQLAlchemy 2.0 models, lazy engine creation.
- Tables: `users`, `conversations`, `messages`, `memories`, `documents`,
  `document_chunks` (pgvector `VECTOR(384)` embedding since the Phase 3
  migration `a4f7c2d91e5b` — was `VECTOR(1536)` — when
  `VECTOR_BACKEND=pgvector`, JSON otherwise), `tasks`, `reminders`,
  `tool_calls`, `workflow_runs`, `workflow_steps`, `resume_versions`,
  `settings`.
- `MemoryTool` rewritten on the DB: same `save`/`recall`/`list`/`delete`
  interface and result keys; auto-categorization kept; `temporary`
  (or importance < 0.4) memories get `expires_at = now + 7 days`; recall and
  list filter out expired rows; recall orders by keyword overlap, then
  importance.
- New endpoints: `POST /api/memory` (`{"content", "category"?, "importance"?}`
  → `{"saved": {...}}`; 422 on empty content),
  `DELETE /api/memory/{memory_id}` → `{"deleted": id}` (404 on unknown id);
  `GET /api/memory` keeps its `{"memories": [...]}` shape.
- Memory tab is real: list (content, category, importance bar, created date),
  add-memory form, delete button.
- Migrations: Alembic (`backend/alembic/`, initial revision `031bbd97dc21`).
  **Canonical schema path: `alembic upgrade head`** (run from `backend/`).
  `init_db()` / `create_all` on app startup is the dev convenience bootstrap
  (it also seeds the default `local` user).

## Phase 3 — RAG knowledge base (implemented)

- **Embedding provider: `HashingEmbeddingProvider` (DEV fallback) — ACTIVE.**
  `sentence-transformers` could not be installed in this environment
  (torch download too large / disk ran out), so the pipeline runs on the
  deterministic char-ngram hashing provider (`name = "hashing-dev-fallback"`).
  It is **not semantic** — retrieval is keyword/lexical-overlap based — but the
  full RAG loop (ingest → chunk → embed → vector search → grounded answer)
  works end to end. Swap in `sentence-transformers` later (`pip install
  sentence-transformers`) and `get_embedding_provider()` will pick it up
  automatically; the active provider is logged at startup.
- **Embedding dimension: 384** for every provider. Alembic revision
  `a4f7c2d91e5b` migrates `document_chunks.embedding` from `VECTOR(1536)` to
  `VECTOR(384)` on the pgvector backend (JSON fallback on sqlite/local),
  and adds `documents(filename, content_type, status, chunk_count)` plus
  `document_chunks(source, created_at)`.
- **Vector backend: pgvector** (live Postgres 16 + pgvector 0.6.0) with
  `embedding <=> :vec` cosine-distance ordering; `LocalVectorStore`
  (brute-force Python cosine) is used when `VECTOR_BACKEND=local`
  (e.g. the sqlite test suite). Scores are cosine similarities on both.
- **Upload limits:** `.pdf` / `.docx` / `.txt` / `.md` only, ≤ 10 MB
  (Content-Length check + read cap), 422 on bad type, non-empty extraction
  required. Originals stored under `backend/uploads/` (gitignored, never
  committed). Chunking: ~400 words with 50-word overlap, `chunk_index` recorded.
- **Agent wiring (memory vs RAG stay distinct):** new intents
  `knowledge_search` ("search my documents…", "find in my files…") and
  `summarize_document`, both routed to the `rag` tool (`"rag": ("results",
  "documents")` added to `_VERIFY_KEYS`). Grounded answers start with
  "Based on your uploaded documents" and cite `[filename, chunk N]`;
  below the **confidence threshold (cosine < 0.15)** the agent replies with
  exactly: "I couldn't find enough information in your documents to answer
  that reliably."
- **Knowledge endpoints:** `POST /api/knowledge/upload` (multipart →
  `{"document": {...}}`), `GET /api/knowledge/documents`,
  `GET /api/knowledge/search?q=&limit=5` → `{"results": [...]}` (each with
  `document_id`, `chunk_id`, `chunk_index`, `content`, `score`, `source`,
  `created_at`), `DELETE /api/knowledge/documents/{id}` (removes chunks, row
  and file). No tracebacks to users — clean `HTTPException` messages.
- **Knowledge tab (frontend):** file upload, document list (name / type /
  date / status / chunk count / delete), search box with cited results
  (`[source, chunk N]` + score). `npm run build` passes.

## Phase 4 — Resume intelligence (implemented)

- **Rule-based analysis — honest by construction.** Everything is regex /
  token-overlap heuristics in `backend/app/tools/resume.py`; there is no ML
  model and no LLM requirement. The tool **never invents**: analysis only
  inspects existing text, improvement rewrites only *rephrase* existing
  bullets (stronger action verbs, filler trimmed), and job-match reports
  matched vs missing skills explicitly — it never claims you have a skill the
  CV doesn't evidence.
- **ResumeTool** (`"resume"` in `TOOL_REGISTRY`, verify keys `("analysis",
  "suggestions", "job_match", "versions", "version")`): `parse` (regex section
  detection — summary/education/experience/projects/skills/certifications/
  achievements with header variants like "Work Experience"/"Employment
  History" — plus contact extraction and truncated `raw_text`), `analyze`
  (sections found/missing, issues `weak_verb` / `vague_statement` /
  `repetition` (token-Jaccard ≥ 0.5) / `missing_measurable`, 0–100 quality
  score, ATS panel with keyword coverage + formatting risks + score, and an
  explicit `missing_info[]` gap list), `improve` (up to 8 rephrase
  suggestions; a test-enforced invariant guarantees every skill token in a
  suggestion already exists in the CV), `job_match` (transparent lexicon +
  alias extractor over the JD; matched/missing/unclear skills, relevant
  experience, honest improvements, keywords-to-consider, 0–100 match score),
  `create_version` / `latest` / `list`.
- **Provider-assisted polish (graceful):** `improve` tries
  `provider.agenerate` for rewrite polish when the provider isn't
  `rule_based`, but any failure — offline provider, over-long output, or
  output that adds a skill token not in the original bullet — falls back to
  the pure rule-based rewrite. The action never fails because the LLM is
  unavailable.
- **Versioning is append-only:** `resume_versions` gained `version_number`
  (per-user monotonic), `source_filename`, `created_from`
  (`"upload"`/`"improvement"`/`"job_match"`/source-version id) via Alembic
  revision `c9f7c2d91e5b` (backfills per-user row numbers). `content` holds
  the full CV text (`content_text` is a read/write alias). Nothing is ever
  overwritten — restore creates a new row.
- **Resume endpoints** (`backend/app/routes/resume.py`): `POST
  /api/resume/upload` (multipart ≤ 10 MB, `.pdf`/`.docx`/`.txt` → version 1),
  `POST /api/resume/analyze`, `POST /api/resume/job-match` (also saves a
  job-specific version), `POST /api/resume/versions/{id}/improve`,
  `GET /api/resume/versions`, `GET /api/resume/versions/{id}`,
  `POST /api/resume/versions/{id}/restore` (new row, never mutates),
  `GET /api/resume/versions/{id}/download?format=txt|md`,
  `GET /api/resume/versions/{id}/compare/{other_id}` (difflib unified diff).
- **Agent wiring:** new intents `resume_analyze` ("analyze my resume",
  "check my CV") and `resume_improve` ("improve my CV") run a dedicated
  9-stage pipeline with visible steps: Understand request → Read resume →
  Analyze sections → Identify weaknesses → Retrieve memory → Generate
  suggestions → Verify results → Compose response → Save memory
  (`MAX_AGENT_STEPS` raised 8 → 9). With no CV stored, chat replies exactly:
  "I don't have your CV yet — upload it in the Resume tab." Job-match via
  chat is intentionally **not** wired — the Resume tab is the primary path
  (the JD is long-form input that belongs in a textarea, not a chat box).
- **Resume tab (frontend):** upload card, analysis report (sections checklist,
  quality/ATS score badges, issues grouped by type, missing-info callouts,
  rewrite suggestions), job-match view (version picker + JD textarea +
  matched/missing/unclear chips, relevant experience, improvements,
  keywords), version history (list, restore-as-new, txt/md download,
  two-version diff). `npm run build` passes.
- **Tests:** `backend/tests/test_resume.py` — 17 tests (section detection,
  weak-verb/vague/missing-measurable/repetition detection, the no-invention
  invariant, append-only versioning incl. restore, job-match matched/missing
  split, no-CV clean message, the 9-step pipeline). 58/58 green.

## Phase 5 — Tools (implemented)

- **TaskTool is now DB-backed** (`backend/app/tools/tasks.py`, `tasks` table):
  same action interface as before (`create`/`list`/`get`/`complete`/
  `set_done`/`delete`; result keys `"task"`/`"tasks"`/`"deleted"`), so the
  agent verify step and `GET /api/tasks` keep working. Due-date parsing lives
  in the tool (`parse_due`: "tomorrow"/"today"/"in N days"/"next week" →
  ISO date); the agent's `_extract_due` reuses it. Task REST grew up too:
  `POST /api/tasks` (title + optional due), `PATCH /api/tasks/{id}`
  (`{"done": bool}` toggle), `DELETE /api/tasks/{id}`.
- **ReminderTool** (`backend/app/tools/reminders.py`, `reminders` table):
  `create {"title", "remind_at"?: iso}` / `list` / `get` / `complete` /
  `set_done` / `delete`; result keys `"reminder"`/`"reminders"`/`"deleted"`.
  Simple time-expression parsing (`parse_reminder_at`: "tomorrow at 5pm",
  "in 2 hours", "tonight", …; vague → tomorrow 9am) and title extraction
  (`extract_reminder_title`) live in the tool and are reused by the agent.
  REST: `GET/POST /api/reminders`, `PATCH /api/reminders/{id}`,
  `DELETE /api/reminders/{id}`.
- **SearchTool** (`backend/app/tools/search.py`): web search via the
  DuckDuckGo Instant Answer endpoint (`https://api.duckduckgo.com/?q=…&
  format=json&no_html=1&skip_disambig=1`, no key, 15 s httpx timeout).
  Parses `AbstractText`/`AbstractURL` plus the first ~5 `RelatedTopics`
  (`Text`+`FirstURL`). Any failure raises
  `ToolError("Web search is unavailable right now.")` — never a traceback —
  and the agent degrades gracefully: the run completes with that message
  instead of failing. Honest limit: the DDG instant-answer API returns an
  abstract + related topics, not a full web index — quick facts yes, deep
  research no.
- **DocumentTool** (`backend/app/tools/documents.py`): `create
  {"title", "content", "format": "md"|"txt"}` saves a real file under
  `backend/generated/` (gitignored; a JSON index keeps title/format
  metadata durable) and returns `{"document": {"id", "title", "format",
  "download_url"}}`; `get`/`list`/`delete` included. `GET
  /api/docs/{doc_id}/download` serves the file; `POST/GET /api/docs`,
  `DELETE /api/docs/{doc_id}` round out the REST.
- **CodeTool** (`backend/app/tools/code.py`): `explain {"code",
  "language"?}` → `{"explanation", "observations"}`. With a real model
  provider configured it calls `provider.agenerate` with the code as
  context; offline (`rule_based`) or on provider failure it says so
  honestly and returns regex-derived structural observations only
  (detected functions/classes, imports, line counts, language guess) —
  never inventing behavior the code doesn't show.
- **ShellTool stub** (`backend/app/tools/shell.py`): registered as
  `"shell"` but `execute()` always raises `ToolError("Shell access is not
  enabled in SPIDEY v1. …")`. The classifier has no rule that can route to
  it (test-enforced). Satisfies the v1 spec: no unrestricted computer
  control.
- **Permission levels (spec 21):** `BaseTool.permission` (`"read"` default)
  + per-action `action_permissions` overrides:

  | tool | read | low_write (auto) | confirm (chat approval) |
  |---|---|---|---|
  | calculator, rag, resume, search, code | everything | — | — |
  | tasks, reminders | list, get | create, complete, set_done | delete |
  | documents | list, get | create | delete |
  | memory | recall, list | save | delete |
  | shell | — | — | everything (always refused) |

- **Chat confirmation flow:** when the agent's plan includes a
  `"confirm"`-level action, it does NOT execute — the run finishes with
  status `awaiting_confirmation` and result `{"needs_confirmation": true,
  "proposal": "<human sentence>", "confirm_token": "<token>"}`. Pending
  actions are stored in-memory keyed by token (single-use, 10-min expiry).
  `POST /api/chat` accepts `confirm_token`: when valid, the pending action
  executes through the normal workflow on a new run (Execute/Verify steps);
  bad/expired tokens are rejected with 400. Direct REST DELETEs stay as-is
  (explicit user clicks need no dialog). Frontend `ChatPanel` shows a
  Confirm/Cancel dialog on `needs_confirmation`; Confirm re-POSTs with the
  token.
- **Classifier intents** (`rule_based.py`): `reminder_create` ("remind me to
  X tomorrow/at 5pm" — checked before the old task rule that used to own
  "remind me to"), `reminder_list`, `reminder_complete`, `reminder_delete`,
  `task_delete`, `task_complete`, `web_search` ("search the web for X",
  "find information about X" — checked AFTER knowledge search so "search my
  documents" stays RAG), `document_create` ("create a document/note titled
  X: …"), `code_explain` ("explain this code: …", "what does this code
  do"). `_VERIFY_KEYS` extended: reminders `("reminder", "reminders",
  "deleted")`, search `("results",)`, documents `("document",
  "documents", "deleted")`, code `("explanation",)`.
- **Frontend:** Tasks tab is fully real (create with due date, done toggle,
  delete); new Reminders tab (same CRUD with datetime picker); ChatPanel
  Confirm/Cancel dialog; `npm run build` passes.
- **Tests:** `backend/tests/test_tools_phase5.py` — 20 tests (task CRUD on
  DB + due parsing, reminder CRUD + time/title parsing, document
  create/get/list/delete + download route, code structural fallback,
  search graceful degradation via monkeypatched httpx, full confirmation
  flow incl. not-deleted-before-confirm, ordinal resolution, token
  single-use/expiry, low_write auto-execution, shell refusal + classifier
  never routing to shell, intent routing incl. "search my documents" →
  RAG). 78/78 green.

## Phase 6 — Voice interface (implemented)

**No backend changes — the API is already text in/out, so voice is a pure
frontend layer** (spec section 18). Built entirely on the free browser Web
Speech API: no paid services, no API keys, no backend endpoints.

- **`frontend/src/hooks/useVoice.ts`** (new): reusable voice module —
  - `useSpeechRecognition()`: wraps `window.SpeechRecognition ||
    window.webkitSpeechRecognition` (`continuous=false`,
    `interimResults=true`, `lang='en-US'`); exposes `{ supported,
    listening, transcript (live interim), finalTranscript, error, start(),
    stop(), clear() }`. `supported` is `false` when the API is absent
    (non-HTTPS / headless browsers), and the UI degrades gracefully.
  - `useTextToSpeech()`: wraps `window.speechSynthesis`; exposes
    `{ supported, speaking, speak(text), stop(), replay() }`. Speech is
    cancelled on unmount.
  - `stripForSpeech()`: light regex cleanup before reading aloud (drops
    code blocks, markdown formatting, links, `[n]` citations, bare URLs).
- **ChatPanel wiring** (`frontend/src/components/ChatPanel.tsx`):
  - Mic button 🎤 next to the input: pulsing red dot + "Listening…" with
    the live interim transcript while recording; the final transcript
    **auto-sends** as the chat message. Disabled with tooltip "Voice not
    supported in this browser" when the API is absent — the app stays fully
    usable through text.
  - Every assistant reply has a 🔊 read-aloud button (markdown stripped)
    and a ⏹ stop button that appears while speaking.
- **Browser requirements:** Chrome or Edge recommended (best Web Speech
  support); HTTPS or localhost is required for the microphone. The browser
  will ask for mic permission on first use. If the API is unavailable, the
  mic button is greyed out and everything else works unchanged.
- **Honest limits:** recognition accuracy/voice choice depends on the
  browser and OS voices installed; the mic cannot be verified headless, so
  voice was validated to build + bundle, not end-to-end on a microphone.

## Roadmap

- **Phase 2 — Memory:** PostgreSQL, memory tables, retrieval, management UI ✅
- **Phase 3 — RAG:** document upload, chunking, embeddings, pgvector, sources ✅
  (embeddings on DEV hashing fallback — real semantic model is a future swap-in)
- **Phase 4 — Resume intelligence:** CV parsing, analysis, job matching, versioning ✅
  (rule-based analysis; rewrites are rephrasings only; job-match lives in the tab)
- **Phase 5 — Tools:** reminders, web search, document tools, code tool,
  permission levels + chat confirmation flow ✅
  (DDG instant-answer limits; code explanations are structural unless a
  model provider is configured; shell stub refuses)
- **Phase 6 — Voice:** speech-to-text, TTS, microphone UI ✅
  (free Web Speech API only; zero backend changes, zero paid APIs)
- **Phase 7 — Advanced agent:** multi-tool planning, retries, workflow history

## Quickstart

Backend:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

Database (Phase 2+):

```bash
docker compose up -d            # or use the local PostgreSQL 16
cd backend
.venv/bin/alembic upgrade head  # canonical schema path
```

PostgreSQL recovery (the data dir lives outside `~` and is ephemeral — if the
DB is gone after a VM replacement):

```bash
bash ~/workspace/spidey/dev-setup-postgres.sh
```

## API

| Method | Path                        | Description                              |
| ------ | --------------------------- | ---------------------------------------- |
| GET    | `/`                         | Service info                             |
| GET    | `/api/health`               | Health + active provider                 |
| POST   | `/api/chat`                 | `{"message": str}` → `{"run_id": str}`   |
| GET    | `/api/workflow/{run_id}`    | Full workflow run with steps             |
| GET    | `/api/workflow/{run_id}/stream` | SSE live step updates                |
| GET    | `/api/activity`             | Past runs, newest first                  |
| GET    | `/api/memory`               | Stored memories                          |
| POST   | `/api/memory`               | `{"content": str, ...}` → `{"saved": {...}}` |
| DELETE | `/api/memory/{memory_id}`     | → `{"deleted": id}` (404 if unknown)        |
| GET    | `/api/tasks`                | Task list                                |
| POST   | `/api/knowledge/upload`     | Multipart file (≤10 MB) → `{"document": {...}}` |
| GET    | `/api/knowledge/documents`  | Document list with metadata              |
| GET    | `/api/knowledge/search`     | `?q=&limit=` → `{"results": [...]}` (cited chunks) |
| DELETE | `/api/knowledge/documents/{id}` | Remove document + chunks + file (404 if unknown) |

## Safety

- Read tools run automatically; external/destructive actions will require
  confirmation (permission levels land with Phase 5+ tools).
- Tool errors never leak tracebacks to the user; technical details stay in logs.
- `.env`, credentials, and uploaded documents are never committed.
