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

## Roadmap

- **Phase 2 — Memory:** PostgreSQL, memory tables, retrieval, management UI ✅
- **Phase 3 — RAG:** document upload, chunking, embeddings, pgvector, sources ✅
  (embeddings on DEV hashing fallback — real semantic model is a future swap-in)
- **Phase 4 — Resume intelligence:** CV parsing, analysis, job matching, versioning ✅
  (rule-based analysis; rewrites are rephrasings only; job-match lives in the tab)
- **Phase 5 — Tools:** reminders, web search, document tools, code tool
- **Phase 6 — Voice:** speech-to-text, TTS, microphone UI
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
