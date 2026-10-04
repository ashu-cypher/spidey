"""MEW 2.0 backend tests: command center, goals, research vault,
action history + undo, and smart notification keyword matching."""
import uuid

import pytest

from app.agents.spidey_agent import _VERIFY_KEYS
from app.main import task_matches_reminder
from app.providers.rule_based import RuleBasedProvider
from app.services import action_log
from app.tools.command_center import CommandCenterTool
from app.tools.goals import GoalsTool
from app.tools.history import HistoryTool
from app.tools.research import ResearchTool
from app.tools.tasks import TaskTool

provider = RuleBasedProvider()


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------- command center
async def test_command_center_empty(monkeypatch):
    async def _no_tasks(self, **kwargs):
        return {"tasks": []}

    async def _no_reminders(self, **kwargs):
        return {"reminders": []}

    async def _no_topics(self, **kwargs):
        return {"topics": []}

    monkeypatch.setattr(TaskTool, "execute", _no_tasks)
    from app.tools.reminders import ReminderTool
    from app.tools.learning import LearningTool

    monkeypatch.setattr(ReminderTool, "execute", _no_reminders)
    monkeypatch.setattr(LearningTool, "execute", _no_topics)
    from app.services import knowledge_graph

    monkeypatch.setattr(knowledge_graph, "find_entities", lambda *a, **k: [])
    out = await CommandCenterTool().execute()
    assert "priorities" in out
    assert "don't have any tasks" in out["priorities"]


async def test_command_center_with_tasks():
    out = await TaskTool().execute(action="create", title=f"CC probe task {_uid()}")
    tid = out["task"]["id"]
    try:
        res = await CommandCenterTool().execute()
        assert "CC probe task" in res["priorities"]
        assert "Up next" in res["priorities"]
    finally:
        await TaskTool().execute(action="delete", id=tid)


# ---------------------------------------------------------------- goals
async def test_goals_create_and_list():
    name = f"become better at AI development {_uid()}"
    created = await GoalsTool().execute(action="create", goal=name)
    goal = created["goal"]
    assert name in goal["goal"]
    # Milestones grounded in the goal text (3-4 of them).
    assert 3 <= len(goal["milestones"]) <= 4
    blob = " ".join(goal["milestones"]).lower()
    assert "ai development" in blob

    listed = await GoalsTool().execute(action="list")
    assert any(name in g["goal"] for g in listed["goals"])


async def test_goals_progress_and_report():
    name = f"learn the piano {_uid()}"
    created = await GoalsTool().execute(action="create", goal=name)
    assert created["goal"]["milestones"]

    updated = await GoalsTool().execute(
        action="progress", goal=name, milestone="1", done=True
    )
    assert updated["updated"]["completed"] == 1
    assert updated["updated"]["total"] >= 3

    report = await GoalsTool().execute(action="report")
    entry = next(g for g in report["progress"]["goals"] if name in g["goal"])
    assert entry["completed"] == 1
    assert entry["next_milestone"] is not None


async def test_goals_suggest_next_empty_ok():
    # No goals at all under a fresh name: honest suggestion to create one.
    res = await GoalsTool().execute(action="suggest_next")
    s = res["suggestion"]
    # Either points at a real goal or honestly says there are none.
    assert s.get("next") or "don't have any goals" in s.get("message", "")


# ---------------------------------------------------------------- research vault
async def test_research_vault_save_and_recall():
    topic = f"quantum batteries probe {_uid()}"
    tool = ResearchTool()
    await tool._save_to_vault(topic, "A detailed report about " + topic + " " * 500, 3)
    entries = await tool._recall_vault(topic)
    assert entries, "vault entry not recalled"
    match = next((e for e in entries if topic in e["topic"]), None)
    assert match is not None
    assert match["sources"] == 3
    assert match["date"]  # real date string


async def test_research_vault_recall_no_match():
    entries = await ResearchTool()._recall_vault(f"zz-nonexistent-{_uid()}")
    assert entries == []


# ---------------------------------------------------------------- action log + history
async def test_action_log_append_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(action_log, "_LOG_PATH", tmp_path / "test_log.jsonl")
    action_log.log_action("task_created", "Created task: probe", undo={"type": "x"})
    action_log.log_action("research_done", "Researched: probes", undo=None)
    actions = action_log.read_actions(limit=10)
    assert len(actions) == 2
    assert actions[0]["action"] == "research_done"  # newest first
    assert action_log.last_undoable()["action"] == "task_created"


async def test_history_undo_task(tmp_path, monkeypatch):
    monkeypatch.setattr(action_log, "_LOG_PATH", tmp_path / "test_log.jsonl")
    created = await TaskTool().execute(action="create", title=f"undo probe {_uid()}")
    tid = created["task"]["id"]
    action_log.log_action(
        "task_created",
        f"Created task: undo probe",
        undo={"type": "delete_task", "id": tid},
    )
    out = await HistoryTool().execute(action="undo")
    assert out["undone"]["ok"] is True
    remaining = await TaskTool().execute(action="list")
    assert all(t["id"] != tid for t in remaining["tasks"])


async def test_history_undo_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(action_log, "_LOG_PATH", tmp_path / "empty_log.jsonl")
    out = await HistoryTool().execute(action="undo")
    assert out["undone"]["ok"] is False
    assert "nothing to undo" in out["undone"]["message"].lower()


async def test_history_list(tmp_path, monkeypatch):
    monkeypatch.setattr(action_log, "_LOG_PATH", tmp_path / "test_log2.jsonl")
    action_log.log_action("goal_created", "Created goal: probe", undo=None)
    out = await HistoryTool().execute(action="list", limit=5)
    assert "actions" in out
    assert any(a["action"] == "goal_created" for a in out["actions"])


# ---------------------------------------------------------------- keyword matching
def test_task_matches_reminder_true():
    assert task_matches_reminder(
        "Finish the assignment draft", "Submit the assignment draft tomorrow"
    )


def test_task_matches_reminder_case_insensitive():
    assert task_matches_reminder("BOOK FLIGHT TICKETS", "book flight seats")


def test_task_matches_reminder_false():
    assert not task_matches_reminder("Buy groceries", "Call mom about the car")
    # Only one significant word in common -> not a match.
    assert not task_matches_reminder("Buy milk", "Milk delivery scheduled")
    # Short words (<=3 chars) don't count.
    assert not task_matches_reminder("Pay the tax", "Pay the fee")


# ---------------------------------------------------------------- router + verify
@pytest.mark.parametrize(
    "text,intent,tool",
    [
        ("what should I work on", "command_center", "command_center"),
        ("what is most urgent right now", "command_center", "command_center"),
        ("what did I leave unfinished", "command_center", "command_center"),
        ("I want to become better at AI development", "goals", "goals"),
        ("my goal is to learn piano", "goals", "goals"),
        ("how am I progressing", "goals", "goals"),
        ("what should I learn next", "goals", "goals"),
        ("show my goals", "goals", "goals"),
        ("what did I research about RAG", "research_recall", "research"),
        ("what did I find about batteries", "research_recall", "research"),
        ("show my research on agents", "research_recall", "research"),
        ("what did you change", "action_history", "history"),
        ("show action history", "action_history", "history"),
        ("undo that", "action_undo", "history"),
        ("undo the last action", "action_undo", "history"),
    ],
)
async def test_mew2_routing(text, intent, tool):
    c = await provider.aclassify_intent(text)
    assert c["intent"] == intent, text
    assert tool in c["tools"], text
    assert c["response_mode"] == "grounded"


async def test_verify_keys_mew2():
    assert _VERIFY_KEYS["command_center"] == ("priorities", "error")
    assert _VERIFY_KEYS["goals"] == ("goal", "goals", "progress", "suggestion", "error")
    assert "vault" in _VERIFY_KEYS["research"]
    assert _VERIFY_KEYS["history"] == ("actions", "undone", "error")
