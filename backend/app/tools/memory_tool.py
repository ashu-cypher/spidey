import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.database import get_session
from app.models import Memory
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

_TEMPORARY_TTL = timedelta(days=7)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_dict(row: Memory) -> dict:
    return {
        "id": row.id,
        "content": row.content,
        "category": row.category,
        "importance": row.importance,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


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
            "include_expired": {"type": "boolean"},
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}
    # save is a low-risk write (auto); delete needs confirmation (spec 21).
    # forget deletes ONE best-matching memory (auto); delete_all wipes the
    # whole store and is confirmation-gated like delete.
    action_permissions = {
        "save": "low_write",
        "delete": "confirm",
        "forget": "low_write",
        "delete_all": "confirm",
    }

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
            return self._save(kwargs)
        if action == "recall":
            return self._recall(kwargs)
        if action == "list":
            return self._list(kwargs)
        if action == "delete":
            return self._delete(kwargs)
        if action == "forget":
            return self._forget(kwargs)
        if action == "delete_all":
            return self._delete_all(kwargs)
        raise ToolError(f"Unknown memory action: {action!r}.")

    def _save(self, kwargs: dict) -> dict:
        content = (kwargs.get("content") or "").strip()
        if not content:
            raise ToolError("Cannot save an empty memory.")
        category = kwargs.get("category")
        importance = kwargs.get("importance")
        if category is None or importance is None:
            auto_cat, auto_imp = self._categorize(content)
            category = category or auto_cat
            importance = auto_imp if importance is None else importance
        importance = float(importance)
        expires_at = None
        if category == "temporary" or importance < 0.4:
            expires_at = _utcnow() + _TEMPORARY_TTL
        with get_session() as session:
            row = Memory(
                user_id=kwargs.get("user_id") or "local",
                content=content,
                category=category,
                importance=importance,
                expires_at=expires_at,
            )
            session.add(row)
            session.flush()
            return {"saved": _to_dict(row)}

    def _recall(self, kwargs: dict) -> dict:
        query = (kwargs.get("query") or "").strip()
        if not query:
            raise ToolError("Recall needs a query.")
        limit = int(kwargs.get("limit") or 5)
        q_tokens = self._tokens(query)
        now = _utcnow()
        with get_session() as session:
            rows = session.execute(
                select(Memory).where(
                    or_(Memory.expires_at.is_(None), Memory.expires_at > now)
                )
            ).scalars().all()
            scored: list[tuple[float, float, Memory]] = []
            for row in rows:
                m_tokens = self._tokens(row.content)
                overlap = len(q_tokens & m_tokens) / max(1, len(q_tokens))
                if overlap > 0:
                    scored.append((overlap, row.importance or 0.0, row))
            scored.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
            return {"results": [_to_dict(row) for _, _, row in scored[:limit]]}

    def _list(self, kwargs: dict) -> dict:
        include_expired = bool(kwargs.get("include_expired", False))
        now = _utcnow()
        with get_session() as session:
            stmt = select(Memory).order_by(Memory.created_at.desc())
            if not include_expired:
                stmt = stmt.where(
                    or_(Memory.expires_at.is_(None), Memory.expires_at > now)
                )
            rows = session.execute(stmt).scalars().all()
            return {"memories": [_to_dict(row) for row in rows]}

    def _delete(self, kwargs: dict) -> dict:
        memory_id = kwargs.get("id")
        with get_session() as session:
            row = session.get(Memory, memory_id)
            if row is None:
                raise ToolError("Memory not found.")
            session.delete(row)
            return {"deleted": memory_id}

    def _best_match(self, query: str) -> Memory | None:
        """The single best-matching non-expired memory for ``query``.

        Reuses the same token-overlap scoring as ``_recall`` so 'forget'
        and 'recall' agree on what matches; the highest (overlap,
        importance) row wins. Returns None when nothing overlaps.
        """
        q_tokens = self._tokens(query)
        if not q_tokens:
            return None
        now = _utcnow()
        with get_session() as session:
            rows = (
                session.execute(
                    select(Memory).where(
                        or_(Memory.expires_at.is_(None), Memory.expires_at > now)
                    )
                )
                .scalars()
                .all()
            )
            scored: list[tuple[float, float, Memory]] = []
            for row in rows:
                m_tokens = self._tokens(row.content)
                overlap = len(q_tokens & m_tokens) / max(1, len(q_tokens))
                if overlap > 0:
                    scored.append((overlap, row.importance or 0.0, row))
            if not scored:
                return None
            scored.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
            best = scored[0][2]
            session.expunge(best)
            return best

    def _forget(self, kwargs: dict) -> dict:
        """Delete the single best-matching memory for ``query``.

        Honest when nothing matches: raises a user-safe ToolError instead
        of deleting something unrelated. Bulk deletion is NOT this action —
        use delete_all (confirmation-gated).
        """
        query = (kwargs.get("query") or "").strip()
        if not query:
            raise ToolError("Forget needs something to forget — say what.")
        best = self._best_match(query)
        if best is None:
            raise ToolError(
                f"I couldn't find a memory matching {query!r} to forget."
            )
        with get_session() as session:
            row = session.get(Memory, best.id)
            if row is None:
                raise ToolError("That memory is already gone.")
            session.delete(row)
        return {"forgot": _to_dict(best)}

    def _delete_all(self, kwargs: dict) -> dict:
        """Wipe every memory for the user. Confirmation-gated (spec 21):
        the agent only reaches this after the user approved the proposal,
        and the tool honours the ``_confirmed`` marker implicitly through
        that gate (the marker kwarg is accepted and ignored)."""
        with get_session() as session:
            rows = (
                session.execute(
                    select(Memory).where(
                        Memory.user_id == (kwargs.get("user_id") or "local")
                    )
                )
                .scalars()
                .all()
            )
            count = len(rows)
            for row in rows:
                session.delete(row)
            return {"deleted": count}
