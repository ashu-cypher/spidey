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
    """Parse simple time expressions; default is tomorrow 9am when vague."""
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
    """Pull the reminder subject out of e.g. 'remind me to call mom tomorrow'."""
    # Relative/absolute phrasing: "remind me in 20 minutes to study",
    # "remind me at 5pm to call mom" — the subject follows the final "to".
    m = re.search(
        r"remind me (?:in\s+\d+\s+(?:hours?|minutes?|days?)"
        r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+to\s+(.+)",
        text or "",
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip().rstrip(".") or "Untitled reminder"
    m = re.search(r"remind me to\s+(.+)", text or "", re.IGNORECASE | re.DOTALL)
    body = m.group(1).strip() if m else (text or "").strip()
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
