"""MISSION J.A.R.V.I.S. — backend tests.

(a) system tool allowlist gating
(b) /api/system/metrics shape
(c) protocols trigger/audit/404
(d) ReminderTool.list_due claim-once semantics
(e) JARVIS persona ("sir") in rule-based responses
Plus: args-aware permission gating and the pending-confirmation round trip
for a non-allowlisted system command (mirrors the chat confirm_token flow).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
import secrets
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.spidey_agent import SpideyAgent
from app.database import get_session
from app.main import app
from app.models import ProtocolAudit
from app.providers import get_provider
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.tools.base import ToolError
from app.workflows.engine import engine

client = TestClient(app)
system = TOOL_REGISTRY["system"]


# --- (a) allowlist -----------------------------------------------------------


async def test_run_allowlisted_command_ok():
    out = await system.execute(action="run", command="ls -la")
    assert out["action"] == "run"
    assert out["exit_code"] == 0
    assert isinstance(out["stdout"], str)


async def test_run_non_allowlisted_needs_confirmation():
    out = await system.execute(action="run", command="rm -rf /")
    assert out["needs_confirmation"] is True
    assert out["proposal"] == "Run shell command: rm -rf /"


async def test_run_cat_outside_sandbox_denied():
    with pytest.raises(ToolError) as excinfo:
        await system.execute(action="run", command="cat /etc/passwd")
    assert "denied" in excinfo.value.user_message.lower()


async def test_run_cat_inside_workspace_ok():
    out = await system.execute(
        action="run", command="cat ~/workspace/spidey/README.md"
    )
    assert out["exit_code"] == 0
    assert len(out["stdout"]) > 0


async def test_run_dotdot_escape_denied():
    with pytest.raises(ToolError):
        await system.execute(
            action="run", command="cat ~/workspace/../spidey/README.md"
        )


async def test_run_tmp_file_ok(tmp_path):
    target = tmp_path / "jarvis_probe.txt"
    target.write_text("probe-ok")
    out = await system.execute(action="run", command=f"cat {target}")
    assert out["exit_code"] == 0
    assert "probe-ok" in out["stdout"]


async def test_open_website_needs_confirmation():
    out = await system.execute(action="open_website", url="https://example.com")
    assert out["needs_confirmation"] is True
    assert "https://example.com" in out["proposal"]


async def test_open_website_bad_scheme_denied():
    with pytest.raises(ToolError):
        await system.execute(action="open_website", url="file:///etc/passwd")


async def test_open_website_confirmed_returns_action():
    out = await system.execute(
        action="open_website", url="https://example.com", _confirmed=True
    )
    assert out == {"action": "open_website", "url": "https://example.com"}


async def test_metrics_shape():
    out = await system.execute(action="metrics")
    assert isinstance(out["cpu_percent"], float)
    assert set(out["ram"]) == {"percent", "used_gb", "total_gb"}
    assert "percent" in out["disk"]
    assert isinstance(out["uptime_seconds"], int)
    assert out["battery"] is None or set(out["battery"]) == {"percent", "plugged"}
    assert isinstance(out["host"], str) and out["host"]


# --- (b) metrics endpoint ----------------------------------------------------


def test_system_metrics_endpoint():
    resp = client.get("/api/system/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["cpu_percent"], float)
    assert set(body["ram"]) == {"percent", "used_gb", "total_gb"}
    assert "percent" in body["disk"]
    assert isinstance(body["uptime_seconds"], int)
    assert "host" in body


# --- (c) protocols -----------------------------------------------------------


def test_list_protocols():
    resp = client.get("/api/protocols")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()]
    assert ids == [
        "clean_slate",
        "house_party",
        "sentry_mode",
        "mute_audio",
        "silent_running",
    ]
    assert all("description" in p and "sfx" in p for p in resp.json())


def test_trigger_protocol_writes_audit():
    resp = client.post("/api/protocols/trigger", json={"id": "sentry_mode"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "sentry_mode"
    assert (
        body["response_text"]
        == "Sentry mode engaged, sir. All sensors at full alert."
    )
    assert body["sfx"] == "protocol_alert"
    assert body["audit_id"]
    with get_session() as session:
        row = session.get(ProtocolAudit, body["audit_id"])
        assert row is not None
        assert row.protocol_id == "sentry_mode"
        assert row.protocol_name == "Sentry Mode"


def test_trigger_unknown_protocol_404():
    resp = client.post("/api/protocols/trigger", json={"id": "order_66"})
    assert resp.status_code == 404


def test_protocol_audit_lists_recent():
    client.post("/api/protocols/trigger", json={"id": "mute_audio"})
    resp = client.get("/api/protocols/audit")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) >= 1
    assert entries[0]["protocol_id"] in {
        "clean_slate",
        "house_party",
        "sentry_mode",
        "mute_audio",
        "silent_running",
    }


# --- (d) reminder list_due ---------------------------------------------------


async def test_list_due_claims_once():
    tool = TOOL_REGISTRY["reminders"]
    past = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).replace(tzinfo=None).isoformat()
    created = await tool.execute(
        action="create", title="jarvis due probe", remind_at=past
    )
    rid = created["reminder"]["id"]

    first = tool.list_due()
    assert any(r["id"] == rid for r in first)
    claimed = next(r for r in first if r["id"] == rid)
    assert set(claimed) == {"id", "title", "remind_at"}

    second = tool.list_due()
    assert not any(r["id"] == rid for r in second)

    # Cleanup: the probe reminder stays done=False; mark done for tidiness.
    await tool.execute(action="complete", id=rid)


async def test_list_due_ignores_future_reminders():
    tool = TOOL_REGISTRY["reminders"]
    future = (
        datetime.now(timezone.utc) + timedelta(hours=2)
    ).replace(tzinfo=None).isoformat()
    created = await tool.execute(
        action="create", title="jarvis future probe", remind_at=future
    )
    rid = created["reminder"]["id"]
    try:
        due = tool.list_due()
        assert not any(r["id"] == rid for r in due)
    finally:
        await tool.execute(action="delete", id=rid)


# --- (e) persona --------------------------------------------------------------


async def test_rule_based_greeting_says_sir():
    provider = RuleBasedProvider()
    assert "sir" in (await provider.agenerate("hello")).lower()


async def test_rule_based_calc_says_sir():
    provider = RuleBasedProvider()
    facts = SpideyAgent._compose_facts(
        "calculate",
        "what is 2+2",
        {"calculator": {"expression": "2 + 2", "result": 4}},
        [],
    )
    assert "sir" in facts.lower()
    assert "sir" in (await provider.agenerate("what is 2+2", context=facts)).lower()


async def test_system_status_intent_routes_to_system_tool():
    provider = RuleBasedProvider()
    for phrase in ("system status", "cpu usage", "how is the system", "diagnostics"):
        classified = await provider.aclassify_intent(phrase)
        assert classified["intent"] == "system_status"
        assert classified["tools"] == ["system"]


async def test_destructive_phrasing_never_routes_to_system():
    provider = RuleBasedProvider()
    for phrase in ("rm -rf /", "delete all my files", "format the hard drive"):
        classified = await provider.aclassify_intent(phrase)
        assert "system" not in classified["tools"]


# --- permission gating + pending confirmation round trip ----------------------


def test_permission_for_args_aware():
    agent = SpideyAgent(get_provider(), TOOL_REGISTRY)
    assert agent._action_permission("system", "metrics", {"action": "metrics"}) == "read"
    assert (
        agent._action_permission("system", "run", {"command": "ls -la"}) == "read"
    )
    assert (
        agent._action_permission("system", "run", {"command": "rm -rf /"})
        == "confirm"
    )
    assert (
        agent._action_permission(
            "system", "open_website", {"url": "https://example.com"}
        )
        == "confirm"
    )
    # Existing tools keep their static permissions.
    assert agent._action_permission("tasks", "delete") == "confirm"
    assert agent._action_permission("tasks", "create") == "low_write"


async def test_confirmed_system_run_executes():
    """Mirror of the chat confirm_token flow: pending -> pop_pending ->
    run_confirmed executes with _confirmed=True."""
    agent = SpideyAgent(get_provider(), TOOL_REGISTRY)
    token = secrets.token_urlsafe(24)
    agent._pending[token] = {
        "tool": "system",
        "args": {"action": "run", "command": "echo confirmed-ok"},
        "message": "run echo confirmed-ok",
        "classification": {"intent": "chat_fallback"},
        "plan_step": {"name": "Run command"},
        "proposal": "Run shell command: echo confirmed-ok",
        "expires_at": time.time() + 600,
    }
    pending = agent.pop_pending(token)
    run = engine.create_run("confirmed: Run shell command: echo confirmed-ok")
    result = await agent.run_confirmed(pending, run, engine)
    assert isinstance(result, str)
    finished = engine.get_run(run.workflow_id)
    assert finished.status == "completed"
    tool_step = next(s for s in finished.steps if s.type == "tool")
    assert tool_step.output["stdout"].strip() == "confirmed-ok"
