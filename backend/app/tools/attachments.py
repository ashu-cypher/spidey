"""MEW Phase 2 — conversational file/document management (spec section 2).

A real, thin tool over the ``conversation_attachments`` rows written by
POST /api/chat/attach. Actions:

* ``list``   — rows for a conversation_id (filename, kind, created_at).
* ``search`` — rows whose extracted_text contains a keyword (case-insensitive
  LIKE), returning filename + a text snippet around the first hit.
* ``delete`` — delete one attachment row by id (destructive: confirm-gated).

Everything returned is real stored data; nothing is invented.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.database import get_session
from app.models import ConversationAttachment
from app.tools.base import BaseTool, ToolError

_SNIPPET_RADIUS = 120


def _to_dict(row: ConversationAttachment) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "kind": row.kind,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _snippet(text: str, keyword: str) -> str:
    lowered = text.lower()
    idx = lowered.find(keyword.lower())
    if idx < 0:
        return text[: 2 * _SNIPPET_RADIUS]
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(text), idx + len(keyword) + _SNIPPET_RADIUS)
    piece = text[start:end]
    if start > 0:
        piece = "…" + piece
    if end < len(text):
        piece = piece + "…"
    return piece


class AttachmentTool(BaseTool):
    name = "attachments"
    description = "Lists, searches, and deletes files attached to a conversation."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "conversation_id": {"type": "string"},
            "query": {"type": "string"},
            "id": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}
    # delete is destructive -> confirmation gate (spec 21). list/search are reads.
    permission = "read"
    action_permissions = {"delete": "confirm"}

    async def _run(self, **kwargs) -> dict:
        action = kwargs.get("action")
        if action == "list":
            return self._list(kwargs)
        if action == "search":
            return self._search(kwargs)
        if action == "delete":
            return self._delete(kwargs)
        raise ToolError(f"Unknown attachment action: {action!r}.")

    @staticmethod
    def _conversation_id(kwargs: dict) -> str:
        conversation_id = (kwargs.get("conversation_id") or "").strip()
        if not conversation_id:
            raise ToolError(
                "I need to know which conversation's files you mean."
            )
        return conversation_id

    def _list(self, kwargs: dict) -> dict:
        conversation_id = self._conversation_id(kwargs)
        limit = int(kwargs.get("limit") or 20)
        with get_session() as session:
            rows = (
                session.execute(
                    select(ConversationAttachment)
                    .where(
                        ConversationAttachment.conversation_id == conversation_id
                    )
                    .order_by(ConversationAttachment.created_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return {"attachments": [_to_dict(r) for r in rows]}

    def _search(self, kwargs: dict) -> dict:
        conversation_id = self._conversation_id(kwargs)
        query = (kwargs.get("query") or "").strip()
        if not query:
            raise ToolError("Search needs a keyword.")
        limit = int(kwargs.get("limit") or 10)
        with get_session() as session:
            rows = (
                session.execute(
                    select(ConversationAttachment)
                    .where(
                        ConversationAttachment.conversation_id
                        == conversation_id,
                        func.lower(ConversationAttachment.extracted_text).like(
                            f"%{query.lower()}%"
                        ),
                    )
                    .order_by(ConversationAttachment.created_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            matches = [
                {
                    **_to_dict(r),
                    "snippet": _snippet(r.extracted_text or "", query),
                }
                for r in rows
            ]
            return {"matches": matches, "query": query}

    def _delete(self, kwargs: dict) -> dict:
        attachment_id = kwargs.get("id")
        with get_session() as session:
            row = session.get(ConversationAttachment, attachment_id)
            if row is None:
                raise ToolError("Attachment not found.")
            info = _to_dict(row)
            session.delete(row)
            return {"deleted": attachment_id, "filename": info["filename"]}
