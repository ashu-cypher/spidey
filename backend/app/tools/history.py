"""Action history tool (MEW 2.0).

Actions:
  list   recent agent actions (from the append-only action log)
  undo   undo the last undoable action:
           task_created      -> delete the created task
           reminder_created  -> delete the created reminder
           file_generated    -> delete the generated file
         Honestly refuses when there is nothing undoable:
           "can't undo a sent Telegram message", etc.

The log itself is written by the agent after successful tool runs
(see app/services/action_log.py and the hook in spidey_agent.py).
"""
from __future__ import annotations

import os

from app.tools.base import BaseTool, ToolError
from app.services import action_log


class HistoryTool(BaseTool):
    name = "history"
    description = (
        "Show what MEW recently changed (action history) and undo the "
        "last undoable action."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "undo"]},
            "limit": {"type": "integer", "description": "how many actions to list"},
        },
        "required": ["action"],
    }
    output_schema = {"result": "object"}
    permission = "read"
    action_permissions = {"undo": "low_write"}

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "").strip().lower()
        if action == "list":
            limit = kwargs.get("limit") or 10
            actions = action_log.read_actions(limit=limit)
            return {
                "actions": [
                    {
                        "ts": a.get("ts", ""),
                        "action": a.get("action", ""),
                        "detail": a.get("detail", ""),
                        "undoable": bool(a.get("undo")),
                    }
                    for a in actions
                ]
            }
        if action == "undo":
            return {"undone": await self._undo_last()}
        raise ToolError(f"Unknown history action: {action!r}.")

    async def _undo_last(self) -> dict:
        entry = action_log.last_undoable()
        if entry is None:
            recent = action_log.read_actions(limit=5)
            if recent:
                return {
                    "ok": False,
                    "message": (
                        "There's nothing I can undo — the most recent "
                        f"action was \"{recent[0].get('detail', 'unknown')}\", "
                        "which can't be reversed (e.g. a sent message)."
                    ),
                }
            return {
                "ok": False,
                "message": "I haven't changed anything yet — nothing to undo.",
            }
        plan = entry["undo"] or {}
        kind = plan.get("type")
        try:
            if kind == "delete_task":
                from app.tools.tasks import TaskTool

                await TaskTool().execute(action="delete", id=plan.get("id"))
            elif kind == "delete_reminder":
                from app.tools.reminders import ReminderTool

                await ReminderTool().execute(action="delete", id=plan.get("id"))
            elif kind == "delete_file":
                path = plan.get("path") or ""
                if not path or not os.path.isfile(path):
                    raise ToolError(
                        "The generated file is already gone — nothing to undo."
                    )
                os.remove(path)
            else:
                raise ToolError(
                    "I can't undo that one — some actions (like a sent "
                    "Telegram message) can't be reversed."
                )
        except ToolError as exc:
            return {"ok": False, "message": exc.user_message}
        # Record the undo itself so history stays truthful.
        action_log.log_action(
            "undo",
            f"Undid: {entry.get('detail', entry.get('action', ''))}",
            undo=None,
        )
        return {
            "ok": True,
            "message": f"Undone: {entry.get('detail', entry.get('action', ''))}",
            "undid_action": entry.get("action"),
        }
