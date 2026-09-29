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
  execution → verification → response → memory update (`MAX_AGENT_STEPS=8`)
- Tool system with common interface: `calculator`, `memory`, `tasks`
- Workflow engine: every agent run is a `WorkflowRun` of `WorkflowStep`s
  (WAITING / RUNNING / COMPLETED / FAILED) with timing, visible live in the UI
- In-memory stores (persistent PostgreSQL + pgvector arrive in Phase 2/3)

## Roadmap

- **Phase 2 — Memory:** PostgreSQL, memory tables, retrieval, management UI
- **Phase 3 — RAG:** document upload, chunking, embeddings, pgvector, sources
- **Phase 4 — Resume intelligence:** CV parsing, analysis, job matching, versioning
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

Database (Phase 2+, optional for now):

```bash
docker compose up -d
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
| GET    | `/api/tasks`                | Task list                                |

## Safety

- Read tools run automatically; external/destructive actions will require
  confirmation (permission levels land with Phase 5+ tools).
- Tool errors never leak tracebacks to the user; technical details stay in logs.
- `.env`, credentials, and uploaded documents are never committed.
