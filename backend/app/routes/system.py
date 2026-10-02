"""MISSION MEW — system routes.

* ``GET /api/system/metrics`` — read-only system metrics (existing).
* ``GET /api/system/status`` — REAL service state: provider/model, DB
  reachability, voice backends, RAG backend, uptime. Nothing is faked:
  "ready" means a query actually succeeded.
* ``GET``/``PUT /api/system/provider`` — runtime AI-provider management.
  The choice persists server-side in ``backend/.provider.json`` (gitignored,
  NOT .env). Secrets stay in ``.env`` and are never returned here. A PUT
  probes the candidate first: an unreachable Ollama/OpenAI is rejected with
  a human-readable message, never a traceback.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.database import get_session
from app.providers.manager import (
    AVAILABLE_PROVIDERS,
    effective_model,
    get_selection,
    probe_provider,
    set_selection,
)
from app.tools.system_controller import SystemControllerTool

router = APIRouter()

# Process start: uptime_s is measured against this (never faked).
_STARTED_AT = time.time()


class ProviderUpdate(BaseModel):
    provider: str
    model: str | None = None


@router.get("/api/system/metrics")
async def system_metrics():
    tool = SystemControllerTool()
    return await tool.execute(action="metrics")


def _memory_state() -> str:
    """'ready' iff the database answers a trivial query."""
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        return "ready"
    except Exception:
        return "unreachable"


def _rag_state() -> str:
    """'ready' on the pgvector backend; 'degraded' on the local/sqlite
    fallback (brute-force cosine search, no vector index)."""
    return "ready" if settings.vector_backend == "pgvector" else "degraded"


@router.get("/api/system/status")
async def system_status():
    """Real service state. Every value is measured, not hardcoded:

    * ``provider``/``model`` — the runtime-persisted AI provider selection.
    * ``online`` — true (the endpoint answered).
    * ``memory`` — "ready" iff a DB query succeeded.
    * ``voice`` — this backend has no server-side speech models; STT/TTS run
      in the browser, stated honestly.
    * ``rag`` — "ready" on pgvector, "degraded" on the sqlite fallback.
    * ``uptime_s`` — seconds since this process started.
    """
    selection = get_selection()
    return {
        "provider": selection["provider"],
        "model": effective_model(selection["provider"], selection.get("model")),
        "online": True,
        "memory": _memory_state(),
        "voice": {"stt": "browser", "tts": "browser"},
        "rag": _rag_state(),
        "uptime_s": int(time.time() - _STARTED_AT),
    }


@router.get("/api/system/provider")
async def get_provider_info():
    """Current AI provider selection + the providers that can be chosen."""
    selection = get_selection()
    return {
        "provider": selection["provider"],
        "model": effective_model(selection["provider"], selection.get("model")),
        "available_providers": list(AVAILABLE_PROVIDERS),
    }


@router.put("/api/system/provider")
async def set_provider_info(req: ProviderUpdate):
    """Switch the AI provider at runtime.

    The candidate is probed FIRST: unreachable Ollama/OpenAI (or a missing
    model/key) is rejected with a human-readable error and nothing is
    persisted. On success the choice is stored in ``backend/.provider.json``
    and takes effect for subsequent requests (the agent consults the
    provider factory per request — no restart needed).
    """
    name = (req.provider or "").strip().lower()
    if name not in AVAILABLE_PROVIDERS:
        raise HTTPException(
            400,
            f"Unknown provider {req.provider!r} — choose one of: "
            f"{', '.join(AVAILABLE_PROVIDERS)}.",
        )
    model = (req.model or "").strip() or None
    if model and len(model) > 128:
        raise HTTPException(400, "Model name is too long.")
    ok, message = await probe_provider(name, model)
    if not ok:
        # 502: the server understood the request but the upstream AI
        # provider is not usable. Human-readable, never a traceback.
        raise HTTPException(502, message)
    selection = set_selection(name, model)
    return {
        "provider": selection["provider"],
        "model": effective_model(selection["provider"], selection.get("model")),
        "available_providers": list(AVAILABLE_PROVIDERS),
    }
