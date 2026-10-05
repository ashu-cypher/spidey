from contextlib import asynccontextmanager
import asyncio
import logging
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routes import chat, events, health, knowledge, profile, protocols, resume, system, automation, whatsapp

logger = logging.getLogger("spidey")

# MISSION MEW: how often the background poller claims due reminders.
_REMINDER_POLL_SECONDS = 30


def _significant_words(text: str) -> set[str]:
    """Words with real meaning for task/reminder matching: lowercase,
    alphanumeric, longer than 3 characters."""
    return set(re.findall(r"[a-z0-9]{4,}", (text or "").lower()))


def task_matches_reminder(task_title: str, reminder_title: str) -> bool:
    """True when a pending task and a due reminder share >= 2 significant
    words — simple word-overlap linking, no NLP, no invented relations."""
    return len(
        _significant_words(task_title) & _significant_words(reminder_title)
    ) >= 2


async def _related_pending_tasks(
    reminder_title: str, limit: int = 2
) -> list[dict]:
    """Pending tasks that keyword-match a due reminder title (max `limit`)."""
    from app.tools.tasks import TaskTool

    if not _significant_words(reminder_title):
        return []
    try:
        out = await TaskTool().execute(action="list")
    except Exception:
        return []
    scored: list[tuple[int, dict]] = []
    for t in out.get("tasks", []) or []:
        if not isinstance(t, dict) or t.get("done") or t.get("completed"):
            continue
        overlap = len(
            _significant_words(t.get("title", "")) & _significant_words(reminder_title)
        )
        if overlap >= 2:
            scored.append((overlap, t))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [t for _, t in scored[:limit]]


async def _reminder_poller() -> None:
    """Every 30s, claim due reminders and publish them to the event bus.

    Never raises out of the loop: a DB hiccup is logged and the next tick
    retries. ``list_due()`` marks claimed rows notified=True, so each due
    reminder is published exactly once.

    MEW Telegram integration (spec 18): due reminders are ALSO sent via
    Telegram when configured. Telegram delivery is honest — the event
    records whether Telegram actually confirmed delivery.
    """
    from app.services.event_bus import publish
    from app.services import telegram as tg
    from app.services import whatsapp as wa
    from app.tools.reminders import ReminderTool

    tool = ReminderTool()
    while True:
        await asyncio.sleep(_REMINDER_POLL_SECONDS)
        try:
            for item in tool.list_due():
                telegram_sent = False
                telegram_error: str | None = None
                whatsapp_sent = False
                whatsapp_error: str | None = None
                # MEW 2.0 — smart contextual notification: link a due
                # reminder to pending tasks with keyword overlap, so the
                # nudge names what's still unfinished. Simple word-overlap,
                # max 2 related tasks.
                context_note = ""
                try:
                    related = await _related_pending_tasks(item.get("title", ""))
                except Exception:
                    related = []
                if related:
                    names = ", ".join(f"'{t.get('title', '')}'" for t in related[:2])
                    if len(related) == 1:
                        context_note = (
                            f"Related: your task {names} is still pending."
                        )
                    else:
                        context_note = (
                            f"Related: your tasks {names} are still pending."
                        )
                title = item.get("title", "")
                if context_note:
                    title = f"{title}\n\n{context_note}"
                # Delivery chain: WhatsApp first (user's preferred channel
                # for personal reminders), then Telegram, then in-app.
                if wa.is_configured():
                    sent, msg = await wa.send_reminder(title)
                    whatsapp_sent = sent
                    if not sent:
                        whatsapp_error = msg
                if tg.is_configured():
                    sent, msg = await tg.send_reminder(title)
                    telegram_sent = sent
                    if not sent:
                        telegram_error = msg
                publish(
                    {
                        "type": "reminder_due",
                        "reminder": item,
                        "whatsapp_sent": whatsapp_sent,
                        "whatsapp_error": whatsapp_error,
                        "telegram_sent": telegram_sent,
                        "telegram_error": telegram_error,
                        "context_note": context_note or None,
                    }
                )
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
    # Automation Engine: separate 60s scheduler loop; coexists with the
    # 30s reminder poller (independent task, independent stores).
    from app.services.scheduler import scheduler_loop

    scheduler_task = asyncio.create_task(
        scheduler_loop(), name="automation-scheduler"
    )
    try:
        yield
    finally:
        poller.cancel()
        model_poller.cancel()
        scheduler_task.cancel()


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
    app.include_router(automation.router)
    app.include_router(whatsapp.router)

    @app.get("/")
    async def root():
        return {"service": "spidey", "phase": 7}

    return app


app = create_app()
