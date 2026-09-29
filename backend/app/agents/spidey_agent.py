import re
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from app.agents.memory_manager import MemoryManager
from app.agents.planner import build_plan
from app.providers.base import AIProvider
from app.tools.base import BaseTool, ToolError

_FALLBACK_REPLY = "Spidey couldn't complete that step."

NO_CV_REPLY = "I don't have your CV yet — upload it in the Resume tab."

_VERIFY_KEYS: dict[str, tuple[str, ...]] = {
    "calculator": ("result",),
    "memory": ("results", "saved"),
    "tasks": ("task", "tasks"),
    "rag": ("results", "documents"),
    "resume": ("analysis", "suggestions", "job_match", "versions", "version"),
}

# Cosine-similarity score below this means the retrieval is too weak to trust.
# Chosen for the 384-dim providers used in Phase 3 (documented in README):
# related query/chunk pairs score well above it, unrelated pairs below.
RAG_CONFIDENCE_THRESHOLD = 0.15

LOW_CONFIDENCE_REPLY = (
    "I couldn't find enough information in your documents to answer that reliably."
)


class SpideyAgent:
    MAX_AGENT_STEPS = 9

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

    def _stepper(self, run, engine):
        """Build the visible-step recorder shared by run() and _run_resume_steps.

        Every step is recorded on the workflow engine (WAITING -> RUNNING ->
        COMPLETED/FAILED) so the WorkflowPanel shows the full pipeline live.
        """
        step_count = 0

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

        return _step

    async def run(self, message: str, run, engine) -> str:
        _step = self._stepper(run, engine)
        tool_results: dict[str, dict] = {}
        classification: dict | None = None
        facts = ""
        mems: list[dict] = []

        try:
            classification = await _step(
                "Understand request",
                "understand",
                lambda: self.provider.aclassify_intent(message),
            )
            intent = classification.get("intent", "chat_fallback")
            if intent in ("resume_analyze", "resume_improve"):
                # Dedicated 9-stage resume pipeline ("Understand request" is
                # already recorded above as stage 1).
                return await self._run_resume_steps(
                    _step, message, intent, run, engine
                )
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

    async def _run_resume_steps(self, _step, message: str, intent: str, run, engine) -> str:
        """Phase 4 resume pipeline — nine visible stages.

        Understand request -> Read resume -> Analyze sections ->
        Identify weaknesses -> Retrieve memory -> Generate suggestions ->
        Verify results -> Compose response -> Save memory.

        When no CV is stored, the pipeline short-circuits after "Read resume"
        with a clean user message instead of failing.
        """
        try:
            latest = await _step(
                "Read resume",
                "tool",
                lambda: self.tools["resume"].execute(action="latest"),
                input_data={"action": "latest"},
            )
            version = (latest or {}).get("version")
            if not version:
                facts = self._compose_facts("resume_empty", message, {}, [])
                response = await _step(
                    "Respond",
                    "respond",
                    lambda: self.provider.agenerate(message, context=facts),
                    input_data={"facts": facts[:500]},
                )
                engine.finish_run(run.workflow_id, "completed", result=response)
                return response

            version_id = version["id"]
            analysis_out = await _step(
                "Analyze sections",
                "tool",
                lambda: self.tools["resume"].execute(
                    action="analyze", version_id=version_id
                ),
                input_data={"action": "analyze", "version_id": version_id},
            )
            analysis = analysis_out.get("analysis", {})
            weaknesses = await _step(
                "Identify weaknesses",
                "analysis",
                lambda: self._rank_weaknesses(analysis),
                input_data={"issues": len(analysis.get("issues", []))},
            )
            mems = await _step(
                "Retrieve memory",
                "memory",
                lambda: self.memory.retrieve_relevant(message),
            )
            improve_out = await _step(
                "Generate suggestions",
                "tool",
                lambda: self.tools["resume"].execute(
                    action="improve", version_id=version_id
                ),
                input_data={"action": "improve", "version_id": version_id},
            )
            await _step(
                "Verify results",
                "verify",
                lambda: self._verify(
                    {
                        "resume_analyze": analysis_out,
                        "resume_improve": improve_out,
                    }
                ),
            )
            tool_results = {
                "resume": {
                    "analysis": analysis,
                    "weaknesses": weaknesses.get("weaknesses", []),
                    "improve": improve_out,
                    "version": version,
                }
            }
            facts = self._compose_facts(intent, message, tool_results, mems)
            response = await _step(
                "Compose response",
                "respond",
                lambda: self.provider.agenerate(message, context=facts),
                input_data={"facts": facts[:500]},
            )
            await _step(
                "Save memory",
                "memory_update",
                lambda: self._save_resume_memory(version, analysis, improve_out),
            )
            engine.finish_run(run.workflow_id, "completed", result=response)
            return response
        except Exception:
            engine.finish_run(run.workflow_id, "failed", result=_FALLBACK_REPLY)
            return _FALLBACK_REPLY

    @staticmethod
    async def _rank_weaknesses(analysis: dict) -> dict:
        """Order analysis issues by severity for the 'Identify weaknesses' step."""
        severity = {
            "repetition": 0,
            "missing_measurable": 1,
            "weak_verb": 2,
            "vague_statement": 3,
        }
        issues = sorted(
            analysis.get("issues", []),
            key=lambda i: severity.get(i.get("type"), 9),
        )
        return {
            "weaknesses": [
                {
                    "type": i.get("type"),
                    "detail": i.get("detail"),
                    "excerpt": i.get("excerpt"),
                }
                for i in issues[:8]
            ],
            "count": len(issues),
        }

    async def _save_resume_memory(
        self, version: dict, analysis: dict, improve_out: dict
    ) -> dict:
        """Persist a short summary of the resume analysis for future chats."""
        try:
            gaps = ", ".join(analysis.get("missing_info", [])[:3]) or "none noted"
            content = (
                f"Resume analyzed (v{version.get('version_number')}): quality "
                f"{analysis.get('quality_score')}/100, ATS "
                f"{analysis.get('ats', {}).get('score')}/100; "
                f"{len(improve_out.get('suggestions', []))} rewrite suggestions "
                f"saved as v{improve_out.get('new_version_number')}. "
                f"Gaps: {gaps}."
            )
            await self.tools["memory"].execute(
                action="save",
                content=content,
                category="career",
                importance=0.7,
            )
            return {"saved": True}
        except Exception:
            # Memory must never fail the resume pipeline.
            return {"saved": False, "reason": "memory store unavailable"}

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
        if tool == "resume":
            # The dedicated resume pipeline builds its own args per stage;
            # this default keeps the generic loop safe.
            return {"action": "latest"}
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
        if intent in ("resume_analyze", "resume_improve"):
            return SpideyAgent._compose_resume_facts(intent, tool_results)
        if intent == "resume_empty":
            return NO_CV_REPLY
        return ""

    @staticmethod
    def _compose_resume_facts(intent: str, tool_results: dict[str, dict]) -> str:
        """Facts for resume intents — analysis summary, never invented content."""
        r = tool_results.get("resume", {})
        analysis = r.get("analysis", {})
        improve_out = r.get("improve", {})
        version = r.get("version", {})
        content = analysis.get("content", {})
        ats = analysis.get("ats", {})
        lines = [
            f"Resume analysis — v{version.get('version_number')} "
            f"\"{version.get('label')}\":"
        ]
        lines.append(
            f"Quality score: {analysis.get('quality_score')}/100 | "
            f"ATS score: {ats.get('score')}/100"
        )
        found = content.get("sections_found", [])
        lines.append("Sections found: " + (", ".join(found) if found else "none"))
        missing = analysis.get("missing_info", [])
        if missing:
            lines.append("Gaps: " + "; ".join(missing))
        weaknesses = r.get("weaknesses", [])[:5]
        if weaknesses:
            lines.append("Top weaknesses:")
            for i, w in enumerate(weaknesses, 1):
                lines.append(f"  {i}. [{w.get('type')}] {w.get('detail')}")
        suggestions = improve_out.get("suggestions", [])
        new_v = improve_out.get("new_version_number")
        if intent == "resume_improve" and suggestions:
            lines.append(f"Rewrite suggestions (saved as v{new_v}):")
            for s in suggestions[:6]:
                lines.append(
                    f"  - \"{s.get('original')}\" → \"{s.get('improved')}\" "
                    f"({s.get('reason')})"
                )
            note = improve_out.get("note")
            if note:
                lines.append(note)
        elif suggestions:
            lines.append(
                f"{len(suggestions)} rewrite suggestions saved as v{new_v} — "
                "see the Resume tab for the full list."
            )
        return "\n".join(lines)

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
