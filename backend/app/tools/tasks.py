import uuid
from datetime import datetime, timezone

from app.tools.base import BaseTool, ToolError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}

    def __init__(self) -> None:
        self._store: list[dict] = []

    def _find(self, task_id: str) -> dict | None:
        for task in self._store:
            if task["id"] == task_id:
                return task
        return None

    async def _run(self, **kwargs) -> dict:
        action = kwargs.get("action")

        if action == "create":
            title = kwargs.get("title")
            if not title:
                raise ToolError("Cannot create a task without a title.")
            task = {
                "id": uuid.uuid4().hex[:8],
                "title": title,
                "due": kwargs.get("due"),
                "done": False,
                "created_at": _now_iso(),
            }
            self._store.append(task)
            return {"task": task}

        if action == "list":
            return {"tasks": list(self._store)}

        if action == "complete":
            task = self._find(kwargs.get("id", ""))
            if task is None:
                raise ToolError("Task not found.")
            task["done"] = True
            return {"task": task}

        if action == "delete":
            task = self._find(kwargs.get("id", ""))
            if task is None:
                raise ToolError("Task not found.")
            self._store.remove(task)
            return {"deleted": task["id"]}

        raise ToolError(f"Unknown task action: {action!r}.")
