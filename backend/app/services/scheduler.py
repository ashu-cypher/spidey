"""Automation Engine — persistent job scheduler for MEW.

A 60s background tick loads due, enabled jobs from a small SQLite table and
executes each exactly once per due window:

- SQLite table ``jobs``: id, kind, name, schedule, payload (JSON), enabled,
  last_run (UTC epoch), last_hash (content hash for change detection),
  created_at, updated_at.
- Schedule grammar: ``daily@HH:MM``, ``weekly@DAY@HH:MM``
  (DAY = mon..sun), ``interval@MINUTES``, ``once@<ISO-8601>``.
  Schedule wall-clock times are interpreted in the job payload's
  ``timezone`` (default UTC); ``last_run`` is always a UTC epoch.
- Exactly-once: the job row is *claimed* (last_run updated under a
  conditional UPDATE) BEFORE executing, so a second tick — or a second
  process — can never double-send.
- Notification intelligence: quiet hours (default 22:00–07:00 in the
  payload's ``user_timezone``, default Asia/Calcutta) postpone a due job
  instead of claiming it; briefings whose content hash is unchanged since
  the last run are skipped instead of re-sent.
- Delivery order: WhatsApp (if ``app.services.whatsapp`` exists and is
  configured — guarded, a parallel worker may be building it) -> Telegram
  -> in-app event-bus publish only. ``delivered=True`` is reported ONLY
  when a channel API confirmed the send.

This loop is separate from the 30s reminder poller in app/main.py; they
coexist as independent asyncio tasks.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("spidey")

_SCHEDULER_TICK_SECONDS = 60

_DB_PATH = Path(__file__).resolve().parents[2] / "scheduler_jobs.db"
_DB_OVERRIDE: str | None = None

_lock = threading.RLock()

VALID_KINDS = {"daily_briefing", "news_briefing", "weekly_review", "reminder"}

_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def set_db_path(path: str | None) -> None:
    """Test hook: point the job store at a throwaway database."""
    global _DB_OVERRIDE
    _DB_OVERRIDE = path


def _db_path() -> str:
    return (
        _DB_OVERRIDE
        or os.environ.get("SPIDEY_SCHEDULER_DB")
        or str(_DB_PATH)
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                schedule TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run REAL,
                last_hash TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_enabled ON jobs(enabled)")


_init()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _job_tz(payload: dict) -> ZoneInfo:
    name = (payload or {}).get("timezone") or "UTC"
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


# --- schedule parsing -------------------------------------------------------

def parse_schedule(schedule: str) -> dict:
    """Parse the schedule grammar into a spec dict. Raises ValueError."""
    raw = (schedule or "").strip()
    if raw.startswith("daily@"):
        hhmm = raw[len("daily@"):]
        hour, minute = _parse_hhmm(hhmm)
        return {"type": "daily", "hour": hour, "minute": minute}
    if raw.startswith("weekly@"):
        rest = raw[len("weekly@"):]
        if "@" not in rest:
            raise ValueError(
                f"Bad weekly schedule {raw!r}: want weekly@DAY@HH:MM."
            )
        day_part, hhmm = rest.split("@", 1)
        day = _WEEKDAYS.get(day_part.strip().lower())
        if day is None:
            raise ValueError(
                f"Bad weekday {day_part!r} in {raw!r}: use mon..sun."
            )
        hour, minute = _parse_hhmm(hhmm)
        return {"type": "weekly", "weekday": day, "hour": hour, "minute": minute}
    if raw.startswith("interval@"):
        try:
            minutes = int(raw[len("interval@"):].strip())
        except ValueError:
            raise ValueError(
                f"Bad interval schedule {raw!r}: want interval@MINUTES."
            ) from None
        if minutes <= 0:
            raise ValueError("interval@MINUTES must be positive.")
        return {"type": "interval", "minutes": minutes}
    if raw.startswith("once@"):
        iso = raw[len("once@"):].strip()
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(
                f"Bad one-shot schedule {raw!r}: want once@<ISO-8601>."
            ) from None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return {"type": "once", "at": dt.astimezone(timezone.utc)}
    raise ValueError(
        f"Bad schedule {raw!r}: want daily@HH:MM, weekly@DAY@HH:MM, "
        "interval@MINUTES, or once@<ISO-8601>."
    )


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    try:
        hour_s, minute_s = hhmm.strip().split(":")
        hour, minute = int(hour_s), int(minute_s)
    except ValueError:
        raise ValueError(
            f"Bad time {hhmm!r}: want HH:MM (24h)."
        ) from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Bad time {hhmm!r}: want HH:MM (24h).")
    return hour, minute


def last_occurrence(spec: dict, now: datetime, tz: ZoneInfo) -> datetime | None:
    """Most recent scheduled occurrence at or before ``now`` (UTC)."""
    local = now.astimezone(tz)
    stype = spec["type"]
    if stype == "daily":
        occ = local.replace(
            hour=spec["hour"], minute=spec["minute"], second=0, microsecond=0
        )
        if occ > local:
            occ -= timedelta(days=1)
        return occ.astimezone(timezone.utc)
    if stype == "weekly":
        occ = local.replace(
            hour=spec["hour"], minute=spec["minute"], second=0, microsecond=0
        )
        delta_days = (local.weekday() - spec["weekday"]) % 7
        occ -= timedelta(days=delta_days)
        if occ > local:
            occ -= timedelta(weeks=1)
        return occ.astimezone(timezone.utc)
    if stype == "once":
        at = spec["at"]
        return at if at <= now else None
    return None  # interval: handled via last_run delta


def job_due(job: dict, now: datetime | None = None) -> tuple[bool, float | None]:
    """(due, claim_epoch). Pure logic — safe to unit test."""
    now = now or _utcnow()
    payload = job.get("payload") or {}
    spec = parse_schedule(job["schedule"])
    last_run = job.get("last_run")
    stype = spec["type"]
    if stype == "interval":
        minutes = spec["minutes"]
        if last_run is None:
            return True, now.timestamp()
        if now.timestamp() - last_run >= minutes * 60:
            return True, now.timestamp()
        return False, None
    occ = last_occurrence(spec, now, _job_tz(payload))
    if occ is None:
        return False, None
    occ_epoch = occ.timestamp()
    if last_run is None or occ_epoch > last_run:
        return True, occ_epoch
    return False, None


# --- quiet hours ------------------------------------------------------------

def in_quiet_hours(payload: dict, now: datetime | None = None) -> bool:
    """True when ``now`` falls inside the quiet window (user-local).

    Default window 22:00–07:00 in the payload's ``user_timezone``
    (default Asia/Calcutta). ``quiet_hours: null`` disables.
    """
    now = now or _utcnow()
    qh = (payload or {}).get("quiet_hours", ["22:00", "07:00"])
    if qh is None:
        return False
    try:
        start_s, end_s = qh
        sh, sm = _parse_hhmm(start_s)
        eh, em = _parse_hhmm(end_s)
    except (ValueError, TypeError):
        return False
    tzname = (payload or {}).get("user_timezone") or "Asia/Calcutta"
    try:
        tz = ZoneInfo(str(tzname))
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    t = now.astimezone(tz).time().replace(second=0, microsecond=0)
    start = datetime.min.time().replace(hour=sh, minute=sm)
    end = datetime.min.time().replace(hour=eh, minute=em)
    if start <= end:
        return start <= t < end
    return t >= start or t < end


# --- job store --------------------------------------------------------------

def _row_to_job(row: sqlite3.Row) -> dict:
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "schedule": row["schedule"],
        "payload": payload,
        "enabled": bool(row["enabled"]),
        "last_run": row["last_run"],
        "last_hash": row["last_hash"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_job(
    kind: str,
    name: str,
    schedule: str,
    payload: dict | None = None,
    enabled: bool = True,
) -> dict:
    kind = (kind or "").strip()
    if kind not in VALID_KINDS:
        raise ValueError(
            f"Unknown job kind {kind!r}. Valid: {sorted(VALID_KINDS)}."
        )
    name = (name or "").strip()
    if not name:
        raise ValueError("Job needs a name.")
    parse_schedule(schedule)  # validates; raises ValueError on bad input
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object.")
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,kind,name,schedule,payload,enabled,"
            "last_run,last_hash,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, kind, name, schedule.strip(),
                json.dumps(payload or {}, ensure_ascii=False),
                1 if enabled else 0, None, None, now, now,
            ),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row)


def get_job(job_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(enabled_only: bool = False) -> list[dict]:
    with _lock, _connect() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE enabled=1 ORDER BY created_at"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
    return [_row_to_job(r) for r in rows]


def delete_job(job_id: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return cur.rowcount > 0


def claim_job(job_id: str, claim_epoch: float) -> bool:
    """Atomically claim the job for this occurrence. Exactly-once guard.

    Returns True only for the caller that wins the race: the row is
    updated only when no newer-or-equal claim already exists.
    """
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET last_run=?, updated_at=? WHERE id=?"
            " AND (last_run IS NULL OR last_run < ?)",
            (claim_epoch, time.time(), job_id, claim_epoch),
        )
        return cur.rowcount > 0


def _record_hash(job_id: str, content_hash: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE jobs SET last_hash=?, updated_at=? WHERE id=?",
            (content_hash, time.time(), job_id),
        )


def _disable_job(job_id: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE jobs SET enabled=0, updated_at=? WHERE id=?",
            (time.time(), job_id),
        )


# --- composition (all data is real) ------------------------------------------

async def compose_job_content(job: dict) -> tuple[str, str]:
    """Compose (markdown, content_hash) for a job kind. Async entry point."""
    kind = job["kind"]
    payload = job.get("payload") or {}
    if kind == "daily_briefing":
        return await _compose_daily_briefing_async()
    if kind == "news_briefing":
        from app.tools.news import daily_news, render_digest

        cats = payload.get("categories") or ["AI", "technology", "India", "world"]
        result = await daily_news(categories=cats, per_category=3)
        news = result.get("news", [])
        if not news:
            content = "# 📰 News briefing\n\n" + result.get("message", "No news found.")
        else:
            content = render_digest(news, result["fetched_at"])
        return content, hashlib.sha256(content.encode()).hexdigest()
    if kind == "weekly_review":
        return await _compose_weekly_review_async()
    if kind == "reminder":
        text = (payload.get("text") or "").strip() or "(empty reminder)"
        return f"⏰ Reminder: {text}", hashlib.sha256(text.encode()).hexdigest()
    raise ValueError(f"Unknown job kind {kind!r}.")


async def _compose_daily_briefing_async() -> tuple[str, str]:
    lines = [f"# MEW Daily Briefing — {_utcnow().strftime('%A, %B %d, %Y')}", ""]
    sections = 0

    try:
        from app.tools.reminders import ReminderTool

        out = await ReminderTool().execute(action="list")
        rems = out.get("reminders", []) if isinstance(out, dict) else []
        now = _utcnow()
        due_items = []
        for r in rems:
            if not isinstance(r, dict) or r.get("done"):
                continue
            try:
                ra = datetime.fromisoformat(
                    str(r.get("remind_at") or "").replace("Z", "+00:00")
                )
            except Exception:
                continue
            if ra.tzinfo is None:
                ra = ra.replace(tzinfo=timezone.utc)
            if ra <= now:
                due_items.append((ra, r.get("title") or r.get("text") or ""))
        if due_items:
            lines.append("## ⏰ Reminders due")
            lines.append("")
            for _ra, title in sorted(due_items)[:8]:
                lines.append(f"- {title}")
            lines.append("")
            sections += 1
    except Exception:
        logger.exception("daily briefing: reminders section failed")

    try:
        from app.tools.tasks import TaskTool

        out = await TaskTool().execute(action="list")
        tasks = out.get("tasks", []) if isinstance(out, dict) else []
        pending = [t for t in tasks if isinstance(t, dict) and not t.get("done")]
        if pending:
            lines.append("## ✅ Pending tasks")
            lines.append("")
            for t in pending[:6]:
                due = t.get("due")
                suffix = f" (due {due[:10]})" if due else ""
                lines.append(f"- {t.get('title', '')}{suffix}")
            if len(pending) > 6:
                lines.append(f"- …and {len(pending) - 6} more")
            lines.append("")
            sections += 1
    except Exception:
        logger.exception("daily briefing: tasks section failed")

    # Today's study plan + exam countdown (real planner tool data).
    try:
        from app.tools.planner import PlannerTool

        today_out = await PlannerTool().execute(action="today")
        today = today_out.get("today", {}) if isinstance(today_out, dict) else {}
        sessions = today.get("sessions", []) or []
        if sessions:
            lines.append("## 📚 Today's study plan")
            lines.append("")
            for s in sessions[:5]:
                lines.append(
                    f"- {s.get('subject', '')} — {s.get('unit_title') or s.get('unit', '')} "
                    f"({s.get('minutes', '')} min)"
                )
            lines.append("")
            sections += 1
        if today.get("assumption"):
            lines.append(f"_{today['assumption']}_")
            lines.append("")

        cd_out = await PlannerTool().execute(action="countdown")
        cd = cd_out.get("countdown", {}) if isinstance(cd_out, dict) else {}
        exams = cd.get("exams", []) or []
        if exams:
            lines.append("## 🎯 Exam countdown")
            lines.append("")
            for e in exams[:4]:
                days = e.get("days_left")
                when = f"{days} days left" if days is not None else str(e.get("date", ""))
                lines.append(
                    f"- {e.get('subject', '')} ({e.get('code', '')}): {when}"
                )
            lines.append("")
            sections += 1
    except Exception:
        # No syllabus ingested (or planner unavailable): the sections are
        # simply omitted — nothing is invented.
        pass

    try:
        from app.tools.news import daily_news

        result = await daily_news(categories=["AI", "technology"], per_category=2)
        stories = result.get("news", [])
        if stories:
            lines.append("## 📰 Headlines")
            lines.append("")
            for s in stories[:3]:
                lines.append(
                    f"- **{s.get('headline', '')}** ({s.get('source', 'web')})"
                )
            lines.append("")
            sections += 1
    except Exception:
        logger.exception("daily briefing: news section failed")

    if sections == 0:
        lines.append(
            "Nothing is scheduled today — no due reminders, no pending tasks."
        )
        lines.append("")
    content = "\n".join(lines).strip()
    return content, hashlib.sha256(content.encode()).hexdigest()


async def _compose_weekly_review_async() -> tuple[str, str]:
    lines = [f"# MEW Weekly Review — week of {_utcnow().strftime('%B %d, %Y')}", ""]
    try:
        from app.tools.tasks import TaskTool

        out = await TaskTool().execute(action="list")
        tasks = [
            t for t in (out.get("tasks", []) if isinstance(out, dict) else [])
            if isinstance(t, dict)
        ]
        done = [t for t in tasks if t.get("done")]
        pending = [t for t in tasks if not t.get("done")]
        lines.append(f"**Completed:** {len(done)} · **Remaining:** {len(pending)}")
        lines.append("")
        if pending:
            lines.append("## Still open")
            lines.append("")
            for t in pending[:8]:
                lines.append(f"- {t.get('title', '')}")
            if len(pending) > 8:
                lines.append(f"- …and {len(pending) - 8} more")
            lines.append("")
        # Honest caveat: tasks carry no completed_at timestamp, so
        # "completed this week" cannot be distinguished from older
        # completions.
        if done:
            lines.append(
                "_Note: tasks don't record when they were completed, so "
                "completed counts are all-time, not this-week-only._"
            )
            lines.append("")
    except Exception:
        logger.exception("weekly review: tasks section failed")
        lines.append("Couldn't read your tasks right now.")
        lines.append("")
    content = "\n".join(lines).strip()
    return content, hashlib.sha256(content.encode()).hexdigest()


# --- delivery ---------------------------------------------------------------

async def deliver(text: str) -> tuple[str, bool, str | None]:
    """Send ``text`` via WhatsApp -> Telegram -> in-app event.

    Returns (channel, delivered, error). ``delivered`` is True ONLY when a
    channel API confirmed the send — never claimed otherwise.
    """
    whatsapp_error: str | None = None
    try:
        from app.services import whatsapp as _wa

        is_cfg = getattr(_wa, "is_configured", None)
        send = getattr(_wa, "send_message", None)
        if callable(is_cfg) and callable(send) and is_cfg():
            ok, msg = await send(text)
            if ok:
                return "whatsapp", True, None
            whatsapp_error = msg
    except ImportError:
        pass  # parallel worker may still be building it
    except Exception as exc:
        whatsapp_error = str(exc) or repr(exc)

    telegram_error: str | None = None
    try:
        from app.services import telegram as _tg

        if _tg.is_configured():
            ok, msg = await _tg.send_message(text)
            if ok:
                return "telegram", True, None
            telegram_error = msg
    except Exception as exc:
        telegram_error = str(exc) or repr(exc)

    # Fallback: in-app event only — honestly NOT a confirmed delivery.
    try:
        from app.services.event_bus import publish

        publish(
            {
                "type": "automation_notification",
                "text": text,
                "note": "No messaging channel configured; shown in-app only.",
            }
        )
    except Exception:
        logger.exception("scheduler: event-bus fallback publish failed")
    reasons = [e for e in (whatsapp_error, telegram_error) if e]
    err = "; ".join(reasons) if reasons else "No messaging channel configured."
    return "in_app", False, err


# --- execution ----------------------------------------------------------------

async def run_job(job: dict, manual: bool = False, now: datetime | None = None) -> dict:
    """Execute one job. Claims before executing; never double-sends.

    ``manual=True`` (the /run endpoint) bypasses quiet-hours and the
    no-new-content skip, but the result still honestly reports the
    delivery channel and whether the send was confirmed.
    """
    now = now or _utcnow()
    job_id = job["id"]
    payload = job.get("payload") or {}

    if not manual and in_quiet_hours(payload, now):
        logger.info("scheduler: job %s postponed (quiet hours)", job_id)
        return {
            "ok": True, "job_id": job_id, "skipped": "quiet_hours",
            "channel": None, "delivered": False, "error": None,
        }

    due, claim_epoch = job_due(job, now)
    if not manual and not due:
        return {
            "ok": True, "job_id": job_id, "skipped": "not_due",
            "channel": None, "delivered": False, "error": None,
        }
    claim_epoch = claim_epoch if claim_epoch is not None else now.timestamp()

    # Claim BEFORE executing: the conditional UPDATE is the exactly-once
    # guard. A lost race means another tick claimed it — skip quietly.
    if not manual and not claim_job(job_id, claim_epoch):
        return {
            "ok": True, "job_id": job_id, "skipped": "already_claimed",
            "channel": None, "delivered": False, "error": None,
        }
    if manual:
        # Manual runs still record a claim so the next tick sees it ran.
        claim_job(job_id, claim_epoch)

    try:
        content, content_hash = await compose_job_content(job)
    except Exception as exc:
        logger.exception("scheduler: job %s composition failed", job_id)
        return {
            "ok": False, "job_id": job_id, "channel": None,
            "delivered": False, "error": f"Composition failed: {exc}",
        }

    # Notification intelligence: don't re-send identical briefings.
    if (
        not manual
        and job["kind"] in ("daily_briefing", "news_briefing", "weekly_review")
        and job.get("last_hash") == content_hash
    ):
        logger.info("scheduler: job %s skipped (no new content)", job_id)
        return {
            "ok": True, "job_id": job_id, "skipped": "no_new_content",
            "channel": None, "delivered": False, "error": None,
        }

    channel, delivered, error = await deliver(content)
    _record_hash(job_id, content_hash)

    # One-shot reminders fire once, then disable themselves.
    try:
        if job["kind"] == "reminder" and parse_schedule(job["schedule"])["type"] == "once":
            _disable_job(job_id)
    except Exception:
        pass

    logger.info(
        "scheduler: job %s ran kind=%s channel=%s delivered=%s",
        job_id, job["kind"], channel, delivered,
    )
    return {
        "ok": True,
        "job_id": job_id,
        "channel": channel,
        "delivered": delivered,
        "error": error,
        "content_preview": content[:300],
    }


async def run_job_now(job_id: str) -> dict:
    """Manual trigger used by POST /api/automation/jobs/{id}/run."""
    job = get_job(job_id)
    if job is None:
        return {"ok": False, "job_id": job_id, "error": "Job not found."}
    if not job["enabled"]:
        return {"ok": False, "job_id": job_id, "error": "Job is disabled."}
    return await run_job(job, manual=True)


async def run_due_jobs(now: datetime | None = None) -> list[dict]:
    """One scheduler tick: run every due, enabled job. Never raises."""
    results: list[dict] = []
    for job in list_jobs(enabled_only=True):
        try:
            results.append(await run_job(job, now=now))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("scheduler: job %s tick failed", job.get("id"))
            results.append(
                {"ok": False, "job_id": job.get("id"),
                 "error": f"Tick failed: {exc}"}
            )
    return results


async def scheduler_loop() -> None:
    """Background loop (60s tick). Never raises out of the loop."""
    while True:
        await asyncio.sleep(_SCHEDULER_TICK_SECONDS)
        try:
            await run_due_jobs()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler tick failed")
