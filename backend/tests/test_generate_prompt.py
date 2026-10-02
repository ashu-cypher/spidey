"""MEW upgrade — generate_prompt intent + build/prompt/explain modes.

The classifier must distinguish:
  "build this for me" / "create a todo app for me" -> execute tools
  "give me a prompt (for X)" / "antigravity ke liye prompt bana do"
      -> generate_prompt
  "explain how to build X" / "kaise banau" -> explanation (chat path)
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.agents.spidey_agent import SpideyAgent
from app.main import create_app
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.workflows.engine import WorkflowEngine

client = TestClient(create_app(), raise_server_exceptions=False)

REQUIRED_SECTIONS = (
    "Goal",
    "Context",
    "Requirements",
    "Constraints",
    "Expected output",
)


@pytest.fixture
def provider():
    return RuleBasedProvider()


async def _intent(provider, text: str) -> dict:
    return await provider.aclassify_intent(text)


async def _run(message: str, conversation_id: str | None = None) -> str:
    agent = SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run(message)
    return await agent.run(
        message, run, engine, conversation_id=conversation_id
    )


# --- classifier: generate_prompt triggers ----------------------------------


async def test_generate_prompt_triggers_en(provider):
    for text in (
        "give me a prompt for Antigravity to build this",
        "make me a prompt to build a todo app",
        "write a prompt to build a landing page",
        "generate a prompt for building a chat app",
    ):
        c = await _intent(provider, text)
        assert c["intent"] == "generate_prompt", text
        assert c["tools"] == [] and not c["requires_tools"]


async def test_generate_prompt_triggers_hinglish(provider):
    for text in (
        "iske liye prompt bana do",
        "antigravity ke liye prompt bana do",
    ):
        c = await _intent(provider, text)
        assert c["intent"] == "generate_prompt", text


# --- classifier: the other two modes must NOT trigger it --------------------


async def test_build_mode_does_not_trigger_generate_prompt(provider):
    c = await _intent(provider, "build this for me")
    assert c["intent"] != "generate_prompt"
    # "create a todo app for me" keeps its existing behavior (task_create).
    c = await _intent(provider, "create a todo app for me")
    assert c["intent"] == "task_create"
    assert c["intent"] != "generate_prompt"


async def test_explain_mode_does_not_trigger_generate_prompt(provider):
    for text in (
        "explain how to build a todo app",
        "kaise banau",
        "how do I build a website",
    ):
        c = await _intent(provider, text)
        assert c["intent"] != "generate_prompt", text
        assert c["tools"] == [] and not c["requires_tools"]


# --- composed prompt --------------------------------------------------------


async def test_generate_prompt_has_required_sections():
    resp = await _run("give me a prompt for Antigravity to build a todo app")
    for section in REQUIRED_SECTIONS:
        assert section in resp, f"missing section: {section}"
    assert "Antigravity" in resp
    assert "todo app" in resp.lower()


async def test_generate_prompt_hinglish_has_required_sections():
    resp = await _run("iske liye prompt bana do")
    for section in REQUIRED_SECTIONS:
        assert section in resp, f"missing section: {section}"


async def test_generate_prompt_uses_attachment_context():
    client.post(
        "/api/chat/attach",
        files={
            "file": (
                "spec.txt",
                io.BytesIO(b"Build a habit tracker with streaks and reminders."),
                "text/plain",
            )
        },
        data={"conversation_id": "conv-prompt"},
    )
    resp = await _run(
        "give me a prompt for Antigravity to build this",
        conversation_id="conv-prompt",
    )
    for section in REQUIRED_SECTIONS:
        assert section in resp, f"missing section: {section}"
    # "this" is a bare pronoun — the attachment grounds the Context section.
    assert "habit tracker" in resp


async def test_generate_prompt_without_context_is_honest():
    resp = await _run("give me a prompt to build this")
    for section in REQUIRED_SECTIONS:
        assert section in resp, f"missing section: {section}"
    # No attachment and no real topic: no invented specifics.
    assert "No extra context was provided" in resp


async def test_generate_prompt_constraints_disclaim_invention():
    resp = await _run("make me a prompt to build a todo app")
    assert "never invent package names or APIs" in resp
