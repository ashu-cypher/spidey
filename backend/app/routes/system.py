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
    is_model_degraded,
    list_ollama_models,
    probe_provider,
    set_selection,
)
from app.providers.ollama import ollama_vision_supported
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


def _model_display(provider: str, model: str | None, degraded: bool) -> str:
    """Honest header label for the MODEL readout.

    * Ollama live -> the model name, e.g. ``QWEN3:0.6B``.
    * Anything else (degraded fallback, explicit rule_based, unreachable) ->
      ``FALLBACK (RULE-BASED)``.
    """
    if provider == "ollama" and model and not degraded:
        return model.upper()
    if provider == "openai" and model:
        return model.upper()
    return "FALLBACK (RULE-BASED)"


# OpenAI model families with real vision support (name heuristic — the
# server never claims vision for a model family it doesn't recognize).
_OPENAI_VISION_FAMILIES = ("gpt-4o", "gpt-4.1", "o1")


async def _vision_state(provider: str, model: str | None) -> bool:
    """Whether the configured provider can analyze images (spec 18).

    * ollama -> /api/show ``capabilities`` or the VISION_MODELS name
      heuristic (cached per provider instance here — a fresh check per
      status call keeps it honest if the model was pulled meanwhile).
    * openai -> name heuristic over known vision families.
    * rule_based (or anything else) -> False: no vision.
    A degraded/unreachable model is honestly False. Never raises.
    """
    try:
        if is_model_degraded():
            return False
        if provider == "ollama" and model:
            base = settings.ollama_base_url.rstrip("/")
            return await ollama_vision_supported(base, model)
        if provider == "openai" and model:
            lowered = model.lower()
            return any(tag in lowered for tag in _OPENAI_VISION_FAMILIES)
    except Exception:
        return False
    return False


@router.get("/api/system/status")
async def system_status():
    """Real service state. Every value is measured, not hardcoded:

    * ``provider``/``model`` — the runtime-persisted AI provider selection.
    * ``model_display`` — honest header label: ``QWEN3:0.6B`` when the local
      model is live, ``FALLBACK (RULE-BASED)`` when it is not.
    * ``model_degraded`` — True when the startup probe found the configured
      Ollama model unavailable (the agent keeps working on rule_based; the
      first chat message in that state carries a one-time notice).
    * ``vision_supported`` — True when the configured provider's model can
      analyze attached images (Ollama /api/show capabilities or the
      VISION_MODELS name heuristic; OpenAI via known vision families).
      Honestly False when the model is degraded or unreachable.
    * ``online`` — true (the endpoint answered).
    * ``memory`` — "ready" iff a DB query succeeded.
    * ``voice`` — this backend has no server-side speech models; STT/TTS run
      in the browser, stated honestly.
    * ``rag`` — "ready" on pgvector, "degraded" on the sqlite fallback.
    * ``uptime_s`` — seconds since this process started.
    """
    selection = get_selection()
    provider = selection["provider"]
    model = effective_model(provider, selection.get("model"))
    degraded = is_model_degraded()
    return {
        "provider": provider,
        "model": model,
        "model_display": _model_display(provider, model, degraded),
        "model_degraded": degraded,
        "vision_supported": await _vision_state(provider, model),
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


@router.get("/api/system/models")
async def list_models():
    """Models available on the local Ollama server (live ``/api/tags``).

    Returns ``{"base_url", "models", "default"}``. 502 with a human-readable
    message (never a traceback) when Ollama is unreachable.
    """
    ok, models, message = await list_ollama_models()
    if not ok:
        raise HTTPException(502, message)
    return {
        "base_url": settings.ollama_base_url.rstrip("/"),
        "models": models,
        "default": settings.ollama_model,
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
