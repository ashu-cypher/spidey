"""MEW LLM brain tests (user request 2026-10-09).

Covers: system-prompt building, lenient {"tool","args"} decision parsing
(valid / prose-wrapped / invalid / unknown tool), tool dispatch to the
registry, think() orchestration (tool round-trip, unparseable-decision as
final answer, provider-error propagation), brain eligibility on the agent
(deterministic intents and rule_based never touch the brain), and MEW_DEBUG
step recording. All existing deterministic behavior stays intact.
"""
import pytest

from app.agents.brain import MEWBrain, parse_tool_decision
from app.agents.spidey_agent import (
    _BRAIN_INTENTS,
    _DETERMINISTIC_INTENTS,
    SpideyAgent,
)
from app.config import settings
from app.providers.base import AIProvider, ProviderError
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.tools.base import BaseTool, ToolError
from app.workflows.engine import WorkflowEngine


class FakeLLMProvider(AIProvider):
    """Scripted LLM stand-in. ``name = "ollama"`` so the agent treats it as
    a configured LLM; ``script`` is the queue of agenerate() replies."""

    name = "ollama"

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def aclassify_intent(self, text, history=None):
        return {"intent": "chat_fallback", "requires_memory": False,
                "requires_tools": False, "tools": [], "response_mode": "chat"}

    async def agenerate(self, text, context="", history=None, lang=None,
                        system_prompt=None):
        self.calls.append({
            "text": text, "context": context, "history": history, "lang": lang,
            "system_prompt": system_prompt,
        })
        if not self.script:
            raise ProviderError("script exhausted")
        reply = self.script.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ExplodingLLMProvider(FakeLLMProvider):
    async def agenerate(self, text, context="", history=None, lang=None,
                        system_prompt=None):
        raise ProviderError("boom")


class StubTool(BaseTool):
    name = "stub"
    description = "stub"
    input_schema = {}
    output_schema = {}

    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    async def _run(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class StubAgent:
    def __init__(self, tools):
        self.tools = tools


# -- build_system_prompt -------------------------------------------------


def test_system_prompt_carries_all_blocks():
    brain = MEWBrain()
    prompt = brain.build_system_prompt(
        history=[{"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "hey"}],
        attachments="[Attachment: sem5.pdf (document)]\nDBMS unit 1 notes",
        syllabus_data=[{"name": "Database Management Systems",
                        "code": "310241", "units": 6,
                        "exam_types": ["Insem", "Endsem"]}],
        mems=[{"content": "User likes concise answers"}],
        tasks=[{"title": "Revise DBMS", "due": "tomorrow"}],
        lang="en",
    )
    assert "You are MEW" in prompt
    assert "J.A.R.V.I.S." in prompt  # identity guard present
    assert "GROUNDING RULES" in prompt
    assert "DOCUMENT-FIRST PRIORITY" in prompt
    assert "ACTIVE DOCUMENTS" in prompt and "DBMS unit 1 notes" in prompt
    assert "Database Management Systems" in prompt and "310241" in prompt
    assert "User likes concise answers" in prompt
    assert "Revise DBMS" in prompt
    assert "document_search" in prompt and "web_search" in prompt
    assert "calculator" in prompt and "memory_search" in prompt
    assert "User: hi" in prompt and "MEW: hey" in prompt


def test_system_prompt_language_and_truncation():
    brain = MEWBrain()
    prompt = brain.build_system_prompt(lang="hi")
    assert "Devanagari" in prompt
    long_attach = "x" * 20000
    prompt = brain.build_system_prompt(attachments=long_attach)
    # 8k budget: the block is bounded, not the whole 20k.
    assert len(prompt) < 20000
    assert "x" * 8000 in prompt


# -- parse_tool_decision -------------------------------------------------


def test_parse_valid_json():
    d = parse_tool_decision('{"tool": "web_search", "args": {"query": "AI news"}}')
    assert d == {"tool": "web_search", "args": {"query": "AI news"}}


def test_parse_prose_wrapped_json():
    d = parse_tool_decision(
        'Sure, I\'ll search. {"tool": "document_search", "args": {"query": "sem 5"}} done.'
    )
    assert d == {"tool": "document_search", "args": {"query": "sem 5"}}


def test_parse_invalid_is_none():
    assert parse_tool_decision("Just a normal answer, no JSON here.") is None
    assert parse_tool_decision('{"tool": ') is None
    assert parse_tool_decision("") is None
    assert parse_tool_decision(None) is None


def test_parse_unknown_tool_is_none():
    assert parse_tool_decision('{"tool": "send_email", "args": {}}') is None
    # Tasks stay deterministic — never a brain tool.
    assert parse_tool_decision('{"tool": "tasks", "args": {}}') is None


def test_parse_missing_args_defaults():
    d = parse_tool_decision('{"tool": "calculator"}')
    assert d == {"tool": "calculator", "args": {}}


# -- execute_tool --------------------------------------------------------


async def test_execute_tool_dispatches_to_registry():
    brain = MEWBrain()
    calc = StubTool({"expression": "2+2", "result": 4})
    agent = StubAgent({"calculator": calc})
    out = await brain.execute_tool("calculator", {"text": "2+2"}, agent)
    assert out["tool"] == "calculator"
    assert out["result"]["result"] == 4
    assert calc.calls == [{"text": "2+2"}]


async def test_execute_tool_document_search_maps_to_rag():
    brain = MEWBrain()
    rag = StubTool({"results": [{"text": "DBMS is...", "source": "sem5.pdf"}]})
    agent = StubAgent({"rag": rag})
    out = await brain.execute_tool("document_search", {"query": "DBMS"}, agent)
    assert out["tool"] == "document_search"
    assert rag.calls[0]["action"] == "search"
    assert rag.calls[0]["query"] == "DBMS"


async def test_execute_tool_failure_degrades_to_error_dict():
    brain = MEWBrain()
    bad = StubTool(ToolError("Web search is unavailable right now."))
    agent = StubAgent({"search": bad})
    out = await brain.execute_tool("web_search", {"query": "x"}, agent)
    assert out["tool"] == "web_search"
    assert out["error"] == "Web search is unavailable right now."


async def test_execute_tool_unknown_name():
    brain = MEWBrain()
    out = await brain.execute_tool("teleport", {}, StubAgent({}))
    assert "error" in out


# -- think() orchestration -----------------------------------------------


async def test_think_tool_round_trip():
    brain = MEWBrain()
    provider = FakeLLMProvider([
        '{"tool": "calculator", "args": {"text": "12 * 8"}}',
        "12 * 8 = 96.",
    ])
    calc = StubTool({"expression": "12 * 8", "result": 96})
    agent = StubAgent({"calculator": calc})
    final, debug = await brain.think(
        agent=agent, provider=provider, message="what is 12 * 8?",
        history=[], lang="en",
    )
    assert final == "12 * 8 = 96."
    # Decision call then final call — exactly two model calls.
    assert len(provider.calls) == 2
    # Both calls carried the brain's composed system prompt.
    assert all(c["system_prompt"] and "MEW" in c["system_prompt"]
               for c in provider.calls)
    # Grounding: the final call's context carries the provenance label.
    assert "[source: calculator]" in provider.calls[1]["context"]
    assert "96" in provider.calls[1]["context"]
    stages = [s["stage"] for s in debug["steps"]]
    assert "decision_parsed" in stages
    assert "tool_ok" in stages
    assert "final" in stages


async def test_think_unparseable_decision_is_final_answer():
    brain = MEWBrain()
    provider = FakeLLMProvider(["Machine learning is a field of AI..."])
    agent = StubAgent({})
    final, debug = await brain.think(
        agent=agent, provider=provider, message="what is machine learning?",
        history=[], lang="en",
    )
    # Unparseable -> the decision text IS the answer: single model call.
    assert final == "Machine learning is a field of AI..."
    assert len(provider.calls) == 1


async def test_think_provider_error_propagates():
    brain = MEWBrain()
    provider = ExplodingLLMProvider([])
    with pytest.raises(ProviderError):
        await brain.think(
            agent=StubAgent({}), provider=provider, message="hi", history=[],
        )


async def test_build_grounded_context_error_is_honest():
    brain = MEWBrain()
    ctx = brain.build_grounded_context(
        {"tool": "web_search", "args": {}},
        {"tool": "web_search", "error": "Web search is unavailable right now."},
    )
    assert "[source: web search]" in ctx
    assert "unavailable" in ctx


# -- agent eligibility ---------------------------------------------------


def test_deterministic_intents_never_brain():
    for intent in ("calculate", "task_create", "reminder_create", "remember",
                   "recall_memory", "greeting", "correction", "who_am_i",
                   "memory_forget_all", "attachment_delete"):
        assert intent in _DETERMINISTIC_INTENTS
        assert intent not in _BRAIN_INTENTS


def test_brain_intents_cover_conversation():
    for intent in ("chat_fallback", "chat_followup", "document_qa",
                   "summarize_document", "web_search", "news", "syllabus_query"):
        assert intent in _BRAIN_INTENTS
        assert intent not in _DETERMINISTIC_INTENTS


def test_specialized_pipelines_stay_off_brain():
    # Resume analysis, file generation, research plans and confirmations keep
    # the existing path even with an LLM configured.
    for intent in ("resume_analyze", "generate_file", "research_and_generate",
                   "attachment_delete"):
        assert intent not in _BRAIN_INTENTS


def test_brain_eligible_rule_based_never():
    agent = SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)
    assert not agent._brain_eligible("chat_fallback")
    assert not agent._brain_eligible("calculate")


def test_brain_eligible_llm_chat_fallback():
    agent = SpideyAgent(FakeLLMProvider([]), TOOL_REGISTRY)
    assert agent._brain_eligible("chat_fallback")
    assert agent._brain_eligible("document_qa")
    assert not agent._brain_eligible("calculate")  # deterministic stays local
    assert not agent._brain_eligible("generate_file")  # pipeline stays put


# -- end-to-end through agent.run ----------------------------------------


async def test_run_chat_fallback_uses_brain_with_llm():
    provider = FakeLLMProvider(["Machine learning is a field of AI."])
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("what is machine learning?")
    resp = await agent.run("what is machine learning?", run, engine)
    assert resp == "Machine learning is a field of AI."
    assert len(provider.calls) == 1
    assert provider.calls[0]["system_prompt"] is not None
    assert engine.get_run(run.workflow_id).status == "completed"


async def test_run_deterministic_intent_skips_brain_with_llm():
    provider = FakeLLMProvider(["should never be called"])
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("calculate 12 * 8")
    resp = await agent.run("calculate 12 * 8", run, engine)
    assert "96" in resp
    # The brain (and its LLM call) was never consulted.
    assert provider.calls == []


async def test_run_brain_provider_error_falls_back():
    provider = ExplodingLLMProvider([])
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("what is machine learning?")
    resp = await agent.run("what is machine learning?", run, engine)
    # Brain failed -> deterministic fallback answered (local rule-based),
    # not a crash and not the generic failure reply.
    assert isinstance(resp, str) and resp.strip()
    assert resp != "Spidey couldn't complete that step."
    assert engine.get_run(run.workflow_id).status == "completed"


async def test_run_brain_tool_round_trip_end_to_end():
    provider = FakeLLMProvider([
        '{"tool": "memory_search", "args": {"query": "favorite language"}}',
        "Your favorite language is Python, per your memory.",
    ])
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("what is my favorite language?")
    resp = await agent.run("what is my favorite language?", run, engine)
    assert "Python" in resp
    assert len(provider.calls) == 2


async def test_run_stream_brain_streams_deltas():
    provider = FakeLLMProvider(["Streaming brain reply here."])
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("what is machine learning?")
    events: list[tuple] = []

    async def emit(event_type, data):
        events.append((event_type, data))

    resp = await agent.run_stream("what is machine learning?", run, engine, emit=emit)
    assert resp == "Streaming brain reply here."
    kinds = [e for e, _ in events]
    assert "delta" in kinds
    assert kinds[-1] == "done"


# -- MEW_DEBUG -----------------------------------------------------------


async def test_mew_debug_records_brain_steps():
    provider = FakeLLMProvider(["Just chatting."])
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("what is machine learning?")
    await agent.run("what is machine learning?", run, engine)
    dbg = agent.get_debug_info(run.workflow_id)
    assert "brain" in dbg
    stages = [s["stage"] for s in dbg["brain"]["steps"]]
    assert "system_prompt" in stages
    assert "decision_parsed" in stages


def test_mew_debug_setting_exists():
    assert settings.mew_debug is False  # opt-in via MEW_DEBUG=1
