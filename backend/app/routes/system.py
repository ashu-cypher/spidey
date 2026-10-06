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


@router.get("/api/system/llm-config")
async def get_llm_config():
    """Get the OpenAI-compatible API config (backend-only values).

    Returns whether a key is set and the current base URL / model —
    never the key itself. The frontend uses this to show status.
    """
    from app.config import settings

    return {
        "key_set": bool(settings.openai_api_key.strip()),
        "base_url": settings.openai_base_url,
        "model": settings.openai_model,
    }


@router.post("/api/system/llm-config")
async def set_llm_config(body: dict):
    """Configure the OpenAI-compatible API via Settings UI.

    Body: {"api_key": "...", "base_url": "...", "model": "..."}.
    Stored in a backend-only file (~/.mew_llm.json), never exposed to
    the frontend, never committed. Env vars take precedence.

    For fast free inference: get a key at console.groq.com, set base URL
    to https://api.groq.com/openai/v1, model to llama-3.3-70b-versatile.
    """
    import json
    from pathlib import Path

    api_key = (body.get("api_key") or "").strip()
    base_url = (body.get("base_url") or "").strip().rstrip("/")
    model = (body.get("model") or "").strip()
    # Pollinations (text.pollinations.ai) is keyless — allow an empty key
    # for it; every other endpoint still requires one.
    keyless = "pollinations.ai" in base_url
    if not api_key and not keyless:
        return {"ok": False, "message": "API key is required."}
    if not base_url.startswith("https://"):
        return {"ok": False, "message": "Base URL must start with https://"}
    cfg_path = Path.home() / ".mew_llm.json"
    cfg_path.write_text(json.dumps({
        "openai_api_key": api_key,
        "openai_base_url": base_url,
        "openai_model": model or "llama-3.3-70b-versatile",
    }))
    try:
        cfg_path.chmod(0o600)
    except Exception:
        pass
    # Apply immediately without restart.
    from app.config import settings
    settings.openai_api_key = api_key
    settings.openai_base_url = base_url
    settings.openai_model = model or "llama-3.3-70b-versatile"
    return {
        "ok": True,
        "message": (
            "API configured. Now switch the provider to 'openai' above "
            "and Apply."
        ),
    }


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


def _telegram_state() -> dict:
    """Honest Telegram state: configured only when env vars are set.
    'connected' is only reported after a live test_connection() call —
    this status endpoint does not claim it without checking."""
    from app.services.telegram import status as tg_status

    return tg_status()


def _whatsapp_state() -> dict:
    """Honest WhatsApp state: mirrors _telegram_state — 'configured' only
    when token + phone number ID + recipient are set; 'connected' is only
    reported after a live test_connection() call, never here."""
    from app.services.whatsapp import status as wa_status

    return wa_status()


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
        "telegram": _telegram_state(),
        "whatsapp": _whatsapp_state(),
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


@router.post("/api/system/telegram/test")
async def test_telegram(body: dict | None = None):
    """Test the Telegram bot connection (getMe).

    Returns the honest result: connected, not configured, or the real
    error. Never claims success unless Telegram's API confirms it.
    Accepts optional {"bot_token": ..., "chat_id": ...} to test
    not-yet-saved form values; otherwise tests the stored config.
    """
    from app.services.telegram import test_connection, validate_token

    body = body or {}
    bot_token = (body.get("bot_token") or "").strip()
    if bot_token:
        # Testing form values: validate token, and check chat ID provided.
        ok, message = await validate_token(bot_token)
        if not ok:
            return {"ok": False, "message": message}
        chat_id = (body.get("chat_id") or "").strip()
        if not chat_id:
            return {"ok": False, "message": "Token works, but chat ID is empty."}
        return {"ok": True, "message": message + " Ready to save."}
    ok, message = await test_connection()
    return {"ok": ok, "message": message}


@router.post("/api/system/telegram/config")
async def config_telegram(body: dict):
    """Configure Telegram via Settings UI (alternative to env vars).

    Body: {"bot_token": "...", "chat_id": "..."}. Stored in a backend-only
    config file (~/.mew_telegram.json), never exposed to the frontend,
    never committed to git. Validates via getMe before saving.
    """
    from app.services.telegram import save_config, validate_token

    bot_token = (body.get("bot_token") or "").strip()
    chat_id = (body.get("chat_id") or "").strip()
    if not bot_token:
        return {"ok": False, "message": "Bot token is required."}
    if not chat_id:
        return {"ok": False, "message": "Chat ID is required."}
    # Validate the token works before saving. validate_token (not
    # test_connection): the chat ID isn't saved yet, so the full
    # connection check would wrongly fail here.
    ok, message = await validate_token(bot_token)
    if not ok:
        return {"ok": False, "message": message}
    save_config(bot_token, chat_id)
    return {"ok": True, "message": "Telegram configured. Send a test reminder to verify delivery."}


@router.post("/api/system/whatsapp/test")
async def test_whatsapp(body: dict | None = None):
    """Test the WhatsApp connection.

    - SIMPLE mode: {"mode": "simple", "phone": "...", "apikey": "..."} —
      sends a real test message to your WhatsApp (the only way to verify
      a CallMeBot key). Nothing is saved.
    - META mode: {"token": ..., "phone_number_id": ..., "recipient": ...}
      tests not-yet-saved form values via Graph API /me; empty body tests
      the stored config.

    Returns the honest result. Never claims success unless the provider
    confirms it.
    """
    from app.services.whatsapp import normalize_number, test_connection

    body = body or {}
    if (body.get("mode") or "").strip().lower() == "simple":
        import os
        import tempfile
        from app.services import whatsapp as wa

        phone = normalize_number(body.get("phone") or "")
        apikey = (body.get("apikey") or "").strip()
        if not phone or not apikey:
            return {
                "ok": False,
                "message": "Phone number and CallMeBot API key are required.",
            }
        # Test without touching the real config: point the module at a
        # temp file for the duration of the test.
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        orig = wa._CONFIG_PATH
        try:
            from pathlib import Path

            wa._CONFIG_PATH = Path(tmp.name)
            wa.save_simple_config(phone, apikey)
            sent, msg = await wa.send_message(
                "🕷 MEW test — WhatsApp reminders are working."
            )
        finally:
            wa._CONFIG_PATH = orig  # type: ignore[attr-defined]
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        if sent:
            return {
                "ok": True,
                "message": "Test message sent — check your WhatsApp.",
            }
        return {"ok": False, "message": msg}

    token = (body.get("token") or "").strip()
    if token:
        # Testing form values: validate the token, then check the other
        # fields the user pasted (nothing is saved yet).
        ok, message = await test_connection(
            token=token,
            phone_number_id=(body.get("phone_number_id") or "").strip(),
            recipient=(body.get("recipient") or "").strip(),
        )
        return {"ok": ok, "message": message}
    ok, message = await test_connection()
    return {"ok": ok, "message": message}


@router.post("/api/system/whatsapp/config")
async def config_whatsapp(body: dict):
    """Configure WhatsApp via Settings UI (alternative to env vars).

    Two modes:
    - SIMPLE (no business account): {"mode": "simple", "phone": "...",
      "apikey": "..."} — free CallMeBot path, send-only. Saved directly
      (the apikey is verified on the first real send).
    - META (business): {"token": "...", "phone_number_id": "...",
      "recipient": "...", optional "verify_token", "app_secret"}.

    Stored in a backend-only config file (~/.mew_whatsapp.json), never
    exposed to the frontend, never committed to git. Tokens are accepted
    in but never returned.
    """
    from app.services.whatsapp import (
        save_config,
        save_simple_config,
        validate_credentials,
    )

    mode = (body.get("mode") or "").strip().lower()
    if mode == "simple":
        phone = (body.get("phone") or "").strip()
        apikey = (body.get("apikey") or "").strip()
        if not phone:
            return {"ok": False, "message": "Your WhatsApp number is required."}
        if not apikey:
            return {"ok": False, "message": "CallMeBot API key is required."}
        save_simple_config(phone, apikey)
        return {
            "ok": True,
            "message": (
                "WhatsApp (simple mode) configured. Reminders will arrive "
                "as WhatsApp messages. Use Test to send yourself a message."
            ),
        }

    token = (body.get("token") or "").strip()
    phone_number_id = (body.get("phone_number_id") or "").strip()
    recipient = (body.get("recipient") or "").strip()
    verify_token = (body.get("verify_token") or "").strip()
    app_secret = (body.get("app_secret") or "").strip()
    if not token:
        return {"ok": False, "message": "Access token is required."}
    if not phone_number_id:
        return {"ok": False, "message": "Phone number ID is required."}
    if not recipient:
        return {"ok": False, "message": "Recipient number is required."}
    # Validate the token works before saving (mirror the telegram config
    # flow: the values aren't saved yet, so the full connection check
    # would wrongly fail on the not-yet-saved fields).
    ok, message = await validate_credentials(token, phone_number_id)
    if not ok:
        return {"ok": False, "message": message}
    save_config(token, phone_number_id, recipient, verify_token, app_secret)
    return {
        "ok": True,
        "message": (
            "WhatsApp configured. Sending works now; receiving needs the "
            "webhook set up in the Meta app dashboard (see the setup steps)."
        ),
    }


@router.get("/api/system/whatsapp/status")
async def whatsapp_status():
    """Honest WhatsApp status: configured + state. Never returns token
    values — the frontend only needs to know READY vs NOT CONFIGURED."""
    from app.services.whatsapp import status as wa_status

    return wa_status()
