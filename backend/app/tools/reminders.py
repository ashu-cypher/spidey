"""Phase 5 — reminder tool (DB-backed).

Actions: create {"title", "remind_at"?: iso str} / list / get / complete /
delete on the ``reminders`` table. Result keys "reminder"/"reminders"/"deleted".

Also owns reminder time/title extraction from chat text, which the agent
reuses (classifier only detects the intent).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import get_session
from app.models import Reminder
from app.tools.base import BaseTool, ToolError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_reminder_at(text: str) -> datetime:
    """Parse simple time expressions; default is tomorrow 9am when vague.

    Hindi/Hinglish is supported deterministically: "kal (9 baje)?" ->
    tomorrow, "parso" -> the day after tomorrow, "aaj raat" -> today 20:00
    ("aaj raat 9 baje" = 21:00, the hour read as PM), and a bare
    "(\\d{1,2}) baje" -> that hour today, else tomorrow (same rollover as
    the English "at 5pm").

    NOTE: all times are resolved against the SERVER clock
    (``datetime.now(timezone.utc)``), not the user's timezone.
    """
    lowered = (text or "").lower()
    now = _utcnow()

    in_match = re.search(r"\bin\s+(\d+)\s+(hours?|minutes?|days?)\b", lowered)
    if in_match:
        n = int(in_match.group(1))
        unit = in_match.group(2)
        if "hour" in unit:
            delta = timedelta(hours=n)
        elif "day" in unit:
            delta = timedelta(days=n)
        else:
            delta = timedelta(minutes=n)
        return now + delta

    base = now
    explicit_time = False
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridiem = m.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        base = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        explicit_time = True
    elif "tonight" in lowered or "this evening" in lowered:
        base = base.replace(hour=20, minute=0, second=0, microsecond=0)
        explicit_time = True
    elif re.search(r"\bthis\s+morning\b", lowered):
        base = base.replace(hour=9, minute=0, second=0, microsecond=0)
        explicit_time = True

    if not explicit_time:
        # Hindi/Hinglish time expressions (server clock, see docstring).
        hi_baje = re.search(r"\b(\d{1,2})\s*baje\b", lowered)
        hi_hour = int(hi_baje.group(1)) % 24 if hi_baje else None
        is_kal = re.search(r"\bkal\b", lowered) is not None
        is_parso = re.search(r"\bparso\b", lowered) is not None
        is_aaj_raat = re.search(r"\baaj\s+raat\b", lowered) is not None
        if is_aaj_raat:
            # "raat" means night: a bare hour is PM ("aaj raat 9 baje" = 21:00).
            hour = hi_hour if hi_hour is not None else 20
            if hi_hour is not None and hi_hour < 12:
                hour += 12
            return now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if is_kal or is_parso:
            days = 2 if is_parso else 1
            hour = hi_hour if hi_hour is not None else 9
            target = now + timedelta(days=days)
            return target.replace(hour=hour, minute=0, second=0, microsecond=0)
        if hi_hour is not None:
            # Bare "N baje": that hour today, else tomorrow — mirrors the
            # English "at 5pm" rollover below.
            target = now.replace(
                hour=hi_hour, minute=0, second=0, microsecond=0
            )
            if target <= now:
                target = target + timedelta(days=1)
            return target

    if "tomorrow" in lowered:
        target = now + timedelta(days=1)
        if explicit_time:
            target = target.replace(
                hour=base.hour, minute=base.minute, second=0, microsecond=0
            )
        else:
            target = target.replace(hour=9, minute=0, second=0, microsecond=0)
        return target
    if "today" in lowered:
        return base
    if explicit_time:
        # "remind me at 5pm": today if the time is still ahead, else tomorrow.
        if base <= now:
            base = base + timedelta(days=1)
        return base
    # Vague ("remind me to call mom") -> tomorrow 9am.
    target = now + timedelta(days=1)
    return target.replace(hour=9, minute=0, second=0, microsecond=0)


def extract_reminder_title(text: str) -> str:
    """Pull the reminder subject out of e.g. 'remind me to call mom tomorrow'.

    Hindi/Hinglish ("bhai kal 9 baje mujhe assignment yaad dila dena" ->
    "assignment"): the subject is whatever sits between the stripped
    vocatives/time expressions and the yaad-dila verb phrase.
    """
    text = text or ""
    # Relative/absolute phrasing: "remind me in 20 minutes to study",
    # "remind me at 5pm to call mom" — the subject follows the final "to".
    m = re.search(
        r"remind me (?:in\s+\d+\s+(?:hours?|minutes?|days?)"
        r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+to\s+(.+)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip().rstrip(".") or "Untitled reminder"
    m = re.search(r"remind me to\s+(.+)", text, re.IGNORECASE | re.DOTALL)
    if m:
        body = m.group(1).strip()
    elif re.search(r"yaad\s+dila(na|o|dena)?", text, re.IGNORECASE):
        return _extract_hindi_reminder_title(text)
    else:
        body = text.strip()
    body = re.sub(
        r"\s+(tomorrow|today|tonight)(\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?\s*$",
        "",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*$", "", body, flags=re.IGNORECASE
    )
    body = re.sub(
        r"\s+in\s+\d+\s+(?:hours?|minutes?|days?)\s*$", "", body, flags=re.IGNORECASE
    )
    body = re.sub(
        r"\s+this\s+(?:evening|morning|afternoon)\s*$", "", body, flags=re.IGNORECASE
    )
    # A bare "remind me at 5pm" / "remind me in 20 minutes" carries no
    # subject — don't leave "remind me" as the title.
    body = re.sub(r"^remind me\s*$", "", body, flags=re.IGNORECASE)
    return body.strip().rstrip(".") or "Untitled reminder"


def _extract_hindi_reminder_title(text: str) -> str:
    """Subject for Hindi/Hinglish yaad-dila phrasing.

    "bhai kal 9 baje mujhe assignment yaad dila dena" -> "assignment":
    strip leading vocatives (bhai, didi, yaar), day words and "N baje"
    time expressions, and "mujhe/mujhko", then take what precedes the
    yaad-dila verb phrase.
    """
    m = re.search(r"yaad\s+dila(na|o|dena)?", text, re.IGNORECASE)
    before = text[: m.start()] if m else text
    subj = before.strip()
    subj = re.sub(
        r"^(bhai|didi|yaar|arre|sun|suniye)\b[,\s]*", "", subj, flags=re.IGNORECASE
    )
    subj = re.sub(
        r"\b(kal|parso|aaj)(\s+raat)?\b(\s+\d{1,2}\s*baje)?",
        "",
        subj,
        flags=re.IGNORECASE,
    )
    subj = re.sub(r"\b\d{1,2}\s*baje\b", "", subj, flags=re.IGNORECASE)
    subj = re.sub(r"\b(mujhe|mujhko|mereko)\b", "", subj, flags=re.IGNORECASE)
    subj = re.sub(r"\s+", " ", subj).strip().rstrip(".")
    return subj or "Untitled reminder"


def _coerce_remind_at(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except ValueError:
        return None


def _to_dict(row: Reminder) -> dict:
    return {
        "id": row.id,
        "title": row.text,
        "text": row.text,
        "remind_at": row.remind_at.isoformat() if row.remind_at else None,
        "done": bool(row.done),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class ReminderTool(BaseTool):
    name = "reminders"
    description = "Creates, lists, completes and deletes reminders."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "title": {"type": "string"},
            "id": {"type": "string"},
            "remind_at": {"type": "string"},
            "done": {"type": "boolean"},
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}
    action_permissions = {
        "create": "low_write",
        "complete": "low_write",
        "set_done": "low_write",
        "delete": "confirm",
    }

    async def _run(self, **kwargs) -> dict:
        action = kwargs.get("action")

        if action == "create":
            return self._create(kwargs)
        if action == "list":
            return self._list(kwargs)
        if action == "get":
            return self._get(kwargs)
        if action == "complete":
            return self._set_done(kwargs.get("id"), True)
        if action == "set_done":
            done = kwargs.get("done")
            return self._set_done(kwargs.get("id"), bool(done) if done is not None else True)
        if action == "delete":
            return self._delete(kwargs)
        raise ToolError(f"Unknown reminder action: {action!r}.")

    def _create(self, kwargs: dict) -> dict:
        title = (kwargs.get("title") or kwargs.get("text") or "").strip()
        if not title:
            raise ToolError("Cannot create a reminder without a title.")
        with get_session() as session:
            row = Reminder(
                user_id=kwargs.get("user_id") or "local",
                text=title,
                remind_at=_coerce_remind_at(kwargs.get("remind_at")),
                done=False,
            )
            session.add(row)
            session.flush()
            return {"reminder": _to_dict(row)}

    def list_due(self, user_id: str = "local") -> list[dict]:
        """Claim reminders whose time has come.

        Returns reminders with ``remind_at <= now``, ``done=False`` and
        ``notified=False``, marking each ``notified=True`` in the same
        transaction so a second call does not return them again.
        """
        now = _utcnow()
        with get_session() as session:
            rows = session.execute(
                select(Reminder)
                .where(Reminder.user_id == user_id)
                .where(Reminder.done.is_(False))
                .where(Reminder.notified.is_(False))
                .where(Reminder.remind_at.is_not(None))
                .where(Reminder.remind_at <= now)
                .order_by(Reminder.remind_at.asc())
            ).scalars().all()
            claimed = [
                {"id": r.id, "title": r.text, "remind_at": r.remind_at.isoformat()}
                for r in rows
            ]
            for r in rows:
                r.notified = True
            session.flush()
            return claimed

    def _list(self, kwargs: dict) -> dict:
        with get_session() as session:
            rows = session.execute(
                select(Reminder)
                .where(Reminder.user_id == (kwargs.get("user_id") or "local"))
                .order_by(Reminder.created_at.asc())
            ).scalars().all()
            return {"reminders": [_to_dict(r) for r in rows]}

    def _get(self, kwargs: dict) -> dict:
        reminder_id = kwargs.get("id")
        with get_session() as session:
            row = session.get(Reminder, reminder_id)
            if row is None:
                raise ToolError("Reminder not found.")
            return {"reminder": _to_dict(row)}

    def _set_done(self, reminder_id: str | None, done: bool) -> dict:
        with get_session() as session:
            row = session.get(Reminder, reminder_id)
            if row is None:
                raise ToolError("Reminder not found.")
            row.done = done
            session.flush()
            return {"reminder": _to_dict(row)}

    def _delete(self, kwargs: dict) -> dict:
        reminder_id = kwargs.get("id")
        with get_session() as session:
            row = session.get(Reminder, reminder_id)
            if row is None:
                raise ToolError("Reminder not found.")
            title = row.text
            session.delete(row)
            return {"deleted": reminder_id, "title": title}
