import json
import logging
import re
import secrets
import time
from inspect import Parameter, signature
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from app.agents.memory_manager import MemoryManager
from app.agents.planner import build_plan
from app.config import settings
from app.database import get_session
from app.models import ToolCall, WorkflowRun as WorkflowRunRow
from app.providers.base import AIProvider
from app.tools.base import BaseTool, ToolError
from app.tools.reminders import extract_reminder_title, parse_reminder_at
from app.tools.tasks import parse_due

logger = logging.getLogger("spidey")

_FALLBACK_REPLY = "Spidey couldn't complete that step."

NO_CV_REPLY = "I don't have your CV yet — upload it in the Resume tab."

_VERIFY_KEYS: dict[str, tuple[str, ...]] = {
    "calculator": ("result",),
    "memory": ("results", "saved"),
    "tasks": ("task", "tasks", "deleted"),
    "reminders": ("reminder", "reminders", "deleted"),
    "rag": ("results", "documents"),
    "resume": ("analysis", "suggestions", "job_match", "versions", "version"),
    "search": ("results",),
    "documents": ("document", "documents", "deleted"),
    "code": ("explanation",),
    "system": ("cpu_percent", "action", "needs_confirmation"),
}

# Pending confirmations live in memory: token -> pending action. Single-use,
# 10-minute expiry (spec 21).
CONFIRM_TTL_SECONDS = 600

# Sensitive input keys are dropped (never stored) by the Phase 7
# tool-call sanitizer (spec 29). Compared case-insensitively.
_SENSITIVE_KEYS = {"password", "api_key", "apikey", "token", "secret"}

# A ToolError counts as transient (worth exactly one retry) only when its
# user-safe message indicates a timeout. Validation and user errors are
# never retried.
_TRANSIENT_MARKER = "timed out"

# Per-step string cap used by the tool-call sanitizer / summarizer.
_SANITIZE_LIMIT = 500


def _sanitize(value: Any) -> Any:
    """Spec 29 sanitizer: drop sensitive keys, truncate long strings."""
    if isinstance(value, dict):
        return {
            key: _sanitize(val)
            for key, val in value.items()
            if str(key).lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, str) and len(value) > _SANITIZE_LIMIT:
        return value[:_SANITIZE_LIMIT]
    return value


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
        # token -> {"tool", "args", "message", "classification", "plan_step",
        #           "proposal", "expires_at"}
        self._pending: dict[str, dict] = {}
        # Tool that failed the most recent _verify() call (Phase 7 re-plan).
        self._last_verify_failure: str | None = None

    def _action_permission(
        self, tool_name: str, action: str | None, args: dict | None = None
    ) -> str:
        """Effective permission for a planned tool action (spec 21).

        Delegates to the tool's ``permission_for`` so tools with args-dependent
        gating (the system controller's command allowlist) are honoured.
        """
        tool = self.tools.get(tool_name)
        if tool is None:
            return "read"
        return tool.permission_for(action, args)

    def pop_pending(self, token: str) -> dict:
        """Consume a pending confirmation; single-use, 10-minute expiry."""
        pending = self._pending.pop(token, None)
        if pending is None or pending.get("expires_at", 0) < time.time():
            raise ToolError(
                "That confirmation has expired or is invalid. Please ask again."
            )
        return pending

    @staticmethod
    def _proposal(tool_name: str, args: dict) -> str:
        action = args.get("action")
        title = args.get("title") or args.get("text") or args.get("id") or "this item"
        if tool_name == "tasks" and action == "delete":
            return f'Delete the task "{title}"?'
        if tool_name == "reminders" and action == "delete":
            return f'Delete the reminder "{title}"?'
        if tool_name == "documents" and action == "delete":
            return f'Delete the document "{title}"?'
        if tool_name == "memory" and action == "delete":
            return f'Delete this memory: "{str(title)[:80]}"?'
        if tool_name == "system":
            # The tool's own proposal wording is mirrored here so the chat
            # confirm_token flow shows the exact pending action.
            if action == "run":
                return f"Run shell command: {args.get('command', '')}"
            if action == "open_website":
                return f"Open website: {args.get('url', '')}"
            return f"Run system ({action})?"
        return f"Run {tool_name} ({action})?"

    def _safe(self, out: Any) -> dict:
        # SPIDEY_DEBUG=true: full input/output in API responses (Phase 7).
        if settings.spidey_debug:
            if isinstance(out, dict):
                return dict(out)
            return {"value": str(out)}
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

    def _record_tool_call(
        self,
        run,
        tool_name: str,
        input_data: Any,
        output: Any,
        duration_ms: int,
        status: str,
        error: str | None,
        attempt: int,
    ) -> None:
        """Phase 7 tool-call observability (spec 29). Never raises."""
        try:
            with get_session() as session:
                # tool_calls is written mid-run, before persist_run() creates
                # the workflow_runs row — ensure the parent row exists first
                # (persist_run upserts/refreshes it on finish).
                if session.get(WorkflowRunRow, run.workflow_id) is None:
                    session.add(
                        WorkflowRunRow(
                            id=run.workflow_id,
                            user_id=getattr(run, "user_id", "local"),
                            request=getattr(run, "request", ""),
                            status="running",
                        )
                    )
                    # Flush the parent row explicitly: the unit of work does
                    # not reliably order a same-session parent+child insert
                    # pair here, so the FK would fail without it.
                    session.flush()
                session.add(
                    ToolCall(
                        workflow_run_id=run.workflow_id,
                        tool_name=tool_name,
                        input=_sanitize(input_data or {}),
                        output=_sanitize(output) if output is not None else None,
                        status=status,
                        duration_ms=duration_ms,
                        error=error,  # user-safe message only, never a traceback
                        attempt=attempt,
                        provider=settings.spidey_provider,
                    )
                )
        except Exception:
            logger.exception("tool_calls insert failed for %s", tool_name)

    def get_debug_info(self, run_id: str) -> dict:
        """Phase 7 debug block for GET /api/workflow/{run_id}."""
        tool_calls = 0
        retries = 0
        try:
            with get_session() as session:
                rows = list(
                    session.scalars(
                        select(ToolCall).where(
                            ToolCall.workflow_run_id == run_id
                        )
                    )
                )
                tool_calls = len(rows)
                retries = sum(1 for r in rows if (r.attempt or 1) > 1)
        except Exception:
            logger.exception("debug info query failed for %s", run_id)
        model = getattr(self.provider, "model", None) or getattr(
            self.provider, "name", "unknown"
        )
        return {
            "provider": settings.spidey_provider,
            "model": model,
            "tool_calls": tool_calls,
            "retries": retries,
        }

    async def _run_tool_step(
        self,
        run,
        engine,
        step,
        tool_name: str,
        fn: Callable[[], Awaitable[Any]],
        input_data: dict | None,
    ) -> Any:
        """Execute a TOOL step with Phase 7 observability + one transient retry.

        Every attempt is recorded in tool_calls (attempt column); only a
        timeout ToolError ("timed out") is retried, exactly once. Validation
        and user errors fail immediately. Errors stored are user-safe only —
        no raw tracebacks, and sensitive input keys were already dropped by
        the sanitizer.
        """
        sanitized_input = _sanitize(input_data or {})
        attempt = 0
        while True:
            attempt += 1
            started = time.perf_counter()
            try:
                out = await fn()
            except ToolError as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                self._record_tool_call(
                    run,
                    tool_name,
                    sanitized_input,
                    None,
                    duration_ms,
                    "failed",
                    exc.user_message,
                    attempt,
                )
                if _TRANSIENT_MARKER in exc.user_message.lower() and attempt == 1:
                    continue  # one retry on transient timeout
                engine.update_step(
                    run.workflow_id, step.step_id, status="FAILED",
                    error=self._user_msg(exc),
                )
                raise
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                self._record_tool_call(
                    run,
                    tool_name,
                    sanitized_input,
                    None,
                    duration_ms,
                    "failed",
                    self._user_msg(exc),
                    attempt,
                )
                engine.update_step(
                    run.workflow_id, step.step_id, status="FAILED",
                    error=self._user_msg(exc),
                )
                raise
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._record_tool_call(
                run,
                tool_name,
                sanitized_input,
                self._safe(out),
                duration_ms,
                "completed",
                None,
                attempt,
            )
            engine.update_step(
                run.workflow_id, step.step_id, status="COMPLETED",
                output=self._safe(out),
            )
            return out

    def _stepper(self, run, engine):
        """Build the visible-step recorder shared by run() and _run_resume_steps.

        Every step is recorded on the workflow engine (WAITING -> RUNNING ->
        COMPLETED/FAILED) so the WorkflowPanel shows the full pipeline live.
        TOOL steps additionally get per-attempt tool_calls rows (Phase 7).
        """
        step_count = 0

        async def _step(
            name: str,
            step_type: str,
            fn: Callable[[], Awaitable[Any]],
            input_data: dict | None = None,
            tool_name: str | None = None,
        ) -> Any:
            nonlocal step_count
            step_count += 1
            if step_count > self.MAX_AGENT_STEPS:
                raise ToolError("Agent step limit exceeded.")
            step = engine.add_step(run.workflow_id, name, step_type, input_data)
            engine.update_step(run.workflow_id, step.step_id, status="RUNNING")
            if step_type == "tool" and tool_name:
                return await self._run_tool_step(
                    run, engine, step, tool_name, fn, input_data
                )
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

    @staticmethod
    def _accepts_history(fn: Callable) -> bool:
        """Backward compat: some providers predate the ``history`` kwarg.

        Existing callers/tests (e.g. the stub provider in the test suite)
        define ``aclassify_intent(message)`` / ``agenerate(message,
        context="")`` — calling those with ``history=`` would TypeError and
        fail the whole run, so we only pass it when the signature accepts it.
        """
        try:
            params = signature(fn).parameters
        except (TypeError, ValueError):
            return False
        if "history" in params:
            return True
        return any(
            p.kind == Parameter.VAR_KEYWORD for p in params.values()
        )

    async def _aclassify(self, message: str, history: list[dict]) -> dict:
        if self._accepts_history(self.provider.aclassify_intent):
            return await self.provider.aclassify_intent(message, history=history)
        return await self.provider.aclassify_intent(message)

    async def _agenerate(
        self, message: str, context: str, history: list[dict]
    ) -> str:
        if self._accepts_history(self.provider.agenerate):
            return await self.provider.agenerate(
                message, context=context, history=history
            )
        return await self.provider.agenerate(message, context=context)

    async def run(
        self,
        message: str,
        run,
        engine,
        history: list[dict] | None = None,
    ) -> str:
        # Conversation continuity: the last 10 turns travel with the message
        # so the provider can resolve pronoun follow-ups ("why would I use
        # it?"). The route bounds history; this is belt-and-braces.
        history = (history or [])[-10:]
        _step = self._stepper(run, engine)
        tool_results: dict[str, dict] = {}
        classification: dict | None = None
        facts = ""
        mems: list[dict] = []

        try:
            classification = await _step(
                "Understand request",
                "understand",
                lambda: self._aclassify(message, history),
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

            # Resolve all tool args up front (async: delete/complete intents
            # resolve titles to ids via a list call). A resolution failure
            # (e.g. nothing matched the ref) ends the run with the tool's
            # user-safe message instead of the generic fallback.
            tool_jobs: list[tuple[dict, str, dict]] = []
            for plan_step in plan:
                if plan_step.get("type") != "tool":
                    continue
                tool_name = plan_step["tool"]
                try:
                    args = await self._tool_args(tool_name, intent, message)
                except ToolError as exc:
                    engine.finish_run(
                        run.workflow_id, "failed", result=exc.user_message
                    )
                    return exc.user_message
                # Cross-resolution: the ref may have matched the sibling
                # tool's list (a reminder named like a task, or vice versa).
                tool_name = args.pop("_resolved_tool", None) or tool_name
                tool_jobs.append((plan_step, tool_name, args))

            # Confirmation gate (spec 21): "confirm"-level actions are never
            # auto-executed via chat. Finish the run as awaiting_confirmation
            # with a token the user can approve or drop.
            gated = next(
                (
                    job
                    for job in tool_jobs
                    if self._action_permission(job[1], job[2].get("action"), job[2])
                    == "confirm"
                ),
                None,
            )
            if gated is not None:
                plan_step, tool_name, args = gated
                proposal = self._proposal(tool_name, args)
                token = secrets.token_urlsafe(24)
                self._pending[token] = {
                    "tool": tool_name,
                    "args": args,
                    "message": message,
                    "classification": classification,
                    "plan_step": plan_step,
                    "proposal": proposal,
                    "expires_at": time.time() + CONFIRM_TTL_SECONDS,
                }
                async def _proposal_out() -> dict:
                    return {"proposal": proposal}

                await _step(
                    "Request confirmation",
                    "confirm",
                    _proposal_out,
                    input_data={"tool": tool_name, "action": args.get("action")},
                )
                payload = json.dumps(
                    {
                        "needs_confirmation": True,
                        "proposal": proposal,
                        "confirm_token": token,
                    }
                )
                engine.finish_run(
                    run.workflow_id, "awaiting_confirmation", result=payload
                )
                return payload

            # Tool execution + bounded re-plan (Phase 7): if "Verify results"
            # fails, drop the failed tool's result and re-run the remaining
            # plan steps exactly once (replan_count max 1), then compose the
            # response from whatever verified. MAX_AGENT_STEPS still caps the
            # total steps and raises cleanly.
            replan_count = 0
            pending_jobs: list[tuple[dict, str, dict]] = list(tool_jobs)
            while pending_jobs:
                current_jobs, pending_jobs = pending_jobs, []
                for plan_step, tool_name, args in current_jobs:
                    try:
                        tool_results[tool_name] = await _step(
                            plan_step["name"],
                            "tool",
                            lambda _t=tool_name, _a=args: self.tools[_t].execute(**_a),
                            input_data=args,
                            tool_name=tool_name,
                        )
                    except ToolError as exc:
                        if tool_name == "search":
                            # Graceful degradation: report the outage, not a crash.
                            tool_results[tool_name] = {
                                "results": [],
                                "error": exc.user_message,
                            }
                        else:
                            raise
                if not tool_results:
                    break
                try:
                    await _step(
                        "Verify results",
                        "verify",
                        lambda: self._verify(tool_results),
                    )
                except ToolError:
                    failed_tool = self._last_verify_failure
                    tool_results.pop(failed_tool, None)
                    if replan_count >= 1:
                        # Re-plan budget spent: compose from whatever verified.
                        break
                    replan_count += 1
                    pending_jobs = [
                        (ps, tn, a)
                        for (ps, tn, a) in tool_jobs
                        if tn in tool_results
                    ]

            facts = self._compose_facts(intent, message, tool_results, mems)
            response = await _step(
                "Compose response",
                "respond",
                lambda: self._agenerate(message, facts, history),
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

    async def run_confirmed(
        self,
        pending: dict,
        run,
        engine,
        history: list[dict] | None = None,
    ) -> str:
        """Execute a user-confirmed pending action through the normal workflow.

        Shows Execute/Verify steps on a fresh run, then invalidates the token
        (already consumed by ``pop_pending``).
        """
        history = (history or [])[-10:]
        _step = self._stepper(run, engine)
        tool_name = pending["tool"]
        args = pending["args"]
        message = pending["message"]
        classification = pending["classification"]
        intent = classification.get("intent", "chat_fallback")
        try:
            mems: list[dict] = []
            if classification.get("requires_memory"):
                mems = await _step(
                    "Retrieve memory",
                    "memory",
                    lambda: self.memory.retrieve_relevant(message),
                )
            # ``_confirmed`` marks args as user-approved: tools with
            # args-dependent gating (the system controller) execute instead of
            # re-requesting confirmation. Other tools ignore the extra kwarg.
            confirmed_args = dict(args, _confirmed=True)
            result = await _step(
                pending["plan_step"]["name"],
                "tool",
                lambda: self.tools[tool_name].execute(**confirmed_args),
                input_data=args,
                tool_name=tool_name,
            )
            tool_results = {tool_name: result}
            await _step(
                "Verify results", "verify", lambda: self._verify(tool_results)
            )
            facts = self._compose_facts(intent, message, tool_results, mems)
            response = await _step(
                "Compose response",
                "respond",
                lambda: self._agenerate(message, facts, history),
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
                tool_name="resume",
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
                tool_name="resume",
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
                tool_name="resume",
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
        self._last_verify_failure = None
        for tool_name, result in tool_results.items():
            if not isinstance(result, dict) or not result:
                self._last_verify_failure = tool_name
                raise ToolError("Verification failed.")
            expected = _VERIFY_KEYS.get(tool_name, ())
            if expected and not any(k in result for k in expected):
                self._last_verify_failure = tool_name
                raise ToolError("Verification failed.")
        return {"verified": True}

    async def _update_memory(self, intent: str, message: str) -> dict:
        if intent == "remember":
            return {"saved": True, "reason": "already saved by memory tool"}
        return {"saved": False, "reason": "nothing salient"}

    async def _tool_args(self, tool: str, intent: str, message: str) -> dict:
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
            if intent in ("task_complete", "task_delete"):
                tool_name, item = await self._resolve_item_cross(
                    "tasks", "task", "reminders", "reminder", message
                )
                action = "complete" if intent == "task_complete" else "delete"
                args = {"action": action, "id": item["id"], "title": item["title"]}
                if tool_name != "tasks":
                    # The ref matched a reminder, not a task: tell the run
                    # loop to execute against the reminders tool.
                    args["_resolved_tool"] = tool_name
                return args
            return {"action": "list"}
        if tool == "reminders":
            if intent == "reminder_create":
                return {
                    "action": "create",
                    "title": extract_reminder_title(message),
                    "remind_at": parse_reminder_at(message).isoformat(),
                }
            if intent in ("reminder_complete", "reminder_delete"):
                tool_name, item = await self._resolve_item_cross(
                    "reminders", "reminder", "tasks", "task", message
                )
                action = "complete" if intent == "reminder_complete" else "delete"
                args = {"action": action, "id": item["id"], "title": item["title"]}
                if tool_name != "reminders":
                    args["_resolved_tool"] = tool_name
                return args
            return {"action": "list"}
        if tool == "search":
            return {"query": self._extract_search_query(message)}
        if tool == "system":
            # The rule-based intent system_status only ever requests metrics;
            # destructive phrasing is never routed to this tool.
            return {"action": "metrics"}
        if tool == "documents":
            return self._extract_document(message)
        if tool == "code":
            code, language = self._extract_code(message)
            return {"code": code, "language": language or None}
        if tool == "rag":
            # summarize_document benefits from more context than a plain search
            limit = 10 if intent == "summarize_document" else 5
            return {"action": "search", "query": message, "limit": limit}
        if tool == "resume":
            # The dedicated resume pipeline builds its own args per stage;
            # this default keeps the generic loop safe.
            return {"action": "latest"}
        return {}

    async def _resolve_item(
        self, tool_name: str, kind: str, message: str
    ) -> dict:
        """Resolve 'delete task 1' / 'complete the reminder to call mom' to a row.

        Matches by 1-based ordinal, id prefix, or title substring. Raises
        ToolError when nothing matches.
        """
        ref = self._extract_ref(message, kind)
        out = await self.tools[tool_name].execute(action="list")
        items = out.get("tasks" if tool_name == "tasks" else "reminders", [])
        title_key = "title"
        if ref:
            if ref.isdigit():
                idx = int(ref) - 1
                if 0 <= idx < len(items):
                    return items[idx]
            lowered = ref.lower()
            for item in items:
                if item["id"] == ref or item["id"].startswith(ref):
                    return item
            for item in items:
                if lowered in str(item.get(title_key, "")).lower():
                    return item
            raise ToolError(
                f"I couldn't find a {kind} matching {ref!r}."
            )
        if len(items) == 1:
            return items[0]
        raise ToolError(
            f"Which {kind}? Say e.g. 'delete {kind} 1' or match its title."
        )

    async def _resolve_item_cross(
        self,
        primary_tool: str,
        primary_kind: str,
        fallback_tool: str,
        fallback_kind: str,
        message: str,
    ) -> tuple[str, dict]:
        """Resolve an item ref against the primary tool, falling back to the
        other tool when nothing matches there ("mark study complete" where
        "study" is a reminder, not a task). Returns (tool_name, item).

        Only "couldn't find" errors fall through, and only when the message
        names neither kind ("task"/"reminder"): "delete task 99999" must
        fail as a task lookup, never wander into the reminders list. An
        ambiguous ref ("Which task?") also stays put. Deletes remain behind
        the confirmation gate, so a cross-resolved delete still asks first.
        """
        try:
            item = await self._resolve_item(primary_tool, primary_kind, message)
            return primary_tool, item
        except ToolError as exc:
            if "couldn't find" not in exc.user_message:
                raise exc
            if re.search(r"\b(tasks?|reminders?)\b", message, re.IGNORECASE):
                raise exc
        item = await self._resolve_item(fallback_tool, fallback_kind, message)
        return fallback_tool, item

    @staticmethod
    def _extract_ref(message: str, kind: str) -> str | None:
        m = re.search(
            rf"\b(?:delete|remove|drop|cancel|complete|finish|mark)\b"
            rf"\s+(?:the\s+)?{kind}s?\s+(?:called\s+|named\s+)?(.+)",
            message,
            re.IGNORECASE,
        )
        if m:
            ref = m.group(1).strip()
            ref = re.sub(r"\s+as\s+done\s*$", "", ref, flags=re.IGNORECASE)
            ref = ref.rstrip(".?!").strip()
            return ref or None
        # Natural phrasing without the kind word, e.g.
        # "mark my Python project complete" -> "Python project".
        m = re.search(
            r"\bmark\s+(?:my\s+)?(.+?)\s+(?:as\s+)?complete\b",
            message,
            re.IGNORECASE,
        )
        if m:
            ref = m.group(1).strip().rstrip(".?!").strip()
            return ref or None
        return None

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
        # Natural phrasing: "add finish my project to my tasks".
        m = re.search(
            r"\badd\s+(.+?)\s+to\s+my\s+tasks?\b",
            message,
            re.IGNORECASE | re.DOTALL,
        )
        if m and m.group(1).strip():
            return m.group(1).strip().rstrip(".")
        m = re.search(
            r"(?:create|add)\s+(?:a\s+)?task(?:\s+for\s+tomorrow)?"
            r"(?::\s*|\s+to\s+|\s+)?(.+)",
            message,
            re.IGNORECASE,
        )
        if m and m.group(1).strip():
            return m.group(1).strip().rstrip(".")
        return "Untitled task"

    @staticmethod
    def _extract_due(message: str) -> str | None:
        # Owned by the task tool; the agent reuses it for chat extraction.
        return parse_due(message)

    @staticmethod
    def _extract_search_query(message: str) -> str:
        for pattern in (
            r"search\s+for\s+the\s+latest\s+information\s+about\s+(.+)",
            r"\bsearch\s+(?:the\s+)?latest\s+(?:news\s+)?(?:about|on)\s+(.+)",
        ):
            m = re.search(pattern, message, re.IGNORECASE | re.DOTALL)
            if m and m.group(1).strip():
                prefix = (
                    "latest information about "
                    if "information" in pattern
                    else "latest "
                )
                return (prefix + m.group(1).strip()).rstrip(".")
        for pattern in (
            r"search\s+the\s+web\s+for\s+(.+)",
            r"search\s+(?:the\s+)?(?:web|internet|online)\s+(?:for\s+)?(.+)",
            r"(?:find|get)\s+(?:me\s+)?information\s+about\s+(.+)",
            r"\bgoogle\s+(.+)",
        ):
            m = re.search(pattern, message, re.IGNORECASE | re.DOTALL)
            if m and m.group(1).strip():
                return m.group(1).strip().rstrip(".")
        return message.strip()

    @staticmethod
    def _extract_document(message: str) -> dict:
        title, content, fmt = "Untitled document", "", "txt"
        m = re.search(
            r"titled\s+[\"']?([^\"':]+?)[\"']?\s*(?::|with\s+content)\s*(.+)",
            message,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            title = m.group(1).strip()
            content = m.group(2).strip()
        else:
            m2 = re.search(
                r"(?:create|make|write)\s+(?:a\s+)?(?:document|note)\s*:?\s*(.+)",
                message,
                re.IGNORECASE | re.DOTALL,
            )
            if m2:
                content = m2.group(1).strip()
        if re.search(r"\bmarkdown\b", message, re.IGNORECASE):
            fmt = "md"
        return {"action": "create", "title": title, "content": content, "format": fmt}

    @staticmethod
    def _extract_code(message: str) -> tuple[str, str]:
        m = re.search(r"```(\w*)\s*\n?(.*?)```", message, re.DOTALL)
        if m:
            return m.group(2).strip(), m.group(1).strip()
        m = re.search(
            r"(?:explain\s+(?:this\s+)?code|what\s+does\s+(?:this|the)\s+code\s+do)"
            r"\s*:?\s*(.+)",
            message,
            re.IGNORECASE | re.DOTALL,
        )
        if m and m.group(1).strip():
            return m.group(1).strip(), ""
        return message.strip(), ""

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
                return f"{calc.get('expression', '')} = {calc['result']}, sir."
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
            reply = (
                f"Task created: {title}" + (f" (due {due})" if due else "") + "."
                " Done, sir — one less thing to worry about."
            )
            # Deterministic variety, one short sentence, no verbosity.
            if len(title) % 2 == 0:
                reply += " Anything else to add?"
            return reply
        if intent == "task_list":
            tasks = tool_results.get("tasks", {}).get("tasks", [])
            if tasks:
                lines = "\n".join(
                    f"- [{'x' if t.get('done') else ' '}] {t.get('title')}"
                    for t in tasks
                )
                return f"Your tasks:\n{lines}"
            return "No tasks yet — say 'create a task' to add one."
        if intent == "task_complete":
            # Cross-resolution may have executed against the reminders tool.
            task = tool_results.get("tasks", {}).get("task") or tool_results.get(
                "reminders", {}
            ).get("reminder", {})
            title = task.get("title") or task.get("text", "")
            return f'Done, sir — "{title}" marked complete.'
        if intent == "task_delete":
            deleted = tool_results.get("tasks", {}) or tool_results.get(
                "reminders", {}
            )
            title = deleted.get("title") or deleted.get("text", "")
            return f'Deleted the task "{title}".'
        if intent == "reminder_create":
            reminder = tool_results.get("reminders", {}).get("reminder", {})
            at = reminder.get("remind_at")
            text = reminder.get("text", "")
            return (
                f"Reminder set: {text}" + (f" (at {at})" if at else "") + "."
                " Consider it handled, sir."
            )
        if intent == "reminder_list":
            reminders = tool_results.get("reminders", {}).get("reminders", [])
            if reminders:
                lines = "\n".join(
                    f"- [{'x' if r.get('done') else ' '}] {r.get('text')}"
                    + (f" (at {r['remind_at']})" if r.get("remind_at") else "")
                    for r in reminders
                )
                return f"Your reminders:\n{lines}"
            return "No reminders yet — say 'remind me to ...' to add one."
        if intent == "reminder_complete":
            reminder = tool_results.get("reminders", {}).get(
                "reminder"
            ) or tool_results.get("tasks", {}).get("task", {})
            text = reminder.get("text") or reminder.get("title", "")
            return f'Reminder done: "{text}".'
        if intent == "reminder_delete":
            deleted = tool_results.get("reminders", {}) or tool_results.get(
                "tasks", {}
            )
            text = deleted.get("text") or deleted.get("title", "")
            return f'Deleted the reminder "{text}".'
        if intent == "web_search":
            search = tool_results.get("search", {})
            if search.get("error"):
                return search["error"]
            results = search.get("results", [])
            if not results:
                return (
                    "The web search came back empty — "
                    "try rephrasing with different words."
                )
            lines = []
            for r in results[:5]:
                title = r.get("title", "")
                url = r.get("url", "")
                snippet = (r.get("snippet", "") or "")[:200]
                lines.append(f"- {title} ({url})\n  {snippet}")
            return "Found it, sir. Here's what the web says:\n" + "\n".join(lines)
        if intent == "document_create":
            doc = tool_results.get("documents", {}).get("document", {})
            return (
                f"Document created: \"{doc.get('title')}\". "
                f"Download it at {doc.get('download_url')}."
            )
        if intent == "system_status":
            m = tool_results.get("system", {})
            if m.get("needs_confirmation"):
                return m.get("proposal", "")
            lines = ["All systems nominal, sir. Current readings:"]
            lines.append(f"- CPU load: {m.get('cpu_percent', 0.0):.1f}%")
            ram = m.get("ram") or {}
            lines.append(
                f"- Memory: {ram.get('percent', 0.0):.1f}% "
                f"({ram.get('used_gb', 0.0)}/{ram.get('total_gb', 0.0)} GB)"
            )
            disk = m.get("disk") or {}
            lines.append(f"- Disk: {disk.get('percent', 0.0):.1f}%")
            uptime = int(m.get("uptime_seconds") or 0)
            hours, rem = divmod(uptime, 3600)
            minutes, _ = divmod(rem, 60)
            lines.append(f"- Uptime: {hours}h {minutes}m")
            batt = m.get("battery")
            if batt:
                lines.append(
                    f"- Battery: {batt.get('percent')}% "
                    f"({'charging' if batt.get('plugged') else 'on battery'})"
                )
            return "\n".join(lines)
        if intent == "code_explain":
            code = tool_results.get("code", {})
            lines = [code.get("explanation", "")]
            lines.extend(f"- {o}" for o in code.get("observations", []))
            return "\n".join(line for line in lines if line)
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
        # Name the actual source documents (distinct, in first-seen order) —
        # never claim a doc source when there are none.
        sources: list[str] = []
        for r in results:
            source = r.get("source") or "document"
            if source not in sources:
                sources.append(source)
        named = ", ".join(f"`{s}`" for s in sources)
        lines = []
        for r in results:
            source = r.get("source") or "document"
            chunk_index = r.get("chunk_index", "?")
            content = (r.get("content") or "")[:600]
            lines.append(f"[{source}, chunk {chunk_index}]\n{content}")
        return (
            f"Based on your {named}:\n\n"
            + "\n\n".join(lines)
            + "\n\nAnswer using only these passages, citing each claim like "
            "[filename, chunk N]."
        )
