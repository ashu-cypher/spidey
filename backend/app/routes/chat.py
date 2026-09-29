import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.spidey_agent import SpideyAgent
from app.providers import get_provider
from app.tools import TOOL_REGISTRY
from app.workflows.engine import engine

router = APIRouter()
provider = get_provider()
agent = SpideyAgent(provider, TOOL_REGISTRY)


class ChatRequest(BaseModel):
    message: str


@router.post("/api/chat")
async def post_chat(req: ChatRequest):
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


@router.get("/api/tasks")
async def tasks():
    return await TOOL_REGISTRY["tasks"].execute(action="list")
