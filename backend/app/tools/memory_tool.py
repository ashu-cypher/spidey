import re
import uuid
from datetime import datetime, timezone

from app.tools.base import BaseTool, ToolError

_STOPWORDS = {
    "what", "do", "you", "know", "about", "my", "the", "a", "an", "is", "are",
    "am", "i", "me", "that", "and", "or", "to", "of", "in", "on", "for",
    "with", "remember", "recall", "tell", "please",
}

_CATEGORY_RULES = [
    ("preference", ("prefer", "like", "love", "favorite", "favourite"), 0.8),
    ("goal", ("goal", "want to", "plan to", "planning to"), 0.8),
    ("skill", ("learning", "studying", "study"), 0.75),
    ("project", ("project", "building", "working on"), 0.75),
    ("temporary", ("temporary", "later", "remind"), 0.3),
]

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryTool(BaseTool):
    name = "memory"
    description = "Stores and recalls facts about the user."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "content": {"type": "string"},
            "query": {"type": "string"},
            "id": {"type": "string"},
            "limit": {"type": "integer"},
            "category": {"type": "string"},
            "importance": {"type": "number"},
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}

    def __init__(self) -> None:
        self._store: list[dict] = []

    @staticmethod
    def _categorize(content: str) -> tuple[str, float]:
        lowered = content.lower()
        for category, keywords, importance in _CATEGORY_RULES:
            if any(k in lowered for k in keywords):
                return category, importance
        return "general", 0.5

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(_TOKEN_RE.findall(text.lower())) - _STOPWORDS

    async def _run(self, **kwargs) -> dict:
        action = kwargs.get("action")

        if action == "save":
            content = kwargs.get("content")
            if not content:
                raise ToolError("Cannot save an empty memory.")
            category = kwargs.get("category")
            importance = kwargs.get("importance")
            if category is None or importance is None:
                auto_cat, auto_imp = self._categorize(content)
                category = category or auto_cat
                importance = auto_imp if importance is None else importance
            item = {
                "id": uuid.uuid4().hex[:8],
                "content": content,
                "category": category,
                "importance": float(importance),
                "created_at": _now_iso(),
            }
            self._store.append(item)
            return {"saved": item}

        if action == "recall":
            query = kwargs.get("query")
            if not query:
                raise ToolError("Recall needs a query.")
            limit = kwargs.get("limit", 5)
            q_tokens = self._tokens(query)
            scored: list[tuple[float, dict]] = []
            for item in self._store:
                m_tokens = self._tokens(item["content"])
                score = len(q_tokens & m_tokens) / max(1, len(q_tokens))
                if score > 0:
                    scored.append((score, item))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            return {"results": [item for _, item in scored[:limit]]}

        if action == "list":
            return {"memories": list(self._store)}

        if action == "delete":
            memory_id = kwargs.get("id")
            for item in self._store:
                if item["id"] == memory_id:
                    self._store.remove(item)
                    return {"deleted": memory_id}
            raise ToolError("Memory not found.")

        raise ToolError(f"Unknown memory action: {action!r}.")
