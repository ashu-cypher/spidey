"""Phase 5 tests: DB-backed tasks, reminders, search, documents, code tool,
shell stub, and the chat confirmation flow (sqlite conftest)."""
import json
import time

import pytest

from app.agents.spidey_agent import SpideyAgent
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.tools.base import ToolError
from app.tools.code import CodeTool, guess_language, structural_observations
from app.tools.documents import DocumentTool
from app.tools.reminders import (
    ReminderTool,
    extract_reminder_title,
    parse_reminder_at,
)
from app.tools.search import SearchTool
from app.tools.shell import ShellTool
from app.tools.tasks import TaskTool, parse_due
from app.workflows.engine import WorkflowEngine


# --- Task tool (DB-backed) ---------------------------------------------------


async def test_task_crud_db():
    t = TaskTool()
    c = await t.execute(action="create", title="Buy milk", due="tomorrow")
    assert c["task"]["done"] is False
    assert c["task"]["due"] is not None  # natural-language due parsed
    task_id = c["task"]["id"]
    g = await t.execute(action="get", id=task_id)
    assert g["task"]["title"] == "Buy milk"
    d = await t.execute(action="complete", id=task_id)
    assert d["task"]["done"] is True
    u = await t.execute(action="set_done", id=task_id, done=False)
    assert u["task"]["done"] is False
    deleted = await t.execute(action="delete", id=task_id)
    assert deleted["deleted"] == task_id
    with pytest.raises(ToolError):
        await t.execute(action="get", id=task_id)


async def test_task_list_filters_to_created():
    t = TaskTool()
    c = await t.execute(action="create", title="phase5-unique-task-xyz")
    out = await t.execute(action="list")
    assert any(row["id"] == c["task"]["id"] for row in out["tasks"])
    await t.execute(action="delete", id=c["task"]["id"])


async def test_parse_due():
    from datetime import date, timedelta

    assert parse_due("buy milk tomorrow") == (date.today() + timedelta(days=1)).isoformat()
    assert parse_due("in 3 days") == (date.today() + timedelta(days=3)).isoformat()
    assert parse_due("no date here") is None


# --- Reminder tool -----------------------------------------------------------


async def test_reminder_crud():
    t = ReminderTool()
    c = await t.execute(action="create", title="Call mom", remind_at="2030-01-01T09:00:00")
    assert c["reminder"]["text"] == "Call mom"
    assert c["reminder"]["remind_at"].startswith("2030-01-01")
    rid = c["reminder"]["id"]
    out = await t.execute(action="list")
    assert any(r["id"] == rid for r in out["reminders"])
    done = await t.execute(action="complete", id=rid)
    assert done["reminder"]["done"] is True
    deleted = await t.execute(action="delete", id=rid)
    assert deleted["deleted"] == rid
    with pytest.raises(ToolError):
        await t.execute(action="create", title="   ")


def test_reminder_parsing():
    assert extract_reminder_title("remind me to call mom tomorrow") == "call mom"
    assert extract_reminder_title("remind me to stretch") == "stretch"
    at = parse_reminder_at("remind me to call mom tomorrow")
    assert (at.hour, at.minute) == (9, 0)
    at2 = parse_reminder_at("remind me to call mom tomorrow at 5pm")
    assert (at2.hour, at2.minute) == (17, 0)
    # vague -> tomorrow 9am
    vague = parse_reminder_at("remind me to drink water")
    assert (vague.hour, vague.minute) == (9, 0)


# --- Search tool -------------------------------------------------------------


class _ExplodingClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        raise ConnectionError("no network")

    async def __aexit__(self, *a):
        return False


async def test_search_graceful_degradation(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _ExplodingClient)
    t = SearchTool()
    with pytest.raises(ToolError) as exc:
        await t.execute(query="python")
    assert exc.value.user_message == "Web search is unavailable right now."
    assert "Traceback" not in str(exc.value)


async def test_search_rejects_empty_query():
    with pytest.raises(ToolError):
        await SearchTool().execute(query="   ")


# --- Document tool -----------------------------------------------------------


async def test_document_create_get_list_delete(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.documents._GENERATED_DIR", tmp_path)
    monkeypatch.setattr("app.tools.documents._INDEX_FILE", tmp_path / "index.json")
    t = DocumentTool()
    c = await t.execute(action="create", title="Test", content="hello", format="txt")
    doc = c["document"]
    assert doc["title"] == "Test"
    assert doc["download_url"] == f"/api/docs/{doc['id']}/download"
    g = await t.execute(action="get", id=doc["id"])
    assert g["document"]["path"].endswith(".txt")
    assert open(g["document"]["path"]).read() == "hello"
    out = await t.execute(action="list")
    assert any(d["id"] == doc["id"] for d in out["documents"])
    deleted = await t.execute(action="delete", id=doc["id"])
    assert deleted["deleted"] == doc["id"]
    with pytest.raises(ToolError):
        await t.execute(action="get", id=doc["id"])


async def test_document_download_route(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.documents._GENERATED_DIR", tmp_path)
    monkeypatch.setattr("app.tools.documents._INDEX_FILE", tmp_path / "index.json")
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    created = await DocumentTool().execute(
        action="create", title="RouteTest", content="download me", format="md"
    )
    doc_id = created["document"]["id"]
    res = client.get(f"/api/docs/{doc_id}/download")
    assert res.status_code == 200
    assert res.text == "download me"
    assert client.get("/api/docs/does-not-exist/download").status_code == 404
    await DocumentTool().execute(action="delete", id=doc_id)


# --- Code tool (structural fallback) -----------------------------------------


async def test_code_tool_structural_fallback():
    t = CodeTool()
    code = "def add(a, b):\n    # sum two numbers\n    return a + b\n"
    out = await t.execute(code=code)
    assert "explanation" in out
    assert "offline" in out["explanation"].lower() or "model" in out["explanation"].lower()
    joined = " ".join(out["observations"])
    assert "add" in joined  # detected function, honestly derived
    assert "3 lines" in joined


def test_guess_language_and_observations():
    assert guess_language("def f():\n    pass\n") == "python"
    obs = structural_observations("class Foo:\n    def bar(self):\n        pass\n")
    assert any("Foo" in o for o in obs)
    assert any("bar" in o for o in obs)


# --- Shell stub --------------------------------------------------------------


async def test_shell_stub_always_refused():
    t = ShellTool()
    with pytest.raises(ToolError) as exc:
        await t.execute(command="echo hi")
    assert "not enabled in SPIDEY v1" in exc.value.user_message


async def test_classifier_never_emits_shell():
    provider = RuleBasedProvider()
    for text in [
        "run ls in the terminal",
        "open the calculator app",
        "delete task 1",
        "search the web for cats",
        "execute a command",
    ]:
        c = await provider.aclassify_intent(text)
        assert "shell" not in c["tools"], text


# --- Permission levels (spec 21) ---------------------------------------------


def test_permission_table():
    expected = {
        "calculator": "read",
        "memory": "read",
        "rag": "read",
        "resume": "read",
        "search": "read",
        "code": "read",
        "tasks": "read",
        "reminders": "read",
        "documents": "read",
    }
    for name, level in expected.items():
        assert TOOL_REGISTRY[name].permission == level, name
    agent = SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)
    assert agent._action_permission("tasks", "delete") == "confirm"
    assert agent._action_permission("tasks", "create") == "low_write"
    assert agent._action_permission("tasks", "list") == "read"
    assert agent._action_permission("reminders", "delete") == "confirm"
    assert agent._action_permission("reminders", "create") == "low_write"
    assert agent._action_permission("documents", "delete") == "confirm"
    assert agent._action_permission("documents", "create") == "low_write"
    assert agent._action_permission("memory", "save") == "low_write"
    assert agent._action_permission("memory", "delete") == "confirm"
    assert agent._action_permission("shell", "command") == "confirm"


# --- Confirmation flow -------------------------------------------------------


def _agent():
    return SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)


async def test_delete_task_needs_confirmation_and_does_not_delete():
    agent = _agent()
    created = await TOOL_REGISTRY["tasks"].execute(
        action="create", title="phase5-confirm-target"
    )
    task_id = created["task"]["id"]
    engine = WorkflowEngine()
    run = engine.create_run("delete task phase5-confirm-target")
    payload = await agent.run("delete task phase5-confirm-target", run, engine)
    finished = engine.get_run(run.workflow_id)
    assert finished.status == "awaiting_confirmation"
    data = json.loads(payload)
    assert data["needs_confirmation"] is True
    assert "phase5-confirm-target" in data["proposal"]
    assert data["confirm_token"]
    # Not deleted yet.
    still = await TOOL_REGISTRY["tasks"].execute(action="get", id=task_id)
    assert still["task"]["id"] == task_id
    # Confirm via token: executes through the workflow and deletes.
    pending = agent.pop_pending(data["confirm_token"])
    run2 = engine.create_run("confirmed")
    resp = await agent.run_confirmed(pending, run2, engine)
    assert engine.get_run(run2.workflow_id).status == "completed"
    assert "Deleted" in resp
    with pytest.raises(ToolError):
        await TOOL_REGISTRY["tasks"].execute(action="get", id=task_id)
    # Token is single-use.
    with pytest.raises(ToolError):
        agent.pop_pending(data["confirm_token"])


async def test_delete_by_ordinal_needs_confirmation():
    agent = _agent()
    c = await TOOL_REGISTRY["tasks"].execute(action="create", title="phase5-ordinal-x")
    engine = WorkflowEngine()
    run = engine.create_run("delete task 99999")
    await agent.run("delete task 99999", run, engine)
    # No match -> honest failure, nothing pending stored for it.
    assert engine.get_run(run.workflow_id).status == "failed"
    await TOOL_REGISTRY["tasks"].execute(action="delete", id=c["task"]["id"])


async def test_bad_and_expired_tokens_rejected():
    agent = _agent()
    with pytest.raises(ToolError):
        agent.pop_pending("no-such-token")
    agent._pending["stale"] = {"expires_at": time.time() - 1}
    with pytest.raises(ToolError):
        agent.pop_pending("stale")
    assert "stale" not in agent._pending


async def test_low_write_actions_skip_confirmation():
    agent = _agent()
    engine = WorkflowEngine()
    run = engine.create_run("create a task phase5-auto-create")
    resp = await agent.run("create a task phase5-auto-create", run, engine)
    assert engine.get_run(run.workflow_id).status == "completed"
    assert "phase5-auto-create" in resp
    out = await TOOL_REGISTRY["tasks"].execute(action="list")
    mine = [t for t in out["tasks"] if t["title"] == "phase5-auto-create"]
    assert mine
    await TOOL_REGISTRY["tasks"].execute(action="delete", id=mine[0]["id"])


async def test_reminder_delete_needs_confirmation():
    agent = _agent()
    created = await TOOL_REGISTRY["reminders"].execute(
        action="create", title="phase5-reminder-target"
    )
    engine = WorkflowEngine()
    run = engine.create_run("delete reminder phase5-reminder-target")
    payload = await agent.run("delete reminder phase5-reminder-target", run, engine)
    assert engine.get_run(run.workflow_id).status == "awaiting_confirmation"
    data = json.loads(payload)
    assert data["needs_confirmation"] is True
    pending = agent.pop_pending(data["confirm_token"])
    run2 = engine.create_run("confirmed")
    await agent.run_confirmed(pending, run2, engine)
    with pytest.raises(ToolError):
        await TOOL_REGISTRY["reminders"].execute(action="get", id=created["reminder"]["id"])


# --- Classifier intents (Phase 5) --------------------------------------------


async def test_phase5_intents():
    provider = RuleBasedProvider()
    cases = {
        "remind me to call mom tomorrow": ("reminder_create", "reminders"),
        "remind me to stretch at 5pm": ("reminder_create", "reminders"),
        "list my reminders": ("reminder_list", "reminders"),
        "delete task 1": ("task_delete", "tasks"),
        "complete task 2": ("task_complete", "tasks"),
        "search the web for quantum computing": ("web_search", "search"),
        "find information about black holes": ("web_search", "search"),
        "who is Ashutosh Dhagat": ("web_search", "search"),
        "who was Albert Einstein": ("web_search", "search"),
        "research quantum computing": ("deep_research", "research"),
        "deep dive into AI safety": ("deep_research", "research"),
        "brief me": ("briefing", "briefing"),
        "morning briefing": ("briefing", "briefing"),
        "create a document titled Notes with content hello": (
            "document_create",
            "documents",
        ),
        "explain this code: def f(): pass": ("code_explain", "code"),
        "what does this code do": ("code_explain", "code"),
        # "search my documents" must stay RAG, not web search:
        "search my documents for the refund policy": ("knowledge_search", "rag"),
    }
    for text, (intent, tool) in cases.items():
        c = await provider.aclassify_intent(text)
        assert c["intent"] == intent, text
        assert tool in c["tools"], text
