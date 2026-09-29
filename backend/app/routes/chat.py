import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.agents.spidey_agent import SpideyAgent
from app.providers import get_provider
from app.tools import TOOL_REGISTRY
from app.tools.base import ToolError
from app.workflows.engine import engine

router = APIRouter()
provider = get_provider()
agent = SpideyAgent(provider, TOOL_REGISTRY)


class ChatRequest(BaseModel):
    message: str = ""
    confirm_token: str | None = None


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
    if req.confirm_token:
        # User approved a pending "confirm"-level action: run it through the
        # normal workflow (new run with Execute/Verify steps).
        try:
            pending = agent.pop_pending(req.confirm_token)
        except ToolError as exc:
            raise HTTPException(400, exc.user_message)
        run = engine.create_run(f"confirmed: {pending['proposal']}")
        asyncio.create_task(_execute_pending(run.workflow_id, pending))
        return {"run_id": run.workflow_id}
    run = engine.create_run(req.message)
    asyncio.create_task(_execute(run.workflow_id, req.message))
    return {"run_id": run.workflow_id}


async def _execute(run_id: str, message: str):
    run = engine.get_run(run_id)
    try:
        response = await agent.run(message, run, engine)
        # Agent normally finishes the run itself; this is a safety net.
        if engine.get_run(run_id).status == "running":
            engine.finish_run(run_id, "completed", result=response)
    except Exception:
        if engine.get_run(run_id).status == "running":
            engine.finish_run(run_id, "failed", result="Spidey couldn't complete that step.")


async def _execute_pending(run_id: str, pending: dict):
    run = engine.get_run(run_id)
    try:
        response = await agent.run_confirmed(pending, run, engine)
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
    if not run:
        raise HTTPException(404, "workflow not found")
    return run.model_dump(mode="json")


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
    return [r.model_dump(mode="json") for r in engine.list_runs()]


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
