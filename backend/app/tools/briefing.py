"""Briefing tool — proactive daily briefing like a futuristic AI assistant.

"Brief me" / "morning briefing" generates:
- Current date and time
- Reminders due today (from the reminders tool)
- Pending tasks (from the tasks tool)
- Top news headlines (via web search)

All data is real — from the user's own tasks/reminders and live web search.
Nothing is invented.
"""
from __future__ import annotations

from datetime import datetime

from app.tools.base import BaseTool, ToolError


class BriefingTool(BaseTool):
    name = "briefing"
    description = (
        "Generates a proactive daily briefing: date, due reminders, "
        "pending tasks, and top news headlines."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    output_schema = {"briefing": "string"}
    permission = "read"

    async def _run(self, **kwargs) -> dict:
        from app.tools.reminders import ReminderTool
        from app.tools.tasks import TaskTool
        from app.tools.search import SearchTool

        now = datetime.now()
        date_str = now.strftime("%A, %B %d, %Y")
        time_str = now.strftime("%I:%M %p")

        lines = [f"# MEW Briefing — {date_str}", "", f"It's {time_str}. Here's what's on your radar.", ""]

        # Reminders due today
        try:
            reminders = ReminderTool()
            due = reminders.list_due() if hasattr(reminders, "list_due") else []
            # Also get today's reminders
            all_rem = await reminders.execute(action="list") if hasattr(reminders, "execute") else {}
            rem_list = all_rem.get("reminders", []) if isinstance(all_rem, dict) else []
            today_rem = [r for r in rem_list if self._is_today(r.get("remind_at", ""))]
            if due or today_rem:
                lines.append("## ⏰ Reminders")
                lines.append("")
                seen = set()
                for r in list(due) + today_rem:
                    title = r.get("title", "")
                    if title and title not in seen:
                        seen.add(title)
                        lines.append(f"- {title}")
                lines.append("")
        except Exception:
            pass

        # Pending tasks
        try:
            tasks = TaskTool()
            out = await tasks.execute(action="list")
            task_list = out.get("tasks", []) if isinstance(out, dict) else []
            pending = [t for t in task_list if not t.get("completed")]
            if pending:
                lines.append("## ✅ Pending Tasks")
                lines.append("")
                for t in pending[:5]:
                    lines.append(f"- {t.get('title', '')}")
                if len(pending) > 5:
                    lines.append(f"- …and {len(pending) - 5} more")
                lines.append("")
        except Exception:
            pass

        # News headlines
        try:
            search = SearchTool()
            out = await search.execute(query="top news headlines today", limit=3)
            results = out.get("results", [])
            if results:
                lines.append("## 📰 Headlines")
                lines.append("")
                for r in results:
                    lines.append(f"- {r.get('title', '')}")
                lines.append("")
        except Exception:
            pass

        if len(lines) <= 4:
            lines.append("All clear — nothing due, no pending tasks. Enjoy your day!")

        return {"briefing": "\n".join(lines).strip()}

    @staticmethod
    def _is_today(remind_at: str) -> bool:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
            return dt.date() == datetime.now().date()
        except Exception:
            return False
