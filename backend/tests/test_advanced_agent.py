"""Phase 7 — advanced agent: retries, bounded re-plan, observability, debug,
workflow persistence.

Runs against the throwaway sqlite DB from conftest.py (same as the rest of
the suite).
"""
import asyncio

import pytest
from sqlalchemy import select

from app.agents.spidey_agent import SpideyAgent
from app.config import settings
from app.database import get_session
from app.models import ToolCall
from app.providers.rule_based import RuleBasedProvider
from app.tools.base import BaseTool, ToolError
from app.workflows.engine import WorkflowEngine


class _MemoryStub(BaseTool):
    name = "memory"
    description = "stub"
    input_schema = {}
    output_schema = {}
    permission = "read"

    async def _run(self, **kwargs):
        return {"results": []}


def _agent(tools: dict) -> SpideyAgent:
    provider = RuleBasedProvider()
    merged = {"memory": _MemoryStub(), **tools}
    return SpideyAgent(provider, merged)


def _rows_for(run_id: str):
    with get_session() as session:
        return list(
            session.scalars(
                select(ToolCall)
                .where(ToolCall.workflow_run_id == run_id)
                .order_by(ToolCall.attempt)
            )
        )


class FlakyTool(BaseTool):
    """Fails with a timeout ToolError exactly once, then succeeds."""

    name = "flaky"
    description = "flaky"
    input_schema = {}
    output_schema = {}
    permission = "read"

    def __init__(self):
        self.calls = 0

    async def _run(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ToolError("flaky timed out.")
        return {"result": "ok"}


class HardFailTool(BaseTool):
    """Non-transient user error — must never be retried."""

    name = "hardfail"
    description = "hardfail"
    input_schema = {}
    output_schema = {}
    permission = "read"

    def __init__(self):
        self.calls = 0

    async def _run(self, **kwargs):
        self.calls += 1
        raise ToolError("I don't understand that input.")


class SanitizerTool(BaseTool):
    name = "sanitizer"
    description = "sanitizer"
    input_schema = {}
    output_schema = {}
    permission = "read"

    async def _run(self, **kwargs):
        return {"echo": "done", "blob": "y" * 600}


async def test_retry_exactly_once_on_timeout():
    """(a) A tool that fails with a timeout ToolError once succeeds after
    exactly 1 retry; both attempts are recorded in tool_calls."""
    tool = FlakyTool()
    agent = _agent({"flaky": tool})
    engine = WorkflowEngine()
    run = engine.create_run("flaky test")
    _step = agent._stepper(run, engine)

    out = await _step(
        "Run flaky", "tool", lambda: tool.execute(), tool_name="flaky"
    )

    assert out == {"result": "ok"}
    assert tool.calls == 2
    rows = _rows_for(run.workflow_id)
    assert len(rows) == 2
    assert [r.attempt for r in rows] == [1, 2]
    assert rows[0].status == "failed" and "timed out" in (rows[0].error or "")
    assert rows[1].status == "completed" and rows[1].error is None
    assert all(r.duration_ms is not None and r.duration_ms >= 0 for r in rows)
    assert rows[0].provider == settings.spidey_provider


async def test_non_transient_error_not_retried():
    """(b) A validation/user ToolError is NOT retried — one attempt only."""
    tool = HardFailTool()
    agent = _agent({"hardfail": tool})
    engine = WorkflowEngine()
    run = engine.create_run("hardfail test")
    _step = agent._stepper(run, engine)

    with pytest.raises(ToolError):
        await _step(
            "Run hardfail", "tool", lambda: tool.execute(), tool_name="hardfail"
        )

    assert tool.calls == 1
    rows = _rows_for(run.workflow_id)
    assert len(rows) == 1
    assert rows[0].attempt == 1 and rows[0].status == "failed"
    # User-safe message stored; no traceback.
    assert rows[0].error == "I don't understand that input."
    assert "Traceback" not in (rows[0].error or "")


class _GoodTool(BaseTool):
    name = "good"
    description = "good"
    input_schema = {}
    output_schema = {}
    permission = "read"

    async def _run(self, **kwargs):
        return {"result": 42}


class _BadTool(BaseTool):
    name = "bad"
    description = "bad"
    input_schema = {}
    output_schema = {}
    permission = "read"

    async def _run(self, **kwargs):
        return {}  # fails verification (empty dict)


class _StubProvider:
    name = "stub"

    def __init__(self, tools):
        self._tools = tools
        self.model = "stub-model"

    async def aclassify_intent(self, message):
        return {
            "intent": "multi",
            "tools": self._tools,
            "requires_memory": False,
        }

    async def agenerate(self, message, context=""):
        return f"reply: {context}"


async def test_verify_failure_replans_once_then_completes():
    """(c) A verify failure drops the bad tool, re-runs the rest once, and
    the run still completes — at most one re-plan."""
    provider = _StubProvider(["good", "bad"])
    agent = SpideyAgent(provider, {"memory": _MemoryStub(), "good": _GoodTool(), "bad": _BadTool()})
    engine = WorkflowEngine()
    run = engine.create_run("multi tool")

    resp = await agent.run("multi tool", run, engine)

    finished = engine.get_run(run.workflow_id)
    assert finished.status == "completed"
    assert resp  # a response was composed from whatever verified
    verifies = [s for s in finished.steps if s.name == "Verify results"]
    assert len(verifies) == 2  # initial attempt + exactly one re-plan
    assert verifies[0].status == "FAILED" and verifies[1].status == "COMPLETED"
    good_runs = [s for s in finished.steps if s.name == "Execute good"]
    assert len(good_runs) == 2  # re-ran once after the failed tool was dropped


async def test_double_verify_failure_still_completes():
    """(c2) When the re-plan also fails verification, the run composes from
    whatever verified instead of looping."""
    provider = _StubProvider(["bad", "alsobad"])

    class _AlsoBad(_BadTool):
        name = "alsobad"

    agent = SpideyAgent(
        provider,
        {"memory": _MemoryStub(), "bad": _BadTool(), "alsobad": _AlsoBad()},
    )
    engine = WorkflowEngine()
    run = engine.create_run("double bad")

    resp = await agent.run("double bad", run, engine)

    finished = engine.get_run(run.workflow_id)
    assert finished.status == "completed"
    assert resp
    verifies = [s for s in finished.steps if s.name == "Verify results"]
    assert len(verifies) == 2  # never more than initial + one re-plan


async def test_max_agent_steps_raises_cleanly():
    """(d) The step cap raises a clean ToolError (no hang, no traceback)."""
    agent = _agent({})
    engine = WorkflowEngine()
    run = engine.create_run("cap test")
    _step = agent._stepper(run, engine)

    async def _noop():
        await asyncio.sleep(0)
        return {}

    for _ in range(SpideyAgent.MAX_AGENT_STEPS):
        await _step("noop", "noop", _noop)
    with pytest.raises(ToolError, match="step limit exceeded"):
        await _step("one too many", "noop", _noop)


async def test_tool_call_sanitization():
    """(e) tool_calls rows carry duration_ms; sensitive input keys are
    dropped and long strings truncated."""
    tool = SanitizerTool()
    agent = _agent({"sanitizer": tool})
    engine = WorkflowEngine()
    run = engine.create_run("sanitize test")
    _step = agent._stepper(run, engine)

    await _step(
        "Run sanitizer",
        "tool",
        lambda: tool.execute(),
        input_data={
            "password": "hunter2",
            "api_key": "sekret",
            "token": "tok",
            "secret": "s3cr3t",
            "query": "x" * 600,
        },
        tool_name="sanitizer",
    )

    rows = _rows_for(run.workflow_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.duration_ms is not None
    assert row.status == "completed"
    for dropped in ("password", "api_key", "token", "secret"):
        assert dropped not in (row.input or {})
    assert row.input["query"] == "x" * 500
    # Output summary is truncated too.
    assert row.output["blob"] == "y" * 500


async def test_debug_flag_toggles_truncation():
    """(f) SPIDEY_DEBUG=true keeps full step output; false truncates."""
    agent = _agent({})
    big = {"k": "v" * 600}
    old = settings.spidey_debug
    try:
        settings.spidey_debug = False
        assert agent._safe(big)["k"] == "v" * 500
        settings.spidey_debug = True
        assert agent._safe(big)["k"] == "v" * 600
    finally:
        settings.spidey_debug = old


async def test_finished_run_in_db_backed_activity():
    """(g) A finished run is persisted and shows up in GET /api/activity."""
    import app.routes.chat as chat_routes

    run = chat_routes.engine.create_run("persist me")
    step = chat_routes.engine.add_step(run.workflow_id, "Only step", "noop")
    chat_routes.engine.update_step(
        run.workflow_id, step.step_id, status="COMPLETED", output={"ok": True}
    )
    chat_routes.engine.finish_run(run.workflow_id, "completed", result="done")

    runs = await chat_routes.activity()
    match = next(
        (r for r in runs if r["workflow_id"] == run.workflow_id), None
    )
    assert match is not None
    assert match["status"] == "completed"
    assert match["result"] == "done"
    assert len(match["steps"]) == 1

    # DB fallback reconstructs the same shape.
    from app.workflows.persistence import get_persisted_run

    db = get_persisted_run(run.workflow_id)
    assert db is not None and db["status"] == "completed"
    assert db["steps"][0]["name"] == "Only step"


async def test_debug_block_on_workflow_endpoint():
    """Debug block appears only when SPIDEY_DEBUG=true."""
    import app.routes.chat as chat_routes
    from fastapi import HTTPException

    run = chat_routes.engine.create_run("debug me")
    chat_routes.engine.finish_run(run.workflow_id, "completed", result="r")
    old = settings.spidey_debug
    try:
        settings.spidey_debug = False
        payload = await chat_routes.get_workflow(run.workflow_id)
        assert "debug" not in payload
        settings.spidey_debug = True
        payload = await chat_routes.get_workflow(run.workflow_id)
        assert payload["debug"]["provider"] == settings.spidey_provider
        assert payload["debug"]["model"] == "rule_based"
        assert isinstance(payload["debug"]["tool_calls"], int)
        assert isinstance(payload["debug"]["retries"], int)
        with pytest.raises(HTTPException):
            await chat_routes.get_workflow("does-not-exist")
    finally:
        settings.spidey_debug = old
