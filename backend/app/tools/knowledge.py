"""Knowledge Graph tool — structured personal knowledge (spec 5).

Actions:
  learn    extract entities/relations from a statement like
           "My main project is MEW, it uses Ollama and RAG"
  query    "what do you remember about my projects?" -> entities + relations
  describe describe one entity and everything linked to it
  list     list entities, optionally filtered by type
  forget   remove an entity (and its relations)

The graph lives in SQLite (app/services/knowledge_graph.py) — stable IDs,
no duplication, relations between user/projects/skills/documents/etc.
"""
from __future__ import annotations

import re

from app.services import knowledge_graph as kg
from app.tools.base import BaseTool, ToolError

# Conservative extractors — only high-confidence phrasings become entities.
_MY_X_IS_Y = re.compile(
    r"\bmy\s+(main\s+|current\s+)?(project|skill|course|goal|interest|assignment)s?"
    r"\s+(is|are)\s+([A-Za-z0-9][\w\s\-+.&]*?)(?:\.|,|$)",
    re.IGNORECASE,
)
_X_USES_Y = re.compile(
    r"\b([A-Z][\w\-+.]*)\s+uses?\s+([A-Za-z0-9][\w\s\-,+.&]*?)(?:\.|$)",
)
# "it uses X and Y" — pronoun referring to the entity just mentioned.
_IT_USES_Y = re.compile(
    r"\bit\s+uses?\s+([A-Za-z0-9][\w\s\-,+.&]*?)(?:\.|$)",
    re.IGNORECASE,
)
_X_IS_A_Y = re.compile(
    r"\b([A-Z][\w\-+.]*)\s+is\s+a(?:n)?\s+([\w\s\-]+?)(?:\.|,|$)",
)

_TYPE_MAP = {
    "project": "project", "skill": "skill", "course": "course",
    "goal": "goal", "interest": "interest", "assignment": "assignment",
}

_TECH_HINTS = {
    "python", "javascript", "typescript", "node", "react", "fastapi", "ollama",
    "rag", "mysql", "postgres", "postgresql", "jwt", "docker", "flask",
    "django", "pytorch", "tensorflow", "langchain", "sqlite", "redis",
}


def _split_list(text: str) -> list[str]:
    parts = re.split(r",|\band\b", text)
    return [p.strip(" .") for p in parts if p.strip(" .")]


class KnowledgeTool(BaseTool):
    name = "knowledge"
    description = (
        "Personal knowledge graph: remember entities (projects, skills, "
        "people, ...) and their relationships; query what MEW knows."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["learn", "query", "describe", "list", "forget"],
            },
            "statement": {"type": "string"},
            "query": {"type": "string"},
            "entity_type": {"type": "string"},
        },
        "required": ["action"],
    }
    output_schema = {"result": "object"}
    permission = "read"
    action_permissions = {"learn": "low_write", "forget": "low_write"}

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "").strip().lower()
        if action == "learn":
            return {"learned": self._learn(kwargs.get("statement") or "")}
        if action == "query":
            return {"answer": self._query(kwargs.get("query") or "")}
        if action == "describe":
            return {"answer": self._describe(kwargs.get("query") or "")}
        if action == "list":
            return {
                "entities": kg.find_entities(
                    entity_type=kwargs.get("entity_type") or None
                )
            }
        if action == "forget":
            name = (kwargs.get("query") or "").strip()
            if not name:
                raise ToolError("What should I forget?")
            ok = kg.forget_entity(name)
            return {"forgotten": ok, "name": name}
        raise ToolError(f"Unknown knowledge action: {action!r}.")

    # -- learn ----------------------------------------------------------
    def _learn(self, statement: str) -> list[str]:
        """Extract entities/relations from a statement. Returns what stuck."""
        learned: list[str] = []
        m = _MY_X_IS_Y.search(statement)
        if m:
            etype = _TYPE_MAP[m.group(2).lower().rstrip("s")]
            name = m.group(4).strip()
            ent = kg.add_entity(etype, name)
            learned.append(f"{etype} '{ent['name']}'")
            # "it uses X and Y" following the entity (pronoun reference).
            for it in _IT_USES_Y.finditer(statement):
                for tech in _split_list(it.group(1)):
                    if not tech:
                        continue
                    ttype = (
                        "technology"
                        if tech.lower() in _TECH_HINTS
                        else "skill"
                    )
                    kg.add_entity(ttype, tech)
                    kg.add_relation(name, "uses", tech)
                    learned.append(f"'{name}' uses '{tech}'")
            # "Name uses X" with an explicit capitalized subject.
            for u in _X_USES_Y.finditer(statement):
                if u.group(1).lower() not in name.lower():
                    continue
                for tech in _split_list(u.group(2)):
                    if not tech:
                        continue
                    ttype = (
                        "technology"
                        if tech.lower() in _TECH_HINTS
                        else "skill"
                    )
                    kg.add_entity(ttype, tech)
                    kg.add_relation(name, "uses", tech)
                    learned.append(f"'{name}' uses '{tech}'")
        # Standalone "X uses A, B" (e.g. "MEW uses Ollama and RAG")
        for u in _X_USES_Y.finditer(statement):
            subj = u.group(1).strip()
            if any(subj.lower() in L.lower() for L in learned):
                continue  # already handled above
            subj_ent = kg.add_entity("project", subj)
            for tech in _split_list(u.group(2)):
                if not tech:
                    continue
                ttype = "technology" if tech.lower() in _TECH_HINTS else "skill"
                kg.add_entity(ttype, tech)
                kg.add_relation(subj, "uses", tech)
                learned.append(f"'{subj_ent['name']}' uses '{tech}'")
        if not learned:
            raise ToolError(
                "I couldn't pull a clear fact out of that. Try phrasing like "
                "'My main project is MEW' or 'MEW uses Ollama and RAG'."
            )
        return learned

    # -- query ----------------------------------------------------------
    def _query(self, query: str) -> str:
        q = query.strip()
        if not q:
            raise ToolError("What should I look up in the knowledge graph?")
        # "my projects" -> list all project entities
        m = re.search(r"\bmy\s+(\w+)", q, re.IGNORECASE)
        if m:
            etype = m.group(1).lower().rstrip("s")
            if etype in _TYPE_MAP.values() or etype + "s" in _TYPE_MAP:
                etype = {"projects": "project"}.get(etype, etype)
                ents = kg.find_entities(entity_type=etype)
                if not ents:
                    return f"You haven't told me about any {etype}s yet."
                lines = [f"Your {etype}s:"]
                for e in ents:
                    rels = kg.relations_for(e["name"])
                    suffix = (
                        f" ({'; '.join(r['relation'] + ' ' + r['object'] for r in rels)})"
                        if rels else ""
                    )
                    lines.append(f"- {e['name']}{suffix}")
                return "\n".join(lines)
        # Named entity lookup
        desc = kg.describe(q)
        if not desc:
            return f"I don't have anything stored about '{q}' yet."
        return self._describe(q)

    def _describe(self, name: str) -> str:
        desc = kg.describe(name.strip())
        if not desc:
            return f"I don't have anything stored about '{name}' yet."
        ent = desc["entity"]
        lines = [f"{ent['name']} ({ent['type']})"]
        for r in desc["relations"]:
            lines.append(f"- {r['relation']}: {r['object']}")
        return "\n".join(lines)
