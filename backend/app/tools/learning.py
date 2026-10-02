"""Personal Learning Engine (spec 15).

Actions:
  note      record a learning topic ("I'm learning RAG") with optional notes
  topics    list tracked topics and progress
  progress  mark a topic's status (started/practicing/mastered)
  explain   explain a topic using memory + knowledge context (template-based;
            the agent's LLM path enriches this when a model is available)
  quiz      generate practice questions on a topic from stored notes and any
            supplied context (deterministic templates in rule_based mode —
            honest about being generated, never presented as an exam)

Learning state is stored via the existing memory store under the
"learning:" namespace so it benefits from persistence, search and forget.
"""
from __future__ import annotations

import re

from app.tools.base import BaseTool, ToolError
from app.tools.memory_tool import MemoryTool

_mem = MemoryTool()


def _topic_key(topic: str) -> str:
    return "learning:" + re.sub(r"\s+", " ", topic.strip().lower())


class LearningTool(BaseTool):
    name = "learning"
    description = (
        "Personal learning engine: track topics, explain concepts, generate "
        "practice quizzes, and record progress."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["note", "topics", "progress", "explain", "quiz"],
            },
            "topic": {"type": "string"},
            "notes": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["started", "practicing", "mastered"],
            },
            "context": {"type": "string", "description": "source text for quiz/explain"},
            "num_questions": {"type": "integer"},
        },
        "required": ["action"],
    }
    output_schema = {"result": "object"}
    permission = "read"
    action_permissions = {"note": "low_write", "progress": "low_write"}

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "").strip().lower()
        if action == "note":
            return {"saved": await self._note(kwargs)}
        if action == "topics":
            return {"topics": await self._topics()}
        if action == "progress":
            return {"updated": await self._progress(kwargs)}
        if action == "explain":
            return {"explanation": await self._explain(kwargs)}
        if action == "quiz":
            return {"quiz": await self._quiz(kwargs)}
        raise ToolError(f"Unknown learning action: {action!r}.")

    async def _note(self, kwargs: dict) -> dict:
        topic = (kwargs.get("topic") or "").strip()
        if not topic:
            raise ToolError("What topic are you learning?")
        notes = (kwargs.get("notes") or "").strip()
        entry = {
            "topic": topic,
            "status": "started",
            "notes": notes,
            "sessions": 1,
        }
        key = _topic_key(topic)
        await _mem.execute(
            action="save",
            content=f"[learning-topic] {key} :: {topic} | status=started"
            + (f" | notes={notes}" if notes else ""),
        )
        return entry

    async def _topics(self) -> list[dict]:
        res = await _mem.execute(action="recall", query="learning-topic", limit=30)
        topics = []
        for m in res.get("results", []):
            content = m.get("content", "")
            if not content.startswith("[learning-topic]"):
                continue
            mm = re.match(
                r"\[learning-topic\]\s+(\S+)\s+::\s+(.*?)\s+\|\s+status=(\w+)"
                r"(?:\s+\|\s+notes=(.*))?$",
                content,
            )
            if mm:
                topics.append({
                    "topic": mm.group(2), "status": mm.group(3),
                    "notes": mm.group(4) or "",
                })
        return topics

    async def _progress(self, kwargs: dict) -> dict:
        topic = (kwargs.get("topic") or "").strip()
        status = (kwargs.get("status") or "").strip().lower()
        if not topic:
            raise ToolError("Which topic?")
        if status not in ("started", "practicing", "mastered"):
            raise ToolError("Status must be started, practicing, or mastered.")
        await _mem.execute(
            action="save",
            content=f"[learning-topic] {_topic_key(topic)} :: {topic} | status={status}",
        )
        return {"topic": topic, "status": status}

    async def _explain(self, kwargs: dict) -> dict:
        topic = (kwargs.get("topic") or "").strip()
        if not topic:
            raise ToolError("What should I explain?")
        context = (kwargs.get("context") or "").strip()
        topics = await self._topics()
        known = next(
            (t for t in topics if t["topic"].lower() == topic.lower()), None
        )
        parts = [f"**{topic}**"]
        if known and known.get("notes"):
            parts.append(f"\nYour notes: {known['notes']}")
        if context:
            # Extract the most relevant sentences mentioning the topic.
            sentences = re.split(r"(?<=[.!?])\s+", context)
            key = topic.lower().split()[0]
            relevant = [s for s in sentences if key in s.lower()][:4]
            if relevant:
                parts.append("\nKey points from your material:")
                parts.extend(f"- {s.strip()}" for s in relevant)
        parts.append(
            "\n_This is a starting explanation from your saved context. "
            "Ask follow-ups like 'quiz me' or 'go deeper on X' to keep learning._"
        )
        return {"topic": topic, "text": "\n".join(parts)}

    async def _quiz(self, kwargs: dict) -> dict:
        topic = (kwargs.get("topic") or "").strip()
        if not topic:
            raise ToolError("What should I quiz you on?")
        n = kwargs.get("num_questions") or 5
        n = max(1, min(int(n), 10))
        context = (kwargs.get("context") or "").strip()
        topics = await self._topics()
        known = next(
            (t for t in topics if t["topic"].lower() == topic.lower()), None
        )
        source = context or (known.get("notes") if known else "")
        questions: list[dict] = []
        if source:
            sentences = [
                s.strip() for s in re.split(r"(?<=[.!?])\s+", source) if len(s.strip()) > 40
            ]
            for i, s in enumerate(sentences[:n]):
                # Turn a statement into a question honestly: ask the user to
                # explain the concept in the sentence.
                concept = s[:60].rstrip(" ,;:")
                questions.append({
                    "q": f"In your own words, explain: \"{concept}...\"",
                    "hint": s[:120],
                })
        # Fill remaining with generic-but-honest prompts about the topic.
        generics = [
            f"What is {topic}, in one or two sentences?",
            f"Give a real example of {topic} in practice.",
            f"What is a common misconception about {topic}?",
            f"How would you explain {topic} to a beginner?",
            f"What would you still like to learn about {topic}?",
        ]
        for g in generics:
            if len(questions) >= n:
                break
            questions.append({"q": g, "hint": ""})
        return {
            "topic": topic,
            "disclaimer": (
                "Practice questions generated from your notes — "
                "not an exam, just a way to check understanding."
            ),
            "questions": questions[:n],
        }
