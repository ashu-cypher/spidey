"""Goals tool — personal goals with milestones (MEW 2.0).

Actions:
  create       start a goal ("I want to become better at AI development")
               + derive 3-4 sensible milestones grounded in the goal text.
  list         all tracked goals.
  progress     mark a milestone done (by goal + milestone text/index),
               or set a goal's status (active/paused/completed).
  report       "how am I progressing" — goals with completed/total
               milestones plus linked learning topics.
  suggest_next "what should I learn next" — first incomplete milestone
               across goals, or an honest suggestion to create a goal.

Goals are stored via MemoryTool under the "[goal]" namespace (same
pattern as learning.py's "[learning-topic]"), so they persist, are
searchable, and are forgettable. Progress always comes from stored
data — never invented.
"""
from __future__ import annotations

import re

from app.tools.base import BaseTool, ToolError
from app.tools.memory_tool import MemoryTool

_mem = MemoryTool()

# Trigger phrases the agent may leave in the goal text — stripped so the
# stored goal reads cleanly.
_GOAL_TRIGGERS = re.compile(
    r"^(?:please\s+)?(?:i\s+want\s+to\s+become\s+better\s+at|my\s+goal\s+is\s+to|"
    r"my\s+goal\s+is|add\s+a\s+goal\s+(?:to\s+|of\s+)?|i\s+want\s+to)\s+",
    re.IGNORECASE,
)

_STATUS_RE = re.compile(r"\bstatus=(\w+)")


def _goal_key(goal: str) -> str:
    return "goal:" + re.sub(r"\s+", " ", goal.strip().lower())


def _split_parts(goal_text: str) -> list[str]:
    """Derive milestones from the goal text itself.

    If the goal lists several things ("X and Y", "X, Y, Z"), each becomes
    its own milestone (grounded). Otherwise generic-but-grounded steps
    built from the goal text: fundamentals -> practice -> build.
    Never invents unrelated milestones.
    """
    core = goal_text.strip().rstrip(".?!")
    # Split on " and " / commas for compound goals.
    parts = [p.strip() for p in re.split(r"\s+and\s+|,\s*", core) if len(p.strip()) > 2]
    if len(parts) >= 2 and all(len(p) < 60 for p in parts):
        return [p[:80] for p in parts[:4]]
    short = core[:60]
    return [
        f"Learn the fundamentals of {short}",
        f"Practice {short} with small exercises",
        f"Build a real project applying {short}",
        f"Review and consolidate {short}",
    ][:4]


def _parse(content: str) -> dict | None:
    mm = re.match(
        r"\[goal\]\s+(.*?)\s+::\s+(.*?)\s+\|\s+status=(\w+)"
        r"\s+\|\s+milestones=(.*?)\s+\|\s+done=([\d,\s]*)$",
        content,
    )
    if not mm:
        return None
    milestones = [m.strip() for m in mm.group(4).split(" || ") if m.strip()]
    done_raw = mm.group(5).strip()
    done = [int(i) for i in done_raw.split(",") if i.strip().isdigit()]
    return {
        "key": mm.group(1),
        "goal": mm.group(2),
        "status": mm.group(3),
        "milestones": milestones,
        "done": done,
        "raw": content,
    }


class GoalsTool(BaseTool):
    name = "goals"
    description = (
        "Personal goals: create goals with milestones, list them, mark "
        "progress, and suggest what to learn/do next."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "progress", "report", "suggest_next"],
            },
            "goal": {"type": "string", "description": "goal text"},
            "milestones": {
                "type": "array",
                "items": {"type": "string"},
                "description": "optional explicit milestones",
            },
            "milestone": {
                "type": "string",
                "description": "milestone text or 1-based index for progress",
            },
            "done": {"type": "boolean", "description": "mark milestone done"},
            "status": {
                "type": "string",
                "enum": ["active", "paused", "completed"],
            },
        },
        "required": ["action"],
    }
    output_schema = {"result": "object"}
    permission = "read"
    action_permissions = {
        "create": "low_write",
        "progress": "low_write",
    }

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "").strip().lower()
        if action == "create":
            return {"goal": await self._create(kwargs)}
        if action == "list":
            return {"goals": await self._all()}
        if action == "progress":
            return {"updated": await self._progress(kwargs)}
        if action == "report":
            return {"progress": await self._report()}
        if action == "suggest_next":
            return {"suggestion": await self._suggest_next()}
        raise ToolError(f"Unknown goals action: {action!r}.")

    # -- create -----------------------------------------------------------
    async def _create(self, kwargs: dict) -> dict:
        goal_text = (kwargs.get("goal") or "").strip()
        goal_text = _GOAL_TRIGGERS.sub("", goal_text).strip().rstrip(".?!")
        if not goal_text:
            raise ToolError("What goal do you want to set?")
        milestones = [
            m.strip() for m in (kwargs.get("milestones") or []) if m and m.strip()
        ] or _split_parts(goal_text)
        entry = {
            "goal": goal_text,
            "status": "active",
            "milestones": milestones,
            "done": [],
        }
        await _mem.execute(
            action="save",
            content=self._serialize(_goal_key(goal_text), entry),
        )
        return entry

    @staticmethod
    def _serialize(key: str, entry: dict) -> str:
        milestones = " || ".join(entry["milestones"])
        done = ",".join(str(i) for i in sorted(entry["done"]))
        return (
            f"[goal] {key} :: {entry['goal']} | status={entry['status']} "
            f"| milestones={milestones} | done={done}"
        )

    # -- list -------------------------------------------------------------
    async def _all(self) -> list[dict]:
        res = await _mem.execute(action="recall", query="goal", limit=30)
        goals = []
        for m in res.get("results", []):
            content = m.get("content", "")
            if content.startswith("[goal]"):
                parsed = _parse(content)
                if parsed:
                    goals.append(parsed)
        # Newest save wins on duplicate keys (memory is append-only).
        seen: dict[str, dict] = {}
        for g in goals:
            seen[g["key"]] = g
        return list(seen.values())

    # -- progress ---------------------------------------------------------
    async def _find_goal(self, goal_text: str) -> dict:
        goals = await self._all()
        if not goals:
            raise ToolError("You don't have any goals tracked yet.")
        needle = goal_text.strip().lower()
        if needle:
            for g in goals:
                if needle in g["goal"].lower() or g["goal"].lower() in needle:
                    return g
        if len(goals) == 1:
            return goals[0]
        names = ", ".join(f"\"{g['goal']}\"" for g in goals)
        raise ToolError(f"Which goal? I have: {names}.")

    async def _progress(self, kwargs: dict) -> dict:
        goal = await self._find_goal(kwargs.get("goal") or "")
        milestone_arg = (kwargs.get("milestone") or "").strip()
        status = (kwargs.get("status") or "").strip().lower()

        if status:
            if status not in ("active", "paused", "completed"):
                raise ToolError("Status must be active, paused, or completed.")
            goal["status"] = status
        elif milestone_arg:
            idx: int | None = None
            if milestone_arg.isdigit():
                i = int(milestone_arg) - 1
                if 0 <= i < len(goal["milestones"]):
                    idx = i
            else:
                low = milestone_arg.lower()
                for i, m in enumerate(goal["milestones"]):
                    if low in m.lower() or m.lower() in low:
                        idx = i
                        break
            if idx is None:
                raise ToolError(
                    f"I couldn't find that milestone in \"{goal['goal']}\"."
                )
            done = goal["done"]
            if kwargs.get("done", True) and idx not in done:
                done.append(idx)
            elif not kwargs.get("done", True) and idx in done:
                done.remove(idx)
            goal["done"] = sorted(done)
        else:
            raise ToolError("Tell me which milestone you finished, or set a status.")

        await _mem.execute(action="save", content=self._serialize(goal["key"], goal))
        completed = len(goal["done"])
        total = len(goal["milestones"])
        return {
            "goal": goal["goal"],
            "status": goal["status"],
            "completed": completed,
            "total": total,
        }

    # -- report -----------------------------------------------------------
    async def _report(self) -> dict:
        goals = await self._all()
        if not goals:
            return {
                "goals": [],
                "message": (
                    "You don't have any goals tracked yet — tell me a goal "
                    "and I'll set it up with milestones."
                ),
            }
        # Linked learning topics: share significant words with the goal.
        from app.tools.learning import LearningTool

        topics: list[dict] = []
        try:
            out = await LearningTool().execute(action="topics")
            topics = [t for t in out.get("topics", []) or [] if isinstance(t, dict)]
        except Exception:
            topics = []

        report = []
        for g in goals:
            done_set = set(g["done"])
            linked = [
                t.get("topic", "")
                for t in topics
                if self._overlaps(t.get("topic", ""), g["goal"])
            ]
            report.append({
                "goal": g["goal"],
                "status": g["status"],
                "completed": len(done_set),
                "total": len(g["milestones"]),
                "next_milestone": next(
                    (m for i, m in enumerate(g["milestones"]) if i not in done_set),
                    None,
                ),
                "learning_topics": linked,
            })
        return {"goals": report}

    @staticmethod
    def _overlaps(a: str, b: str) -> bool:
        wa = {w for w in re.findall(r"[a-z]{4,}", a.lower())}
        wb = {w for w in re.findall(r"[a-z]{4,}", b.lower())}
        return bool(wa & wb)

    # -- suggest_next -----------------------------------------------------
    async def _suggest_next(self) -> dict:
        goals = await self._all()
        active = [g for g in goals if g["status"] == "active"]
        for g in active:
            done_set = set(g["done"])
            for i, m in enumerate(g["milestones"]):
                if i not in done_set:
                    return {
                        "goal": g["goal"],
                        "next": m,
                        "milestone_number": i + 1,
                        "of_total": len(g["milestones"]),
                    }
        if goals:
            return {
                "goal": None,
                "next": None,
                "message": "All your tracked goals are complete or paused. Set a new goal to keep moving.",
            }
        return {
            "goal": None,
            "next": None,
            "message": (
                "You don't have any goals yet — tell me something like "
                "\"I want to become better at AI development\" and I'll "
                "set it up with milestones."
            ),
        }
