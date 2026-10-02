"""Streaming chat (SSE) + Hindi/Hinglish backend.

Covers: provider stream reassembly, voice_summary contract, language
detection, Hindi date parsing + subject extraction, the Hindi reminder E2E
through run_stream events, the /api/chat/stream SSE contract, and the
fast-path audit (exactly one generation call per request in every branch).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.agents.spidey_agent import SpideyAgent, make_voice_summary
from app.database import get_session
from app.models import Reminder
from app.providers.rule_based import RuleBasedProvider
from app.services.language import detect_language, split_sentences
from app.tools import TOOL_REGISTRY
from app.tools.reminders import extract_reminder_title, parse_reminder_at
from app.workflows.engine import WorkflowEngine


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- 1. Stream reassembly ----------------------------------------------------


async def test_stream_reassembly_equals_agenerate():
    provider = RuleBasedProvider()
    messages = [
        "Hello Spidey.",
        "What is RAG?",
        "calculate 12 * 8",
        "bhai kya haal hai",
        "नमस्ते",
    ]
    for msg in messages:
        full = await provider.agenerate(msg)
        chunks = [c async for c in provider.agenerate_stream(msg)]
        assert chunks, f"no chunks for {msg!r}"
        assert "".join(chunks) == full, f"reassembly mismatch for {msg!r}"
        # Sentence chunks keep their terminators.
        assert all(c.strip() for c in chunks)


async def test_stream_with_context_reassembles():
    provider = RuleBasedProvider()
    full = await provider.agenerate("x", context="Reminder set: foo (at soon). Done.")
    chunks = [c async for c in provider.agenerate_stream("x", context=full)]
    assert "".join(chunks) == full


async def test_stream_chunks_split_on_sentence_boundaries():
    provider = RuleBasedProvider()
    chunks = [
        c
        async for c in provider.agenerate_stream(
            "x", context="Pehla vakya. Dusra vakya! Teesra?"
        )
    ]
    assert len(chunks) == 3
    assert chunks[0].rstrip().endswith(".")
    assert chunks[1].rstrip().endswith("!")
    assert chunks[2].rstrip().endswith("?")


# --- 2. Voice summary contract ------------------------------------------------


def test_voice_summary_is_prefix_and_bounded():
    text = (
        "First sentence here. Second one too! Third is extra and long. "
        "Fourth follows."
    )
    summary = make_voice_summary(text)
    assert text.startswith(summary)
    assert len(split_sentences(summary)) <= 2
    assert "Third" not in summary


def test_voice_summary_short_text():
    summary = make_voice_summary("Ho gaya!")
    assert summary == "Ho gaya!"


def test_voice_summary_truncates_at_280_chars():
    text = " ".join(f"Sentence number {i} is here." for i in range(30))
    summary = make_voice_summary(text)
    assert len(summary) <= 280
    assert text.startswith(summary)


def test_voice_summary_hindi_danda():
    text = "नमस्ते। मैं स्पाइडी हूँ। तीसरा वाक्य यहाँ है।"
    summary = make_voice_summary(text, lang="hi")
    assert text.startswith(summary)
    assert len(split_sentences(summary)) <= 2
    assert "तीसरा" not in summary


def test_split_sentences_keeps_initialism_intact():
    # "J.A.R.V.I.S." must not be shredded into "J." "A." … — TTS would
    # speak the fragments literally.
    text = "At your service, sir. J.A.R.V.I.S. online and at your disposal."
    chunks = split_sentences(text)
    assert "".join(chunks) == text  # exact reassembly still holds
    assert chunks == [
        "At your service, sir. ",
        "J.A.R.V.I.S. online and at your disposal.",
    ]
    summary = make_voice_summary(text)
    assert "J.A.R.V.I.S." in summary
    assert not summary.rstrip().endswith("J.")


# --- 3. Language detection ----------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hello Spidey, how are you?", "en"),
        ("calculate 12 * 8", "en"),
        ("What is the capital of France?", "en"),
        ("The assignment is due tomorrow", "en"),  # 'assignment' is NOT a marker
        ("remind me to call mom tomorrow", "en"),
        ("Spidey kal mujhe 8 baje project yaad dila dena", "hinglish"),
        ("bhai kal 9 baje mujhe assignment yaad dila dena", "hinglish"),
        ("mere tasks kya hain", "hinglish"),
        ("mujhe madad chahiye", "hinglish"),
        ("kal kya kaam hai", "hinglish"),
        ("aaj kaam khatam karo", "hinglish"),
        ("mujhe yaad dila dena", "hinglish"),  # single highly-distinctive marker
        ("मुझे कल 9 बजे याद दिला देना", "hi"),
        ("नमस्ते! आप कैसे हैं?", "hi"),
    ],
)
def test_detect_language_samples(text, expected):
    assert detect_language(text) == expected


def test_detect_language_preferred_overrides():
    assert detect_language("मुझे कल याद दिला देना", "en") == "en"
    assert detect_language("hello", "hindi") == "hi"
    assert detect_language("hello", "hi") == "hi"
    assert detect_language("hello", "hinglish") == "hinglish"
    assert detect_language("", "auto") == "en"


# --- 4. Rule-based Hindi/Hinglish replies --------------------------------------


async def test_rule_based_hindi_greeting():
    provider = RuleBasedProvider()
    reply = await provider.agenerate("नमस्ते")
    assert "स्पाइडी" in reply


async def test_rule_based_hinglish_greeting():
    provider = RuleBasedProvider()
    c = await provider.aclassify_intent("namaste Spidey, mujhe madad chahiye")
    assert c["intent"] == "greeting"
    reply = await provider.agenerate("namaste Spidey, mujhe madad chahiye")
    assert "Main Spidey hoon" in reply


async def test_rule_based_hinglish_fallback_not_english():
    provider = RuleBasedProvider()
    reply = await provider.agenerate("bhai kya chal raha hai")
    assert "Samajh gaya" in reply
    assert "sir" not in reply.lower()


async def test_rule_based_hindi_classifier_intents():
    provider = RuleBasedProvider()
    assert (await provider.aclassify_intent("kal mujhe meeting yaad dila dena"))[
        "intent"
    ] == "reminder_create"
    assert (await provider.aclassify_intent("mere tasks dikhao"))["intent"] == "task_list"
    assert (await provider.aclassify_intent("ek kaam add karo"))["intent"] == "task_create"
    assert (await provider.aclassify_intent("yeh baat yaad rakhna"))["intent"] == "remember"
    assert (await provider.aclassify_intent("tumhe kya yaad hai"))["intent"] == "recall_memory"


# --- 5. Hindi date parsing -----------------------------------------------------


def test_parse_kal_with_baje():
    at = parse_reminder_at("kal 9 baje meeting yaad dila dena")
    expected = _utcnow() + timedelta(days=1)
    assert at.date() == expected.date()
    assert (at.hour, at.minute) == (9, 0)


def test_parse_kal_bare_defaults_to_9am():
    at = parse_reminder_at("kal yaad dila dena")
    expected = _utcnow() + timedelta(days=1)
    assert at.date() == expected.date()
    assert (at.hour, at.minute) == (9, 0)


def test_parse_parso():
    at = parse_reminder_at("parso doctor ke paas jana hai yaad dila dena")
    expected = _utcnow() + timedelta(days=2)
    assert at.date() == expected.date()
    assert (at.hour, at.minute) == (9, 0)


def test_parse_parso_with_baje():
    at = parse_reminder_at("parso 5 baje chai yaad dila dena")
    expected = _utcnow() + timedelta(days=2)
    assert at.date() == expected.date()
    assert (at.hour, at.minute) == (5, 0)


def test_parse_aaj_raat_defaults_to_20():
    at = parse_reminder_at("aaj raat movie yaad dila dena")
    now = _utcnow()
    assert at.date() == now.date()
    assert (at.hour, at.minute) == (20, 0)


def test_parse_aaj_raat_with_baje():
    at = parse_reminder_at("aaj raat 9 baje call yaad dila dena")
    now = _utcnow()
    assert at.date() == now.date()
    assert (at.hour, at.minute) == (21, 0)


def test_parse_bare_baje_rollover():
    at = parse_reminder_at("9 baje uthna hai yaad dila dena")
    now = _utcnow()
    today_nine = now.replace(hour=9, minute=0, second=0, microsecond=0)
    expected = today_nine if today_nine > now else today_nine + timedelta(days=1)
    assert at == expected


def test_parse_english_unchanged():
    at = parse_reminder_at("remind me to call mom tomorrow")
    expected = _utcnow() + timedelta(days=1)
    assert at.date() == expected.date()
    assert (at.hour, at.minute) == (9, 0)
    at2 = parse_reminder_at("remind me at 5pm to call mom")
    assert (at2.hour, at2.minute) == (17, 0)


def test_hindi_subject_extraction():
    assert (
        extract_reminder_title("bhai kal 9 baje mujhe assignment yaad dila dena")
        == "assignment"
    )
    assert extract_reminder_title("kal mujhe project yaad dila dena") == "project"
    assert extract_reminder_title("parso 5 baje mujhe chai yaad dilana") == "chai"


def test_english_subject_extraction_unchanged():
    assert extract_reminder_title("remind me to call mom tomorrow") == "call mom"


# --- 6. Hindi reminder E2E via run_stream ---------------------------------------


async def _collect(agent, message, **kwargs):
    events: list[tuple[str, dict]] = []
    engine = WorkflowEngine()
    run = engine.create_run(message)

    async def emit(event_type: str, data: dict):
        events.append((event_type, data))

    result = await agent.run_stream(message, run, engine, emit=emit, **kwargs)
    return result, events, engine, run


async def test_hindi_reminder_e2e_run_stream():
    agent = SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)
    message = "bhai kal 9 baje mujhe assignment yaad dila dena"
    result, events, engine, run = await _collect(agent, message)

    # Event contract: tool_start for reminders fired.
    tool_starts = [
        d for t, d in events if t == "state" and d.get("state") == "tool_start"
    ]
    assert any(d.get("tool") == "reminders" for d in tool_starts)
    tool_completes = [
        d for t, d in events if t == "state" and d.get("state") == "tool_complete"
    ]
    assert any(d.get("tool") == "reminders" for d in tool_completes)

    # voice_summary comes before any delta, and is a prefix of the result.
    types = [t for t, _ in events]
    assert types.index("voice_summary") < types.index("delta")
    summaries = [d["text"] for t, d in events if t == "voice_summary"]
    assert len(summaries) == 1
    assert result.startswith(summaries[0])

    # Deltas reassemble to the full result.
    deltas = "".join(d["text"] for t, d in events if t == "delta")
    assert deltas == result

    # Hinglish reply, not an English translation.
    assert "Ho gaya!" in result
    assert "assignment" in result
    assert "kal 9 baje yaad dila dunga" in result

    # done event carries the contract fields.
    done = [d for t, d in events if t == "done"]
    assert len(done) == 1
    assert done[0]["run_id"] == run.workflow_id
    assert done[0]["lang"] == "hinglish"
    assert done[0]["needs_confirmation"] is False
    assert done[0]["result"] == result

    # The reminder really landed for tomorrow 09:00 (server clock).
    expected_day = (_utcnow() + timedelta(days=1)).date()
    with get_session() as session:
        rows = list(
            session.scalars(
                select(Reminder).where(Reminder.text == "assignment")
            )
        )
    assert rows, "reminder 'assignment' was not created"
    remind_at = rows[-1].remind_at
    assert remind_at.date() == expected_day
    assert (remind_at.hour, remind_at.minute) == (9, 0)

    # Steps were recorded for /api/workflow/{run_id}.
    finished = engine.get_run(run.workflow_id)
    assert finished.status == "completed"
    names = [s.name for s in finished.steps]
    assert "Understand request" in names
    assert "Compose response" in names
    understand = next(s for s in finished.steps if s.name == "Understand request")
    assert "Fast path: reminder_create" in str(understand.output)


async def test_run_stream_direct_path_events():
    agent = SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)
    result, events, engine, run = await _collect(agent, "Hello Spidey.")
    assert "sir" in result.lower()
    types = [t for t, _ in events]
    assert types[0] == "state"
    assert events[0][1] == {"state": "thinking", "label": "Understanding request"}
    assert "voice_summary" in types and "delta" in types
    assert types[-1] == "done"
    done = [d for t, d in events if t == "done"][0]
    assert done["lang"] == "en"
    # No tool ran on the direct path.
    assert not [d for t, d in events if d.get("state") == "tool_start"]


async def test_run_stream_confirmation_gate():
    agent = SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)
    created = await TOOL_REGISTRY["tasks"].execute(
        action="create", title="stream confirm target"
    )
    title = created["task"]["title"]
    result, events, engine, run = await _collect(agent, f"delete task {title}")

    awaiting = [
        d for t, d in events if t == "state" and d.get("state") == "awaiting_confirmation"
    ]
    assert len(awaiting) == 1
    assert title in awaiting[0]["proposal"]
    done = [d for t, d in events if t == "done"][0]
    assert done["needs_confirmation"] is True
    assert done["confirm_token"]
    assert done["proposal"] == awaiting[0]["proposal"]

    # Confirm through the streaming path: same events, pre-resolved tool.
    pending = agent.pop_pending(done["confirm_token"])
    events2: list[tuple[str, dict]] = []
    engine2 = WorkflowEngine()
    run2 = engine2.create_run("confirmed")

    async def emit2(event_type: str, data: dict):
        events2.append((event_type, data))

    result2 = await agent.run_stream_confirmed(pending, run2, engine2, emit=emit2)
    assert "stream confirm target" in result2
    assert any(
        t == "state" and d.get("state") == "tool_start" and d.get("tool") == "tasks"
        for t, d in events2
    )
    done2 = [d for t, d in events2 if t == "done"][0]
    assert done2["needs_confirmation"] is False
    assert engine2.get_run(run2.workflow_id).status == "completed"


# --- 7. SSE endpoint contract ----------------------------------------------------


async def test_chat_stream_endpoint_sse_contract():
    from app.routes import chat as chat_routes

    resp = await chat_routes.post_chat_stream(
        chat_routes.ChatRequest(message="Hello Spidey.", lang="en")
    )
    assert resp.media_type == "text/event-stream"
    raw = "".join([chunk async for chunk in resp.body_iterator])
    frames = [f for f in raw.split("\n\n") if f.strip()]
    assert frames, "no SSE frames emitted"
    events = []
    for frame in frames:
        if frame.startswith(":"):
            continue  # keep-alive ping
        lines = frame.splitlines()
        assert lines[0].startswith("event: ")
        assert lines[1].startswith("data: ")
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    types = [t for t, _ in events]
    assert "state" in types
    assert "voice_summary" in types
    assert "delta" in types
    assert types[-1] == "done"
    done = events[-1][1]
    assert done["run_id"]
    assert done["lang"] == "en"
    assert "sir" in done["result"].lower()


async def test_chat_stream_endpoint_hindi_lang():
    from app.routes import chat as chat_routes

    resp = await chat_routes.post_chat_stream(
        chat_routes.ChatRequest(message="namaste Spidey, mujhe madad chahiye")
    )
    raw = "".join([chunk async for chunk in resp.body_iterator])
    # Find the done frame and check the detected language travelled through.
    frames = [f for f in raw.split("\n\n") if f.startswith("event: done")]
    assert frames
    done = json.loads(frames[0].splitlines()[1][6:])
    assert done["lang"] == "hinglish"
    assert "Main Spidey hoon" in done["result"]


# --- 8. Fast-path audit: exactly one generation call per request ------------------


class _CountingProvider(RuleBasedProvider):
    """Counts generation calls across run()/run_stream() branches."""

    def __init__(self):
        self.generate_calls = 0
        self.stream_calls = 0

    async def agenerate(self, text, context="", history=None, lang=None):
        self.generate_calls += 1
        return await super().agenerate(
            text, context=context, history=history, lang=lang
        )

    async def agenerate_stream(self, message, context="", history=None, lang=None):
        self.stream_calls += 1
        # Bypass the override when composing: RuleBasedProvider.agenerate_stream
        # calls self.agenerate() internally, which is the SAME single logical
        # generation — counting it again would defeat the audit.
        text = await RuleBasedProvider.agenerate(
            self, message, context=context, history=history, lang=lang
        )
        for chunk in split_sentences(text):
            yield chunk


def _counting_agent():
    provider = _CountingProvider()
    return provider, SpideyAgent(provider, TOOL_REGISTRY)


async def test_single_generation_direct_path():
    provider, agent = _counting_agent()
    engine = WorkflowEngine()
    run = engine.create_run("hi")
    await agent.run("Hello Spidey.", run, engine)
    assert provider.generate_calls == 1
    assert provider.stream_calls == 0


async def test_single_generation_tool_path():
    provider, agent = _counting_agent()
    engine = WorkflowEngine()
    run = engine.create_run("calc")
    resp = await agent.run("calculate 12 * 8", run, engine)
    assert "96" in resp
    assert provider.generate_calls == 1
    assert provider.stream_calls == 0


async def test_single_generation_confirm_path():
    provider, agent = _counting_agent()
    created = await TOOL_REGISTRY["tasks"].execute(
        action="create", title="audit confirm target"
    )
    title = created["task"]["title"]

    engine = WorkflowEngine()
    run = engine.create_run("del")
    payload = await agent.run(f"delete task {title}", run, engine)
    # The gate proposes without generating anything.
    assert provider.generate_calls == 0
    data = json.loads(payload)
    assert data["needs_confirmation"] is True

    pending = agent.pop_pending(data["confirm_token"])
    engine2 = WorkflowEngine()
    run2 = engine2.create_run("confirmed")
    await agent.run_confirmed(pending, run2, engine2)
    assert provider.generate_calls == 1
    assert provider.stream_calls == 0


async def test_single_generation_stream_paths():
    provider, agent = _counting_agent()

    events: list[tuple[str, dict]] = []

    async def emit(t, d):
        events.append((t, d))

    engine = WorkflowEngine()
    run = engine.create_run("s1")
    await agent.run_stream("Hello Spidey.", run, engine, emit=emit)
    assert provider.stream_calls == 1
    assert provider.generate_calls == 0

    engine = WorkflowEngine()
    run = engine.create_run("s2")
    await agent.run_stream("calculate 3 * 7", run, engine, emit=emit)
    assert provider.stream_calls == 2
    assert provider.generate_calls == 0

    # Streamed confirmation gate: no generation until confirmed.
    created = await TOOL_REGISTRY["tasks"].execute(
        action="create", title="audit stream confirm target"
    )
    engine = WorkflowEngine()
    run = engine.create_run("s3")
    await agent.run_stream(
        f"delete task {created['task']['title']}", run, engine, emit=emit
    )
    assert provider.stream_calls == 2  # gate only — still no generation
    done = [d for t, d in events if t == "done"][-1]
    pending = agent.pop_pending(done["confirm_token"])
    engine2 = WorkflowEngine()
    run2 = engine2.create_run("s4")
    await agent.run_stream_confirmed(pending, run2, engine2, emit=emit)
    assert provider.stream_calls == 3
    assert provider.generate_calls == 0


async def test_understand_step_records_fast_path():
    provider, agent = _counting_agent()
    engine = WorkflowEngine()
    run = engine.create_run("calc")
    await agent.run("calculate 12 * 8", run, engine)
    understand = next(
        s for s in engine.get_run(run.workflow_id).steps if s.name == "Understand request"
    )
    assert "Fast path: calculate" in str(understand.output)
