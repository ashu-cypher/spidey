"""MEW 2.0 — Action history + undo log (spec: action history).

Append-only JSONL log of real side-effecting actions the agent performed
(task created, reminder created, file generated, research completed, ...).
Backing file: ``backend/action_log.jsonl``.

Each entry:
  {ts, action, detail, undo}

``undo`` is an optional dict describing how the action can be reversed,
e.g. {"type": "delete_task", "id": 12}. Actions with no honest reversal
(e.g. a sent Telegram message) carry ``undo=None`` and report honestly.

Thread-safe: a module-level lock guards appends; the file is opened in
append mode per write so a missing/rotated file never breaks logging.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

# backend/action_log.jsonl — two levels up from app/services/.
_LOG_PATH = Path(__file__).resolve().parents[2] / "action_log.jsonl"
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_action(action: str, detail: str, undo: dict | None = None) -> dict:
    """Append one entry to the action log. Never raises."""
    entry = {
        "ts": _now_iso(),
        "action": (action or "").strip(),
        "detail": (detail or "").strip(),
        "undo": undo,
    }
    try:
        with _lock:
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def read_actions(limit: int = 20) -> list[dict]:
    """Return the most recent ``limit`` entries, newest first."""
    limit = max(1, min(int(limit or 20), 200))
    try:
        if not _LOG_PATH.exists():
            return []
        with _lock:
            with open(_LOG_PATH, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
    except OSError:
        return []
    entries: list[dict] = []
    for line in lines[-limit:]:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("action"):
            entries.append(entry)
    entries.reverse()
    return entries


def last_undoable() -> dict | None:
    """Return the newest entry that carries an undo plan, or None."""
    for entry in read_actions(limit=200):
        if entry.get("undo"):
            return entry
    return None


def log_path() -> str:
    return os.fspath(_LOG_PATH)
