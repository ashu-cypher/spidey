import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from inspect import Parameter, signature
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.agents.spidey_agent import SpideyAgent
from app.config import settings
from app.database import get_session
from app.models import ConversationAttachment
from app.providers import get_provider
from app.rag.pipeline import extract_text, validate_upload
from app.tools import TOOL_REGISTRY
from app.tools.base import ToolError
from app.tools.resume import detect_sections
from app.workflows.engine import engine
from app.workflows.persistence import (
    get_persisted_run,
    list_persisted_runs,
    persist_run,
)

router = APIRouter()
provider = get_provider()
# MEW capability upgrade: the agent consults get_provider() per request so
# PUT /api/system/provider switches take effect immediately (the selection
# is persisted in backend/.provider.json by app.providers.manager).
agent = SpideyAgent(provider, TOOL_REGISTRY, provider_factory=get_provider)

# Phase 7: write-through persistence — finished runs land in workflow_runs /
# workflow_steps so activity survives restarts. Registered on the process
# singleton here; finish_run swallows hook errors so DB trouble never breaks
# the live run.
engine.add_finish_hook(persist_run)


class ChatRequest(BaseModel):
    message: str = ""
    confirm_token: str | None = None
    # Recent conversation turns for continuity (pronoun resolution). Bounded
    # to the last 10 in post_chat before reaching the agent.
    history: list[dict] = []
    # Reply language: "auto" detects per message ('en'|'hi'|'hinglish');
    # any other value forces it ('hindi' normalizes to 'hi').
    lang: str = "auto"
    # MEW upgrade: chat-client conversation key. Files uploaded via
    # POST /api/chat/attach under this id become context for the turn's
    # attachments ("explain this" resolves against them, no re-upload).
    conversation_id: str | None = None


class MemoryCreateRequest(BaseModel):
    content: str
    category: str | None = None
    importance: float | None = None


class TaskCreateRequest(BaseModel):
    title: str
    due: str | None = None


class TaskDoneRequest(BaseModel):
    done: bool


class ReminderCreateRequest(BaseModel):
    title: str
    remind_at: str | None = None


class ReminderDoneRequest(BaseModel):
    done: bool


class DocumentCreateRequest(BaseModel):
    title: str = "Untitled document"
    content: str = ""
    format: str = "txt"


@router.post("/api/chat")
async def post_chat(req: ChatRequest):
    # Keep only well-formed turns, oldest first, capped at the last 10.
    history = [
        {"role": h.get("role"), "content": h.get("content", "")}
        for h in (req.history or [])
        if isinstance(h, dict) and h.get("role") in ("user", "assistant")
    ][-10:]
    if req.confirm_token:
        # User approved a pending "confirm"-level action: run it through the
        # normal workflow (new run with Execute/Verify steps).
        try:
            pending = agent.pop_pending(req.confirm_token)
        except ToolError as exc:
            raise HTTPException(400, exc.user_message)
        run = engine.create_run(f"confirmed: {pending['proposal']}")
        asyncio.create_task(_execute_pending(run.workflow_id, pending, history))
        return {"run_id": run.workflow_id}
    run = engine.create_run(req.message)
    asyncio.create_task(
        _execute(
            run.workflow_id, req.message, history, req.lang,
            conversation_id=req.conversation_id,
        )
    )
    return {"run_id": run.workflow_id}


async def _stream_events(
    run, message, history, lang, confirmed_pending=None, conversation_id=None
):
    """Bridge agent.run_stream emit() calls to SSE frames.

    Event contract: ``event: <type>\\ndata: <json>\\n\\n`` with types
    ``state`` | ``voice_summary`` | ``delta`` | ``done`` | ``error``.
    Keep-alive ``: ping`` comments every 15s while the run is quiet.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event_type: str, data: dict):
        await queue.put((event_type, data))

    async def worker():
        try:
            if confirmed_pending is not None:
                await agent.run_stream_confirmed(
                    confirmed_pending, run, engine, history=history, emit=emit
                )
            else:
                await agent.run_stream(
                    message, run, engine, history=history, lang=lang, emit=emit,
                    conversation_id=conversation_id,
                )
        finally:
            await queue.put((None, None))  # sentinel: stream is over

    task = asyncio.create_task(worker())
    try:
        while True:
            try:
                event_type, data = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event_type is None:
                break
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    finally:
        if not task.done():
            task.cancel()


@router.post("/api/chat/stream")
async def post_chat_stream(req: ChatRequest):
    """Streaming chat: Server-Sent Events for the agent run.

    Accepts the same ChatRequest (message/history/lang/confirm_token).
    POST /api/chat is unchanged; this is a parallel streaming variant.
    """
    history = [
        {"role": h.get("role"), "content": h.get("content", "")}
        for h in (req.history or [])
        if isinstance(h, dict) and h.get("role") in ("user", "assistant")
    ][-10:]
    if req.confirm_token:
        # Confirmed pending action, streamed: same events, pre-resolved tool.
        try:
            pending = agent.pop_pending(req.confirm_token)
        except ToolError as exc:
            raise HTTPException(400, exc.user_message)
        run = engine.create_run(f"confirmed: {pending['proposal']}")
        return StreamingResponse(
            _stream_events(run, "", history, req.lang, confirmed_pending=pending),
            media_type="text/event-stream",
        )
    run = engine.create_run(req.message)
    return StreamingResponse(
        _stream_events(
            run, req.message, history, req.lang,
            conversation_id=req.conversation_id,
        ),
        media_type="text/event-stream",
    )


# --- Conversation attachments (MEW upgrade) -----------------------------------

# Text is extracted with the same pipeline the resume tool uses
# (app.rag.pipeline: validate_upload + extract_text) — no duplicated parsers.
_ATTACH_TEXT_LIMIT = 8000  # extracted_text is bounded at write time
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# A file counts as a resume when its name says so, when the resume tool's
# own section detector finds at least two real CV sections in the extracted
# text, or when it has one CV section plus contact info (email/phone/
# LinkedIn) — a weaker but still resume-shaped signal. This is a heuristic,
# and it is labeled as one.
_RESUME_NAME_HINT = re.compile(r"\b(resume|cv|curriculum\s*vitae)\b", re.IGNORECASE)
_RESUME_CORE_SECTIONS = {"summary", "education", "experience", "projects", "skills"}
_RESUME_CONTACT_HINT = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|(?:https?://)?(?:www\.)?linkedin\.com/\S+"
    r"|\+?\d[\d\s().-]{6,}\d",
    re.IGNORECASE,
)


def _classify_attachment_kind(filename: str, text: str) -> str:
    """'resume' | 'document'. Reuses the resume tool's section detection."""
    if _RESUME_NAME_HINT.search(filename or ""):
        return "resume"
    sections = detect_sections(text or "")
    hits = sum(1 for s in _RESUME_CORE_SECTIONS if sections.get(s))
    if hits >= 2:
        return "resume"
    if hits >= 1 and _RESUME_CONTACT_HINT.search(text or ""):
        # One CV section + real contact info: resume-shaped, not a doc.
        return "resume"
    return "document"


@router.post("/api/chat/attach")
async def attach_file(
    file: UploadFile = File(...),
    conversation_id: str = Form(...),
    message: str | None = Form(None),
):
    """Attach a file to a conversation as chat context.

    Multipart form: ``file`` (UploadFile), ``conversation_id`` (str),
    optional ``message`` (str — accepted for future use, not stored).
    Text is extracted with the RAG pipeline's extractors (.pdf/.docx/.txt/
    .md, 10 MB cap) and bounded to ~8k chars at write time. Images are
    accepted but only the filename is stored: this backend has no vision
    model, so the image content is never read.

    Returns ``{id, conversation_id, filename, kind, created_at}`` where
    kind is 'document' | 'resume' | 'image'.
    """
    filename = Path(file.filename or "upload").name
    data = await file.read()
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")
    ext = Path(filename).suffix.lower()
    if ext in _IMAGE_EXTENSIONS:
        kind = "image"
        # Honest: no vision model — the image bytes are never read.
        text = (
            f"[Image attachment: {filename}. This backend has no vision "
            f"model, so only the filename was stored — the image content "
            f"was not read.]"
        )
    else:
        try:
            ext = validate_upload(filename, data)
        except ToolError as exc:
            raise HTTPException(422, exc.user_message)
        try:
            text = extract_text(ext, data, filename)
        except ToolError as exc:
            raise HTTPException(422, exc.user_message)
        kind = _classify_attachment_kind(filename, text)
    with get_session() as session:
        row = ConversationAttachment(
            conversation_id=conversation_id,
            filename=filename,
            kind=kind,
            extracted_text=text[:_ATTACH_TEXT_LIMIT],
        )
        session.add(row)
        session.flush()
        row_id, row_kind = row.id, row.kind
        created_at = row.created_at
    return {
        "id": row_id,
        "conversation_id": conversation_id,
        "filename": filename,
        "kind": row_kind,
        "created_at": created_at.isoformat() if created_at else None,
    }


async def _execute(
    run_id: str,
    message: str,
    history: list[dict] | None = None,
    lang: str = "auto",
    conversation_id: str | None = None,
):
    run = engine.get_run(run_id)
    try:
        # Backward compat: test doubles of the agent may predate ``lang``.
        try:
            params = signature(agent.run).parameters
            takes_lang = "lang" in params or any(
                p.kind == Parameter.VAR_KEYWORD for p in params.values()
            )
            takes_conversation_id = "conversation_id" in params or any(
                p.kind == Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            takes_lang = False
            takes_conversation_id = False
        kwargs: dict = {"history": history}
        if takes_lang:
            kwargs["lang"] = lang
        if takes_conversation_id:
            kwargs["conversation_id"] = conversation_id
        response = await agent.run(message, run, engine, **kwargs)
        # Agent normally finishes the run itself; this is a safety net.
        if engine.get_run(run_id).status == "running":
            engine.finish_run(run_id, "completed", result=response)
    except Exception:
        if engine.get_run(run_id).status == "running":
            engine.finish_run(run_id, "failed", result="Spidey couldn't complete that step.")


async def _execute_pending(
    run_id: str, pending: dict, history: list[dict] | None = None
):
    run = engine.get_run(run_id)
    try:
        response = await agent.run_confirmed(pending, run, engine, history=history)
        if engine.get_run(run_id).status == "running":
            engine.finish_run(run_id, "completed", result=response)
    except Exception:
        if engine.get_run(run_id).status == "running":
            engine.finish_run(run_id, "failed", result="Spidey couldn't complete that step.")


def _tool_error(exc: ToolError, not_found_code: int = 404) -> HTTPException:
    msg = exc.user_message
    code = 400
    if "not found" in msg.lower():
        code = not_found_code
    return HTTPException(code, msg)


@router.get("/api/workflow/{run_id}")
async def get_workflow(run_id: str):
    run = engine.get_run(run_id)
    if run is not None:
        payload = run.model_dump(mode="json")
    else:
        # Phase 7: fall back to the DB-persisted run (restart survival).
        payload = get_persisted_run(run_id)
        if payload is None:
            raise HTTPException(404, "workflow not found")
    if settings.spidey_debug:
        # Phase 7 debug block: provider/model + observability counters.
        payload["debug"] = agent.get_debug_info(run_id)
    return payload


@router.get("/api/workflow/{run_id}/stream")
async def stream_workflow(run_id: str):
    run = engine.get_run(run_id)
    if not run:
        raise HTTPException(404, "workflow not found")

    async def gen():
        current = engine.get_run(run_id)
        for s in current.steps:
            yield f"event: step_update\ndata: {json.dumps(s.model_dump(mode='json'))}\n\n"
        if current.status != "running":
            yield f"event: done\ndata: {json.dumps(current.model_dump(mode='json'))}\n\n"
            return
        q = engine.subscribe(run_id)
        if engine.get_run(run_id).status != "running":
            r = engine.get_run(run_id)
            yield f"event: done\ndata: {json.dumps(r.model_dump(mode='json'))}\n\n"
            return
        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=20)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            key = "step" if evt["type"] == "step_update" else "run"
            yield f"event: {evt['type']}\ndata: {json.dumps(evt[key])}\n\n"
            if evt["type"] == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/api/activity")
async def activity():
    """Phase 7: in-memory running runs PLUS DB-persisted finished runs,
    deduped by workflow_id, newest first (restart survival)."""
    runs = [r.model_dump(mode="json") for r in engine.list_runs()]
    seen = {r["workflow_id"] for r in runs}
    for persisted in list_persisted_runs():
        if persisted["workflow_id"] not in seen:
            runs.append(persisted)
            seen.add(persisted["workflow_id"])
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs


@router.get("/api/memory")
async def memories():
    return await TOOL_REGISTRY["memory"].execute(action="list")


@router.post("/api/memory")
async def create_memory(req: MemoryCreateRequest):
    from app.tools.base import ToolError

    if not req.content.strip():
        raise HTTPException(422, "content must not be empty")
    try:
        return await TOOL_REGISTRY["memory"].execute(
            action="save",
            content=req.content.strip(),
            category=req.category,
            importance=req.importance,
        )
    except ToolError as exc:
        raise HTTPException(400, exc.user_message)


@router.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str):
    from app.tools.base import ToolError

    try:
        return await TOOL_REGISTRY["memory"].execute(action="delete", id=memory_id)
    except ToolError as exc:
        raise HTTPException(404, exc.user_message)


@router.get("/api/tasks")
async def tasks():
    return await TOOL_REGISTRY["tasks"].execute(action="list")


@router.post("/api/tasks")
async def create_task(req: TaskCreateRequest):
    if not req.title.strip():
        raise HTTPException(422, "title must not be empty")
    try:
        return await TOOL_REGISTRY["tasks"].execute(
            action="create", title=req.title.strip(), due=req.due
        )
    except ToolError as exc:
        raise _tool_error(exc, 400)


@router.patch("/api/tasks/{task_id}")
async def set_task_done(task_id: str, req: TaskDoneRequest):
    try:
        return await TOOL_REGISTRY["tasks"].execute(
            action="set_done", id=task_id, done=req.done
        )
    except ToolError as exc:
        raise _tool_error(exc)


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    # Direct REST delete: an explicit user click, no chat confirmation needed.
    try:
        return await TOOL_REGISTRY["tasks"].execute(action="delete", id=task_id)
    except ToolError as exc:
        raise _tool_error(exc)


# --- Reminders (Phase 5) ----------------------------------------------------


@router.get("/api/reminders")
async def reminders():
    return await TOOL_REGISTRY["reminders"].execute(action="list")


@router.post("/api/reminders")
async def create_reminder(req: ReminderCreateRequest):
    if not req.title.strip():
        raise HTTPException(422, "title must not be empty")
    try:
        return await TOOL_REGISTRY["reminders"].execute(
            action="create", title=req.title.strip(), remind_at=req.remind_at
        )
    except ToolError as exc:
        raise _tool_error(exc, 400)


@router.patch("/api/reminders/{reminder_id}")
async def set_reminder_done(reminder_id: str, req: ReminderDoneRequest):
    try:
        return await TOOL_REGISTRY["reminders"].execute(
            action="set_done", id=reminder_id, done=req.done
        )
    except ToolError as exc:
        raise _tool_error(exc)


@router.delete("/api/reminders/{reminder_id}")
async def delete_reminder(reminder_id: str):
    # Direct REST delete: an explicit user click, no chat confirmation needed.
    try:
        return await TOOL_REGISTRY["reminders"].execute(
            action="delete", id=reminder_id
        )
    except ToolError as exc:
        raise _tool_error(exc)


# --- MEW initiation (capability upgrade) -------------------------------------

# The greeting the frontend plays on launch. Served by the backend so
# product copy is not hardcoded in the client; the voice line is run
# through TTS hygiene (strip_for_speech) so it is speakable as-is.
_MEW_GREETING_TEXT = "Hey, I'm MEW. What are we working on today?"


@router.get("/api/conversation/greeting")
async def conversation_greeting():
    """MEW initiation content: greeting text + speakable voice line."""
    from app.services.language import strip_for_speech

    return {
        "text": _MEW_GREETING_TEXT,
        "voice_line": strip_for_speech(_MEW_GREETING_TEXT),
    }


# --- Proactive briefing (read-only) ------------------------------------------


@router.get("/api/briefing")
async def briefing():
    """Read-only proactive briefing: pending task count plus reminders due
    soon (within the next 60 minutes, including overdue ones). No side
    effects — only the tools' list actions are used."""
    tasks_out = await TOOL_REGISTRY["tasks"].execute(action="list")
    pending_tasks = sum(
        1 for t in tasks_out.get("tasks", []) if not t.get("done")
    )
    reminders_out = await TOOL_REGISTRY["reminders"].execute(action="list")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    horizon = now + timedelta(minutes=60)
    due_soon: list[dict] = []
    for r in reminders_out.get("reminders", []):
        if r.get("done") or not r.get("remind_at"):
            continue
        try:
            at = datetime.fromisoformat(r["remind_at"]).replace(tzinfo=None)
        except ValueError:
            continue
        if at <= horizon:
            due_soon.append(
                {
                    "text": r.get("text") or r.get("title") or "",
                    "remind_at": r["remind_at"],
                }
            )
    due_soon.sort(key=lambda d: d["remind_at"])
    return {"pending_tasks": pending_tasks, "due_soon": due_soon}


# --- Generated documents (Phase 5) ------------------------------------------


@router.get("/api/docs")
async def list_docs():
    return await TOOL_REGISTRY["documents"].execute(action="list")


@router.post("/api/docs")
async def create_doc(req: DocumentCreateRequest):
    try:
        return await TOOL_REGISTRY["documents"].execute(
            action="create",
            title=req.title,
            content=req.content,
            format=req.format,
        )
    except ToolError as exc:
        raise _tool_error(exc, 400)


@router.get("/api/docs/{doc_id}/download")
async def download_doc(doc_id: str):
    try:
        result = await TOOL_REGISTRY["documents"].execute(action="get", id=doc_id)
    except ToolError as exc:
        raise _tool_error(exc)
    doc = result["document"]
    return FileResponse(
        doc["path"],
        media_type="text/plain; charset=utf-8",
        filename=f"{doc['title'] or 'document'}.{doc['format']}",
    )


@router.delete("/api/docs/{doc_id}")
async def delete_doc(doc_id: str):
    # Direct REST delete: an explicit user click, no chat confirmation needed.
    try:
        return await TOOL_REGISTRY["documents"].execute(action="delete", id=doc_id)
    except ToolError as exc:
        raise _tool_error(exc)
