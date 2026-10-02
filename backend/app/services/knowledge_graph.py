"""Personal Knowledge Graph — structured entity/relation memory.

A small SQLite-backed graph for durable facts about the user's world:
projects, skills, documents, people, courses, goals, technologies, etc.

Schema:
  entities(id, type, name, attributes JSON, created_at, updated_at)
  relations(id, from_id, to_id, relation, created_at)

Names are matched case-insensitively; adding the same (type, name) twice
updates attributes instead of duplicating (stable IDs, spec 5).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parents[2] / "knowledge_graph.db"
# RLock: add_entity calls get_entity while holding the lock.
_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS entities(
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                attributes TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(type, name)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS relations(
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                to_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(from_id, to_id, relation)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)")


_init()

_VALID_TYPES = {
    "project", "skill", "technology", "document", "person", "course",
    "goal", "task", "interest", "repository", "assignment",
}


def _norm(name: str) -> str:
    return " ".join((name or "").strip().split())


def add_entity(entity_type: str, name: str, attributes: dict | None = None) -> dict:
    """Add (or update) an entity. Returns the entity dict."""
    entity_type = (entity_type or "").strip().lower()
    name = _norm(name)
    if not entity_type or not name:
        raise ValueError("Entity needs a type and a name.")
    if entity_type not in _VALID_TYPES:
        raise ValueError(f"Unknown entity type {entity_type!r}.")
    attrs = json.dumps(attributes or {}, ensure_ascii=False)
    now = time.time()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT id FROM entities WHERE type=? AND name=? COLLATE NOCASE",
            (entity_type, name),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE entities SET attributes=?, updated_at=? WHERE id=?",
                (attrs, now, row["id"]),
            )
            entity_id = row["id"]
        else:
            entity_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO entities(id,type,name,attributes,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (entity_id, entity_type, name, attrs, now, now),
            )
        # Read back in the same connection: the row may be uncommitted.
        row = conn.execute(
            "SELECT * FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
        return _row_to_entity(row)


def get_entity(entity_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
        return _row_to_entity(row) if row else None


def _row_to_entity(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "attributes": json.loads(row["attributes"] or "{}"),
    }


def find_entities(entity_type: str | None = None, query: str | None = None) -> list[dict]:
    with _lock, _connect() as conn:
        sql = "SELECT * FROM entities"
        clauses, params = [], []
        if entity_type:
            clauses.append("type=?")
            params.append(entity_type.strip().lower())
        if query:
            clauses.append("name LIKE ? COLLATE NOCASE")
            params.append(f"%{_norm(query)}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT 50"
        return [_row_to_entity(r) for r in conn.execute(sql, params).fetchall()]


def add_relation(from_name: str, relation: str, to_name: str) -> dict | None:
    """Link two existing entities by name. Returns the relation or None."""
    relation = (relation or "").strip().lower().replace(" ", "_")
    if not relation:
        raise ValueError("Relation needs a name.")
    with _lock, _connect() as conn:
        f = conn.execute(
            "SELECT id,name,type FROM entities WHERE name=? COLLATE NOCASE",
            (_norm(from_name),),
        ).fetchone()
        t = conn.execute(
            "SELECT id,name,type FROM entities WHERE name=? COLLATE NOCASE",
            (_norm(to_name),),
        ).fetchone()
        if not f or not t:
            return None
        rid = uuid.uuid4().hex
        conn.execute(
            "INSERT OR IGNORE INTO relations(id,from_id,to_id,relation,created_at)"
            " VALUES(?,?,?,?,?)",
            (rid, f["id"], t["id"], relation, time.time()),
        )
        return {
            "from": f["name"], "relation": relation, "to": t["name"],
            "from_type": f["type"], "to_type": t["type"],
        }


def relations_for(name: str) -> list[dict]:
    """All relations touching the named entity (both directions)."""
    with _lock, _connect() as conn:
        e = conn.execute(
            "SELECT id,name FROM entities WHERE name=? COLLATE NOCASE",
            (_norm(name),),
        ).fetchone()
        if not e:
            return []
        rows = conn.execute(
            """SELECT r.relation, a.name AS from_name, b.name AS to_name,
                      a.type AS from_type, b.type AS to_type, r.from_id
               FROM relations r
               JOIN entities a ON a.id=r.from_id
               JOIN entities b ON b.id=r.to_id
               WHERE r.from_id=? OR r.to_id=?
               ORDER BY r.created_at DESC""",
            (e["id"], e["id"]),
        ).fetchall()
        out = []
        for r in rows:
            if r["from_id"] == e["id"]:
                out.append({
                    "subject": r["from_name"], "relation": r["relation"],
                    "object": r["to_name"], "object_type": r["to_type"],
                })
            else:
                out.append({
                    "subject": r["to_name"], "relation": f"is_{r['relation']}_of",
                    "object": r["from_name"], "object_type": r["from_type"],
                })
        return out


def describe(name: str) -> dict | None:
    """Entity + its relations, for 'what do you remember about X?'."""
    ents = find_entities(query=name)
    if not ents:
        return None
    ent = ents[0]
    return {"entity": ent, "relations": relations_for(ent["name"])}


def forget_entity(name: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM entities WHERE name=? COLLATE NOCASE", (_norm(name),)
        )
        return cur.rowcount > 0


def stats() -> dict:
    with _lock, _connect() as conn:
        n_e = conn.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"]
        n_r = conn.execute("SELECT COUNT(*) c FROM relations").fetchone()["c"]
        return {"entities": n_e, "relations": n_r}
