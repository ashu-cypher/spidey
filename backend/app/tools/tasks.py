"""Phase 5 — DB-backed task tool.

Same action interface as the Phase 1 in-memory version
(create/list/complete/delete; result keys "task"/"tasks"/"deleted"),
now persisted on the ``tasks`` table via SQLAlchemy. Also owns due-date
parsing, which the agent reuses for chat extraction.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.database import get_session
from app.models import Task
from app.tools.base import BaseTool, ToolError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_due(text: str) -> str | None:
    """Natural-language due phrase -> ISO date string (YYYY-MM-DD), or None."""
    if not text:
        return None
    lowered = text.lower()
    today = date.today()
    if "tomorrow" in lowered:
        return (today + timedelta(days=1)).isoformat()
    if "today" in lowered:
        return today.isoformat()
    m = re.search(r"\bin\s+(\d+)\s+days?\b", lowered)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    if "next week" in lowered:
        return (today + timedelta(days=7)).isoformat()
    return None


def _coerce_due(value: object) -> datetime | None:
    """Accept an ISO date/datetime string (or natural language) -> datetime."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None)
    except ValueError:
        pass
    iso = parse_due(text)
    if iso:
        return datetime.fromisoformat(iso)
    return None


def _to_dict(row: Task) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "due": row.due.isoformat() if row.due else None,
        "done": bool(row.done),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class TaskTool(BaseTool):
    name = "tasks"
    description = "Creates, lists, completes and deletes tasks."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "title": {"type": "string"},
            "id": {"type": "string"},
            "due": {"type": "string"},
            "done": {"type": "boolean"},
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}
    # list = read (default); create/complete are low-risk writes;
    # delete needs explicit user confirmation via chat (spec 21).
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
        raise ToolError(f"Unknown task action: {action!r}.")

    def _create(self, kwargs: dict) -> dict:
        title = (kwargs.get("title") or "").strip()
        if not title:
            raise ToolError("Cannot create a task without a title.")
        with get_session() as session:
            row = Task(
                user_id=kwargs.get("user_id") or "local",
                title=title,
                due=_coerce_due(kwargs.get("due")),
                done=False,
            )
            session.add(row)
            session.flush()
            return {"task": _to_dict(row)}

    def _list(self, kwargs: dict) -> dict:
        with get_session() as session:
            rows = session.execute(
                select(Task)
                .where(Task.user_id == (kwargs.get("user_id") or "local"))
                .order_by(Task.created_at.asc())
            ).scalars().all()
            return {"tasks": [_to_dict(r) for r in rows]}

    def _get(self, kwargs: dict) -> dict:
        task_id = kwargs.get("id")
        with get_session() as session:
            row = session.get(Task, task_id)
            if row is None:
                raise ToolError("Task not found.")
            return {"task": _to_dict(row)}

    def _set_done(self, task_id: str | None, done: bool) -> dict:
        with get_session() as session:
            row = session.get(Task, task_id)
            if row is None:
                raise ToolError("Task not found.")
            row.done = done
            session.flush()
            return {"task": _to_dict(row)}

    def _delete(self, kwargs: dict) -> dict:
        task_id = kwargs.get("id")
        with get_session() as session:
            row = session.get(Task, task_id)
            if row is None:
                raise ToolError("Task not found.")
            title = row.title
            session.delete(row)
            return {"deleted": task_id, "title": title}
