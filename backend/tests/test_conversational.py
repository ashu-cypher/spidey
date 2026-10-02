"""Conversational-AI enhancement pass — backend tests.

Covers: relative reminder parsing ("in 20 minutes", "at 5pm" rollover),
new NL command intents + extraction, pronoun follow-up (chat_followup)
with/without history, conversation_recall with empty and non-empty
history, history plumbing through POST /api/chat (bounded to the last 10),
the GET /api/briefing shape, RAG attribution naming the source document,
the selective-memory contract, and the MEW personality reactions.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.spidey_agent import LOW_CONFIDENCE_REPLY, SpideyAgent
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.tools.reminders import extract_reminder_title, parse_reminder_at
from app.workflows.engine import WorkflowEngine


@pytest.fixture
def setup():
    provider = RuleBasedProvider()
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    return provider, agent


# --- 1. Relative reminder parsing -------------------------------------------


def _fixed_utc(monkeypatch, fixed: datetime):
    import app.tools.reminders as rem_mod

    monkeypatch.setattr(rem_mod, "_utcnow", lambda: fixed)


def test_parse_reminder_relative_durations(monkeypatch):
    _fixed_utc(monkeypatch, datetime(2026, 10, 2, 10, 0, 0))
    assert parse_reminder_at("remind me in 20 minutes to study") == datetime(
        2026, 10, 2, 10, 20, 0
    )
    assert parse_reminder_at("remind me in 2 hours to stretch") == datetime(
        2026, 10, 2, 12, 0, 0
    )
    assert parse_reminder_at("remind me in 3 days to review notes") == datetime(
        2026, 10, 5, 10, 0, 0
    )


def test_parse_reminder_at_5pm_today_or_tomorrow(monkeypatch):
    _fixed_utc(monkeypatch, datetime(2026, 10, 2, 10, 0, 0))
    # 5pm is still ahead today -> today.
    at = parse_reminder_at("remind me at 5pm to call mom")
    assert (at.day, at.hour, at.minute) == (2, 17, 0)
    # 8am already passed today -> tomorrow.
    at2 = parse_reminder_at("remind me at 8am to call mom")
    assert (at2.day, at2.hour, at2.minute) == (3, 8, 0)
    # Explicit "tomorrow" still wins.
    at3 = parse_reminder_at("remind me tomorrow at 8am to call mom")
    assert (at3.day, at3.hour, at3.minute) == (3, 8, 0)


def test_extract_reminder_title_relative_phrasing():
    assert extract_reminder_title("remind me in 20 minutes to study") == "study"
    assert extract_reminder_title("remind me at 5pm to call mom") == "call mom"
    assert extract_reminder_title("remind me in 3 days to review notes") == "review notes"
    # Bare time-only phrasing must not leave "remind me" as the title.
    assert extract_reminder_title("remind me at 5pm") == "Untitled reminder"


# --- 2. New NL command intents -----------------------------------------------


@pytest.mark.parametrize(
    "text,intent",
    [
        ("remind me in 20 minutes to study", "reminder_create"),
        ("remind me at 5pm to call mom", "reminder_create"),
        ("add finish my project to my tasks", "task_create"),
        ("mark my Python project complete", "task_complete"),
        ("search for the latest information about fusion", "web_search"),
        ("search latest about AI safety", "web_search"),
        # Already-covered phrasings, verified as part of this pass:
        ("remember that my current project is Spidey", "remember"),
        ("explain this code", "code_explain"),
        ("show my pending tasks", "task_list"),
    ],
)
async def test_nl_command_intents(setup, text, intent):
    provider, _ = setup
    c = await provider.aclassify_intent(text)
    assert c["intent"] == intent


def test_extract_task_add_to_my_tasks():
    assert (
        SpideyAgent._extract_task("add finish my project to my tasks")
        == "finish my project"
    )


def test_extract_ref_mark_complete_without_kind_word():
    assert (
        SpideyAgent._extract_ref("mark my Python project complete", "task")
        == "Python project"
    )
    # Existing kind-word phrasing still works.
    assert (
        SpideyAgent._extract_ref("complete the task buy milk", "task")
        == "buy milk"
    )


def test_extract_search_query_latest():
    assert (
        SpideyAgent._extract_search_query(
            "search for the latest information about fusion reactors"
        )
        == "latest information about fusion reactors"
    )
    assert (
        SpideyAgent._extract_search_query("search latest about AI safety")
        == "latest AI safety"
    )


# --- 3. Pronoun follow-up (chat_followup) ------------------------------------


async def test_chat_followup_with_history_names_topic(setup):
    provider, _ = setup
    history = [{"role": "user", "content": "What is RAG?"}]
    c = await provider.aclassify_intent("why would I use it?", history=history)
    assert c["intent"] == "chat_followup"
    assert c["topic"] == "RAG"
    c2 = await provider.aclassify_intent("tell me more about it", history=history)
    assert c2["intent"] == "chat_followup"
    c3 = await provider.aclassify_intent("how does that work?", history=history)
    assert c3["intent"] == "chat_followup"


async def test_chat_followup_reply_is_honest_and_names_topic(setup):
    provider, _ = setup
    history = [{"role": "user", "content": "What is RAG?"}]
    resp = await provider.agenerate("why would I use it?", history=history)
    assert "RAG" in resp
    assert "sir" in resp.lower()


async def test_chat_followup_without_topic_falls_through(setup):
    provider, _ = setup
    # No history at all -> must not invent an answer.
    c = await provider.aclassify_intent("why would I use it?")
    assert c["intent"] == "chat_fallback"
    c2 = await provider.aclassify_intent("tell me more")
    assert c2["intent"] == "chat_fallback"
    # Vacuous history (no user turn) -> also falls through.
    c3 = await provider.aclassify_intent(
        "why?", history=[{"role": "assistant", "content": "hi"}]
    )
    assert c3["intent"] == "chat_fallback"


async def test_chat_followup_end_to_end(setup):
    provider, agent = setup
    engine = WorkflowEngine()
    history = [{"role": "user", "content": "What is RAG?"}]
    run = engine.create_run("why would I use it?")
    resp = await agent.run("why would I use it?", run, engine, history=history)
    assert "RAG" in resp
    assert engine.get_run(run.workflow_id).status == "completed"


# --- 4. Conversation recall ---------------------------------------------------


async def test_conversation_recall_with_history(setup):
    provider, _ = setup
    history = [
        {"role": "user", "content": "What is RAG?"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "remind me to call mom"},
    ]
    c = await provider.aclassify_intent("what did we discuss earlier?", history=history)
    assert c["intent"] == "conversation_recall"
    resp = await provider.agenerate("what did we discuss earlier?", history=history)
    assert "RAG" in resp
    assert "call mom" in resp


async def test_conversation_recall_empty_history(setup):
    provider, _ = setup
    c = await provider.aclassify_intent("what were we talking about")
    assert c["intent"] == "conversation_recall"
    resp = await provider.agenerate("what were we talking about", history=[])
    assert resp == "We haven't discussed anything yet in this conversation, sir."
    resp2 = await provider.agenerate("summarize our conversation", history=None)
    assert resp2 == "We haven't discussed anything yet in this conversation, sir."


# --- 5. History plumbing through POST /api/chat -------------------------------


async def test_chat_route_bounds_history_to_last_10(monkeypatch):
    from app.routes import chat as chat_routes

    seen: dict = {}

    class _CaptureAgent:
        async def run(self, message, run, engine, history=None):
            seen["message"] = message
            seen["history"] = history
            return "ok"

    monkeypatch.setattr(chat_routes, "agent", _CaptureAgent())
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
        for i in range(15)
    ]
    # Malformed entries (wrong role, missing role) are filtered out.
    history.insert(0, {"role": "system", "content": "junk"})
    history.insert(3, {"content": "no role here"})
    resp = await chat_routes.post_chat(
        chat_routes.ChatRequest(message="hello?", history=history)
    )
    run_id = resp["run_id"]
    for _ in range(200):
        if chat_routes.engine.get_run(run_id).status != "running":
            break
        await asyncio.sleep(0.01)
    assert seen["message"] == "hello?"
    assert seen["history"] is not None
    assert len(seen["history"]) == 10
    assert [h["content"] for h in seen["history"]] == [
        f"turn {i}" for i in range(5, 15)
    ]
    assert all(h["role"] in ("user", "assistant") for h in seen["history"])


# --- 6. Briefing endpoint -----------------------------------------------------


async def test_briefing_shape():
    from app.routes import chat as chat_routes

    baseline = await chat_routes.briefing()
    base_pending = baseline["pending_tasks"]
    assert set(baseline.keys()) == {"pending_tasks", "due_soon"}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    created_task = await TOOL_REGISTRY["tasks"].execute(
        action="create", title="Briefing probe task"
    )
    soon = await TOOL_REGISTRY["reminders"].execute(
        action="create",
        title="Briefing probe reminder",
        remind_at=(now + timedelta(minutes=30)).isoformat(),
    )
    overdue = await TOOL_REGISTRY["reminders"].execute(
        action="create",
        title="Briefing probe overdue",
        remind_at=(now - timedelta(minutes=10)).isoformat(),
    )
    far = await TOOL_REGISTRY["reminders"].execute(
        action="create",
        title="Briefing probe far future",
        remind_at=(now + timedelta(hours=5)).isoformat(),
    )
    done_reminder = await TOOL_REGISTRY["reminders"].execute(
        action="create",
        title="Briefing probe done",
        remind_at=(now + timedelta(minutes=10)).isoformat(),
    )
    await TOOL_REGISTRY["reminders"].execute(
        action="complete", id=done_reminder["reminder"]["id"]
    )

    out = await chat_routes.briefing()
    assert out["pending_tasks"] == base_pending + 1
    texts = [d["text"] for d in out["due_soon"]]
    # Due soon + overdue are included...
    assert "Briefing probe reminder" in texts
    assert "Briefing probe overdue" in texts
    # ...far-future and completed reminders are not.
    assert "Briefing probe far future" not in texts
    assert "Briefing probe done" not in texts
    for entry in out["due_soon"]:
        assert set(entry.keys()) == {"text", "remind_at"}

    # Cleanup so later tests see a clean slate.
    await TOOL_REGISTRY["tasks"].execute(
        action="delete", id=created_task["task"]["id"]
    )
    for r in (soon, overdue, far, done_reminder):
        await TOOL_REGISTRY["reminders"].execute(
            action="delete", id=r["reminder"]["id"]
        )


# --- 7. RAG attribution -------------------------------------------------------


def test_rag_attribution_names_source_documents():
    facts = SpideyAgent._compose_rag_facts(
        {
            "rag": {
                "results": [
                    {
                        "source": "project.pdf",
                        "chunk_index": 0,
                        "content": "aaa",
                        "score": 0.9,
                    },
                    {
                        "source": "notes.md",
                        "chunk_index": 2,
                        "content": "bbb",
                        "score": 0.8,
                    },
                    {
                        "source": "project.pdf",
                        "chunk_index": 1,
                        "content": "ccc",
                        "score": 0.7,
                    },
                ]
            }
        }
    )
    # Distinct sources, backticked, first-seen order.
    assert facts.startswith("Based on your `project.pdf`, `notes.md`:")
    # Low-confidence path is untouched — never claims a doc source.
    assert (
        SpideyAgent._compose_rag_facts({"rag": {"results": []}})
        == LOW_CONFIDENCE_REPLY
    )


# --- 8. Selective memory contract ---------------------------------------------


async def test_memory_update_only_saves_for_remember_intent(setup):
    _, agent = setup
    saved = await agent._update_memory("remember", "remember that X")
    assert saved == {"saved": True, "reason": "already saved by memory tool"}
    for intent in ("task_create", "chat_fallback", "web_search", "greeting"):
        out = await agent._update_memory(intent, "some message")
        assert out["saved"] is False


async def test_memory_update_step_output_contract(setup):
    """Frontend reads the memory_update step output for its 'Memory updated'
    indicator: contract is {"saved": bool, ...}."""
    provider, agent = setup
    engine = WorkflowEngine()
    run = engine.create_run("remember that my current project is Spidey")
    resp = await agent.run(
        "remember that my current project is Spidey", run, engine
    )
    assert "Spidey" in resp
    finished = engine.get_run(run.workflow_id)
    mem_steps = [s for s in finished.steps if s.type == "memory_update"]
    assert mem_steps
    assert mem_steps[0].output.get("saved") is True


# --- 9. Personality reactions (concise, one short sentence) --------------------


async def test_task_create_personality(setup):
    _, agent = setup
    engine = WorkflowEngine()
    run = engine.create_run("create a task to buy milk")
    resp = await agent.run("create a task to buy milk", run, engine)
    assert "buy milk" in resp
    assert "Done, sir" in resp
    # Cleanup.
    listed = await TOOL_REGISTRY["tasks"].execute(action="list")
    for t in listed["tasks"]:
        if t["title"] == "buy milk":
            await TOOL_REGISTRY["tasks"].execute(action="delete", id=t["id"])


async def test_reminder_create_personality(setup):
    _, agent = setup
    engine = WorkflowEngine()
    run = engine.create_run("remind me in 20 minutes to study")
    resp = await agent.run("remind me in 20 minutes to study", run, engine)
    assert "study" in resp
    assert "Consider it handled, sir." in resp
    # Cleanup.
    listed = await TOOL_REGISTRY["reminders"].execute(action="list")
    for r in listed["reminders"]:
        if r["text"] == "study":
            await TOOL_REGISTRY["reminders"].execute(action="delete", id=r["id"])


async def test_task_complete_personality_and_resolution(setup):
    _, agent = setup
    created = await TOOL_REGISTRY["tasks"].execute(
        action="create", title="Python project"
    )
    engine = WorkflowEngine()
    run = engine.create_run("mark my Python project complete")
    resp = await agent.run("mark my Python project complete", run, engine)
    assert "Done, sir" in resp
    assert "Python project" in resp
    await TOOL_REGISTRY["tasks"].execute(
        action="delete", id=created["task"]["id"]
    )


def test_web_search_personality_prefix():
    facts = SpideyAgent._compose_facts(
        "web_search",
        "search the web for X",
        {
            "search": {
                "results": [{"title": "T", "url": "https://x", "snippet": "s"}]
            }
        },
        [],
    )
    assert facts.startswith("Found it, sir.")


# --- Smoke-fix tests: calculator gate, definition offer, recall hygiene,
#     cross-tool resolution -----------------------------------------------


async def test_what_is_rag_not_routed_to_calculator(setup):
    provider, _ = setup
    classified = await provider.aclassify_intent("What is RAG?")
    assert classified["intent"] != "calculate"
    reply = await provider.agenerate("What is RAG?")
    assert "RAG" in reply
    assert "search the web" in reply.lower()


async def test_calculate_still_requires_math(setup):
    provider, _ = setup
    assert (await provider.aclassify_intent("calculate 12 * 8"))["intent"] == "calculate"
    assert (await provider.aclassify_intent("what is 2+2"))["intent"] == "calculate"
    assert (await provider.aclassify_intent("what is twenty plus five"))["intent"] == "calculate"


async def test_conversation_recall_skips_followup_turns(setup):
    provider, _ = setup
    history = [
        {"role": "user", "content": "What is RAG?"},
        {"role": "assistant", "content": "RAG is Retrieval-Augmented Generation."},
        {"role": "user", "content": "Why would I use it?"},
        {"role": "assistant", "content": "On RAG, sir ..."},
    ]
    reply = await provider.agenerate("what did we discuss earlier?", history=history)
    assert "RAG" in reply
    assert "I use it" not in reply


async def test_cross_resolve_complete_reminder_via_task_phrasing(setup):
    _, agent = setup
    # Unique title + pre-clean so leftover dev-db rows can't pollute the run.
    for r in (await TOOL_REGISTRY["reminders"].execute(action="list"))["reminders"]:
        if r["text"] == "qx7-study":
            await TOOL_REGISTRY["reminders"].execute(action="delete", id=r["id"])
    created = await TOOL_REGISTRY["reminders"].execute(
        action="create", title="qx7-study", remind_at="2030-01-01T10:00:00"
    )
    try:
        engine = WorkflowEngine()
        run = engine.create_run("mark qx7-study complete")
        resp = await agent.run("mark qx7-study complete", run, engine)
        assert "Done, sir" in resp
        assert "qx7-study" in resp
        listed = await TOOL_REGISTRY["reminders"].execute(action="list")
        row = next(r for r in listed["reminders"] if r["id"] == created["reminder"]["id"])
        assert row["done"] is True
    finally:
        await TOOL_REGISTRY["reminders"].execute(
            action="delete", id=created["reminder"]["id"]
        )


async def test_resolution_failure_surfaces_helpful_message(setup):
    _, agent = setup
    engine = WorkflowEngine()
    # Explicit kind word -> no cross-tool wandering; the honest message wins.
    run = engine.create_run("finish task zzz-no-such-thing-xyz")
    resp = await agent.run("finish task zzz-no-such-thing-xyz", run, engine)
    assert "couldn't find a task" in resp
    assert resp != "Spidey couldn't complete that step."
