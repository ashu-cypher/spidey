"""Phase 5 — document tool: Spidey-generated notes saved as real files.

Actions: create {"title", "content", "format": "md"|"txt"} / get / list /
delete. Files live under ``backend/generated/`` (gitignored); a small JSON
index keeps title/format metadata durable across restarts.

Result keys: "document" / "documents" / "deleted".
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.tools.base import BaseTool, ToolError

_GENERATED_DIR = Path(__file__).resolve().parents[2] / "generated"
_INDEX_FILE = _GENERATED_DIR / "docs_index.json"
_FORMATS = ("md", "txt")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentTool(BaseTool):
    name = "documents"
    description = "Creates, reads, lists and deletes generated documents/notes."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "format": {"type": "string"},
            "id": {"type": "string"},
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}
    action_permissions = {"create": "low_write", "delete": "confirm"}

    def __init__(self) -> None:
        _GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # -- index ------------------------------------------------------------
    def _load_index(self) -> dict:
        try:
            return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_index(self, index: dict) -> None:
        _INDEX_FILE.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def _public(self, meta: dict) -> dict:
        return {
            "id": meta["id"],
            "title": meta["title"],
            "format": meta["format"],
            "created_at": meta.get("created_at"),
            "download_url": f"/api/docs/{meta['id']}/download",
        }

    # -- actions ----------------------------------------------------------
    async def _run(self, **kwargs) -> dict:
        action = kwargs.get("action")

        if action == "create":
            return self._create(kwargs)
        if action == "get":
            return self._get(kwargs)
        if action == "list":
            return self._list()
        if action == "delete":
            return self._delete(kwargs)
        raise ToolError(f"Unknown document action: {action!r}.")

    def _create(self, kwargs: dict) -> dict:
        title = (kwargs.get("title") or "").strip() or "Untitled document"
        content = kwargs.get("content") or ""
        fmt = (kwargs.get("format") or "txt").strip().lower()
        if fmt not in _FORMATS:
            raise ToolError("Document format must be 'md' or 'txt'.")
        doc_id = uuid.uuid4().hex
        filename = f"{doc_id}.{fmt}"
        (_GENERATED_DIR / filename).write_text(content, encoding="utf-8")
        meta = {
            "id": doc_id,
            "title": title,
            "format": fmt,
            "filename": filename,
            "created_at": _utcnow_iso(),
        }
        index = self._load_index()
        index[doc_id] = meta
        self._save_index(index)
        return {"document": self._public(meta)}

    def _get(self, kwargs: dict) -> dict:
        meta = self._load_index().get(kwargs.get("id") or "")
        if meta is None:
            raise ToolError("Document not found.")
        path = _GENERATED_DIR / meta["filename"]
        if not path.exists():
            raise ToolError("Document file is missing.")
        public = self._public(meta)
        public["path"] = str(path)  # server-side only; used by the download route
        return {"document": public}

    def _list(self) -> dict:
        index = self._load_index()
        docs = sorted(
            (self._public(m) for m in index.values()),
            key=lambda d: d.get("created_at") or "",
            reverse=True,
        )
        return {"documents": docs}

    def _delete(self, kwargs: dict) -> dict:
        doc_id = kwargs.get("id") or ""
        index = self._load_index()
        meta = index.get(doc_id)
        if meta is None:
            raise ToolError("Document not found.")
        path = _GENERATED_DIR / meta["filename"]
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise ToolError("Could not delete the document file.")
        title = meta["title"]
        del index[doc_id]
        self._save_index(index)
        return {"deleted": doc_id, "title": title}
