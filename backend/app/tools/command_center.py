"""Command Center tool — "What should I work on?"

Synthesizes a prioritized work list from REAL stored data only:
  - pending tasks (TaskTool, not done)
  - due / overdue reminders (remind_at <= now, not done)
  - learning topics in progress (LearningTool, status != mastered)
  - recent projects (knowledge graph entities of type "project")

Output is prioritized markdown:
  ## Most urgent   — overdue / due-today items first
  ## Up next       — everything else actionable
  ## Also on your radar — learning topics, projects

If nothing is stored anywhere, it says so honestly instead of inventing.
"""
from __future__ import annotations

from datetime import datetime

from app.tools.base import BaseTool, ToolError


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except (ValueError, TypeError):
        return None


def _task_open(t: dict) -> bool:
    # Tasks store "done"; check "completed" too for older records.
    return not t.get("done") and not t.get("completed")


class CommandCenterTool(BaseTool):
    name = "command_center"
    description = (
        "Prioritized 'what should I work on' summary built from real "
        "tasks, reminders, learning topics and projects."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    output_schema = {"priorities": "string"}
    permission = "read"

    async def _run(self, **kwargs) -> dict:
        from app.tools.learning import LearningTool
        from app.tools.reminders import ReminderTool
        from app.tools.tasks import TaskTool
        from app.services import knowledge_graph

        now = datetime.now()
        today = now.date()

        # --- Pending tasks ---
        pending_tasks: list[dict] = []
        try:
            out = await TaskTool().execute(action="list")
            for t in out.get("tasks", []) or []:
                if isinstance(t, dict) and _task_open(t):
                    pending_tasks.append(t)
        except Exception:
            pass

        # --- Due / overdue reminders ---
        overdue: list[dict] = []
        due_today: list[dict] = []
        try:
            out = await ReminderTool().execute(action="list")
            for r in out.get("reminders", []) or []:
                if not isinstance(r, dict) or r.get("done"):
                    continue
                dt = _parse_dt(r.get("remind_at"))
                if dt is None:
                    continue
                if dt <= now and dt.date() < today:
                    overdue.append(r)
                elif dt <= now or dt.date() == today:
                    due_today.append(r)
        except Exception:
            pass

        # --- Learning topics in progress ---
        learning: list[dict] = []
        try:
            out = await LearningTool().execute(action="topics")
            for t in out.get("topics", []) or []:
                if isinstance(t, dict) and t.get("status") != "mastered":
                    learning.append(t)
        except Exception:
            pass

        # --- Recent projects ---
        projects: list[str] = []
        try:
            for e in knowledge_graph.find_entities("project") or []:
                name = (e.get("name") or "").strip()
                if name:
                    projects.append(name)
        except Exception:
            pass

        has_any = bool(pending_tasks or overdue or due_today or learning or projects)
        if not has_any:
            return {
                "priorities": (
                    "I don't have any tasks, reminders, or projects tracked yet — "
                    "tell me what you're working on and I'll keep track."
                )
            }

        lines = ["# 🎯 Command Center", ""]
        lines.append("## Most urgent")
        lines.append("")
        if overdue or due_today:
            if overdue:
                lines.append("**Overdue reminders:**")
                for r in overdue:
                    lines.append(f"- ⏰ {r.get('title', 'Untitled')}")
            if due_today:
                lines.append("**Due today:**")
                for r in due_today:
                    lines.append(f"- ⏰ {r.get('title', 'Untitled')}")
            lines.append("")
        # Tasks due today or overdue go into "Most urgent" too.
        urgent_tasks = [
            t for t in pending_tasks
            if (lambda d: d is not None and d.date() <= today)(_parse_dt(t.get("due")))
        ]
        if urgent_tasks:
            lines.append("**Tasks due now:**")
            for t in urgent_tasks[:5]:
                lines.append(f"- ✅ {t.get('title', 'Untitled')}")
            lines.append("")
        if len(lines) <= 4:
            lines.append("Nothing overdue or due today. Nice — you're clear on the urgent stuff.")
            lines.append("")

        lines.append("## Up next")
        lines.append("")
        rest_tasks = [t for t in pending_tasks if t not in urgent_tasks]
        if rest_tasks:
            for t in rest_tasks[:7]:
                lines.append(f"- {t.get('title', 'Untitled')}")
            if len(rest_tasks) > 7:
                lines.append(f"- …and {len(rest_tasks) - 7} more tasks")
        elif not overdue and not due_today:
            lines.append("No pending tasks or reminders on the list.")
        lines.append("")

        lines.append("## Also on your radar")
        lines.append("")
        if learning:
            for t in learning[:5]:
                lines.append(f"- 📚 {t.get('topic', '')} ({t.get('status', 'started')})")
            if len(learning) > 5:
                lines.append(f"- …and {len(learning) - 5} more learning topics")
        if projects:
            for p in projects[:5]:
                lines.append(f"- 🕷 Project: {p}")
        if not learning and not projects:
            lines.append("Nothing tracked here yet.")
        lines.append("")

        return {"priorities": "\n".join(lines).strip()}
