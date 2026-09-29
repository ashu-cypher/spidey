import re
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from app.agents.memory_manager import MemoryManager
from app.agents.planner import build_plan
from app.providers.base import AIProvider
from app.tools.base import BaseTool, ToolError

_FALLBACK_REPLY = "Spidey couldn't complete that step."

_VERIFY_KEYS: dict[str, tuple[str, ...]] = {
    "calculator": ("result",),
    "memory": ("results", "saved"),
    "tasks": ("task", "tasks"),
    "rag": ("results", "documents"),
}

# Cosine-similarity score below this means the retrieval is too weak to trust.
# Chosen for the 384-dim providers used in Phase 3 (documented in README):
# related query/chunk pairs score well above it, unrelated pairs below.
RAG_CONFIDENCE_THRESHOLD = 0.15

LOW_CONFIDENCE_REPLY = (
    "I couldn't find enough information in your documents to answer that reliably."
)


class SpideyAgent:
    MAX_AGENT_STEPS = 8

    def __init__(self, provider: AIProvider, tools: dict[str, BaseTool]) -> None:
        self.provider = provider
        self.tools = tools
        self.memory = MemoryManager(tools["memory"])

    def _safe(self, out: Any) -> dict:
        if isinstance(out, dict):
            safe: dict = {}
            for key, value in out.items():
                safe[key] = value[:500] if isinstance(value, str) and len(value) > 500 else value
            return safe
        return {"value": str(out)[:500]}

    @staticmethod
    def _user_msg(exc: Exception) -> str:
        if isinstance(exc, ToolError):
            return exc.user_message
        return _FALLBACK_REPLY

    async def run(self, message: str, run, engine) -> str:
        step_count = 0
        tool_results: dict[str, dict] = {}
        classification: dict | None = None
        facts = ""
        mems: list[dict] = []

        async def _step(
            name: str,
            step_type: str,
            fn: Callable[[], Awaitable[Any]],
            input_data: dict | None = None,
        ) -> Any:
            nonlocal step_count
            step_count += 1
            if step_count > self.MAX_AGENT_STEPS:
                raise ToolError("Agent step limit exceeded.")
            step = engine.add_step(run.workflow_id, name, step_type, input_data)
            engine.update_step(run.workflow_id, step.step_id, status="RUNNING")
            try:
                out = await fn()
            except Exception as exc:
                engine.update_step(
                    run.workflow_id, step.step_id, status="FAILED",
                    error=self._user_msg(exc),
                )
                raise
            engine.update_step(
                run.workflow_id, step.step_id, status="COMPLETED", output=self._safe(out)
            )
            return out

        try:
            classification = await _step(
                "Understand request",
                "understand",
                lambda: self.provider.aclassify_intent(message),
            )
            intent = classification.get("intent", "chat_fallback")
            plan = build_plan(classification)

            if classification.get("requires_memory"):
                mems = await _step(
                    "Retrieve memory",
                    "memory",
                    lambda: self.memory.retrieve_relevant(message),
                )

            for plan_step in plan:
                if plan_step.get("type") != "tool":
                    continue
                tool_name = plan_step["tool"]
                args = self._tool_args(tool_name, intent, message)
                tool_results[tool_name] = await _step(
                    plan_step["name"],
                    "tool",
                    lambda _t=tool_name, _a=args: self.tools[_t].execute(**_a),
                    input_data=args,
                )

            if tool_results:
                await _step(
                    "Verify results",
                    "verify",
                    lambda: self._verify(tool_results),
                )

            facts = self._compose_facts(intent, message, tool_results, mems)
            response = await _step(
                "Compose response",
                "respond",
                lambda: self.provider.agenerate(message, context=facts),
                input_data={"facts": facts[:500]},
            )

            await _step(
                "Update memory",
                "memory_update",
                lambda: self._update_memory(intent, message),
            )

            engine.finish_run(run.workflow_id, "completed", result=response)
            return response
        except Exception:
            engine.finish_run(run.workflow_id, "failed", result=_FALLBACK_REPLY)
            return _FALLBACK_REPLY

    async def _verify(self, tool_results: dict[str, dict]) -> dict:
        for tool_name, result in tool_results.items():
            if not isinstance(result, dict) or not result:
                raise ToolError("Verification failed.")
            expected = _VERIFY_KEYS.get(tool_name, ())
            if expected and not any(k in result for k in expected):
                raise ToolError("Verification failed.")
        return {"verified": True}

    async def _update_memory(self, intent: str, message: str) -> dict:
        if intent == "remember":
            return {"saved": True, "reason": "already saved by memory tool"}
        return {"saved": False, "reason": "nothing salient"}

    def _tool_args(self, tool: str, intent: str, message: str) -> dict:
        if tool == "calculator":
            return {"text": message}
        if tool == "memory":
            if intent == "remember":
                return {
                    "action": "save",
                    "content": self._extract_remember(message),
                }
            return {"action": "recall", "query": message, "limit": 5}
        if tool == "tasks":
            if intent == "task_create":
                return {
                    "action": "create",
                    "title": self._extract_task(message),
                    "due": self._extract_due(message),
                }
            return {"action": "list"}
        if tool == "rag":
            # summarize_document benefits from more context than a plain search
            limit = 10 if intent == "summarize_document" else 5
            return {"action": "search", "query": message, "limit": limit}
        return {}

    @staticmethod
    def _extract_remember(message: str) -> str:
        m = re.search(r"remember\s+(?:that\s+)?(.+)", message, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")
        return message.strip()

    @staticmethod
    def _extract_task(message: str) -> str:
        m = re.search(r"remind me to\s+(.+)", message, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")
        m = re.search(
            r"(?:create|add)\s+(?:a\s+)?task\s*(?:for\s+tomorrow\s*)?"
            r"(?::\s*|\s+to\s+|\s+)?(.+)",
            message,
            re.IGNORECASE,
        )
        if m and m.group(1).strip():
            return m.group(1).strip().rstrip(".")
        return "Untitled task"

    @staticmethod
    def _extract_due(message: str) -> str | None:
        if "tomorrow" in message.lower():
            return (date.today() + timedelta(days=1)).isoformat()
        return None

    @staticmethod
    def _compose_facts(
        intent: str,
        message: str,
        tool_results: dict[str, dict],
        mems: list[dict],
    ) -> str:
        if intent == "calculate":
            calc = tool_results.get("calculator", {})
            if "result" in calc:
                return f"{calc.get('expression', '')} = {calc['result']}"
            return ""
        if intent == "remember":
            saved = tool_results.get("memory", {}).get("saved", {})
            content = saved.get("content", message)
            return f"Remembered: {content}"
        if intent == "recall_memory":
            results = tool_results.get("memory", {}).get("results", [])
            if results:
                lines = "\n".join(f"- {m['content']}" for m in results)
                return f"Here's what I remember:\n{lines}"
            return "I don't have anything stored about that yet."
        if intent == "task_create":
            task = tool_results.get("tasks", {}).get("task", {})
            title = task.get("title", "")
            due = task.get("due")
            return f"Task created: {title}" + (f" (due {due})" if due else "")
        if intent == "task_list":
            tasks = tool_results.get("tasks", {}).get("tasks", [])
            if tasks:
                lines = "\n".join(
                    f"- [{'x' if t.get('done') else ' '}] {t.get('title')}"
                    for t in tasks
                )
                return f"Your tasks:\n{lines}"
            return "No tasks yet — say 'create a task' to add one."
        if intent in ("knowledge_search", "summarize_document"):
            return SpideyAgent._compose_rag_facts(tool_results)
        return ""

    @staticmethod
    def _compose_rag_facts(tool_results: dict[str, dict]) -> str:
        """Facts for RAG intents, with citation + confidence gating.

        Memory (the memory tool) and knowledge (the rag tool) stay distinct:
        document passages are cited per chunk, and low-confidence retrieval
        yields exactly ``LOW_CONFIDENCE_REPLY`` — nothing before or after it.
        """
        results = tool_results.get("rag", {}).get("results", []) or []
        best = max((float(r.get("score") or 0.0) for r in results), default=0.0)
        if not results or best < RAG_CONFIDENCE_THRESHOLD:
            return LOW_CONFIDENCE_REPLY
        lines = []
        for r in results:
            source = r.get("source") or "document"
            chunk_index = r.get("chunk_index", "?")
            content = (r.get("content") or "")[:600]
            lines.append(f"[{source}, chunk {chunk_index}]\n{content}")
        return (
            "Based on your uploaded documents:\n\n"
            + "\n\n".join(lines)
            + "\n\nAnswer using only these passages, citing each claim like "
            "[filename, chunk N]."
        )
