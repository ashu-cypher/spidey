from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routes import chat, events, health, knowledge, profile, protocols, resume, system

logger = logging.getLogger("spidey")

# MISSION MEW: how often the background poller claims due reminders.
_REMINDER_POLL_SECONDS = 30


async def _reminder_poller() -> None:
    """Every 30s, claim due reminders and publish them to the event bus.

    Never raises out of the loop: a DB hiccup is logged and the next tick
    retries. ``list_due()`` marks claimed rows notified=True, so each due
    reminder is published exactly once.
    """
    from app.services.event_bus import publish
    from app.tools.reminders import ReminderTool

    tool = ReminderTool()
    while True:
        await asyncio.sleep(_REMINDER_POLL_SECONDS)
        try:
            for item in tool.list_due():
                publish({"type": "reminder_due", "reminder": item})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reminder poller tick failed")


async def _model_recovery_poller() -> None:
    """Every 60s, re-probe Ollama while degraded; auto-recover when back.

    The user shouldn't need to restart the backend after starting Ollama —
    when the model comes back, the degraded flag clears, ollama becomes
    the default again, and a "model_online" event is published so the UI
    can update its status header. Never raises out of the loop.
    """
    from app.providers.manager import recheck_model
    from app.services.event_bus import publish

    while True:
        await asyncio.sleep(60)
        try:
            if await recheck_model():
                publish({"type": "model_online"})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("model recovery poller tick failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # dev convenience bootstrap; canonical schema path is `alembic upgrade head`
    # Log which embedding provider is active (print: uvicorn's default logging
    # config drops INFO from app loggers, so print guarantees visibility).
    from app.rag.embeddings import get_embedding_provider

    provider = get_embedding_provider()
    print(
        f"[spidey] Embedding provider ACTIVE: {provider.name} "
        f"(dim={provider.dim})",
        flush=True,
    )
    # MEW real-agent transformation (Phase 1): probe Ollama at startup so
    # the default provider is honest — ollama when the configured model
    # (OLLAMA_MODEL) is present, rule_based fallback with
    # model_degraded=True otherwise. An explicit persisted provider choice
    # (PUT /api/system/provider) is never overridden. startup_probe never
    # raises: an unreachable Ollama degrades, it does not crash boot.
    from app.providers.manager import startup_probe

    probe = await startup_probe()
    print(
        f"[spidey] Startup model probe: default={probe['provider']} "
        f"model={probe['model']} degraded={probe['model_degraded']}",
        flush=True,
    )
    poller = asyncio.create_task(_reminder_poller(), name="reminder-poller")
    model_poller = asyncio.create_task(
        _model_recovery_poller(), name="model-recovery-poller"
    )
    try:
        yield
    finally:
        poller.cancel()
        model_poller.cancel()


def create_app():
    app = FastAPI(title="SPIDEY", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router)
    app.include_router(health.router)
    app.include_router(knowledge.router)
    app.include_router(profile.router)
    app.include_router(resume.router)
    app.include_router(events.router)
    app.include_router(protocols.router)
    app.include_router(system.router)

    @app.get("/")
    async def root():
        return {"service": "spidey", "phase": 7}

    return app


app = create_app()
