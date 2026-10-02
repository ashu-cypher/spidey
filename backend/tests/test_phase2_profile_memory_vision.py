"""MEW Phase 2 — backend tests.

Covers:
 1. User profile store (spec 8): GET/PUT /api/profile, partial updates,
    "Who am I?" from the profile (honest unknown when empty), and
    "Remember that I prefer ..." captured as a profile preference.
 2. Memory forget (spec 7): forget action deletes the best match,
    "what do you remember about me?" lists, bulk wipe is
    confirmation-gated, classifier patterns.
 3. Conversational file management (spec 2): list / search / delete
    attachments via chat, delete confirmation-gated.
 4. Vision architecture (spec 18): capability detection (name heuristic +
    /api/show), the honest unsupported reply, the images array on the
    supported path, and vision_supported in /api/system/status.
 5. Alembic upgrade/downgrade for the two new revisions.
"""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.agents.spidey_agent import VISION_UNSUPPORTED_REPLY, SpideyAgent
from app.database import get_session
from app.main import create_app
from app.models import ConversationAttachment, Memory, UserProfile
from app.providers import manager
from app.providers.ollama import OllamaProvider, model_name_supports_vision
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.workflows.engine import WorkflowEngine

client = TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture
def agent():
    return SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)


@pytest.fixture(autouse=True)
def _clean_phase2_state():
    """Profile row + startup-probe state must never leak between tests."""
    with get_session() as session:
        session.execute(delete(UserProfile))
    manager._reset_startup_state()
    yield
    with get_session() as session:
        session.execute(delete(UserProfile))
    manager._reset_startup_state()


async def _run_chat(agent, message, conversation_id=None):
    engine = WorkflowEngine()
    run = engine.create_run(message)
    return await agent.run(
        message, run, engine, conversation_id=conversation_id
    )


def _attach_doc(conversation_id, filename, text, kind="document"):
    with get_session() as session:
        row = ConversationAttachment(
            conversation_id=conversation_id,
            filename=filename,
            kind=kind,
            extracted_text=text,
        )
        session.add(row)
        session.flush()
        return row.id


def _attach_image(conversation_id, filename="photo.png", data=b"\x89PNG fake"):
    with get_session() as session:
        row = ConversationAttachment(
            conversation_id=conversation_id,
            filename=filename,
            kind="image",
            extracted_text=f"[Image attachment: {filename}.]",
            data_base64=base64.b64encode(data).decode("ascii"),
        )
        session.add(row)
        session.flush()
        return row.id


# --- 1. User profile store ---------------------------------------------------


def test_profile_get_empty():
    resp = client.get("/api/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] is None
    assert body["college"] is None
    assert body["projects"] == []
    assert body["skills"] == []
    assert body["preferences"] == []


def test_profile_put_partial_update():
    resp = client.put(
        "/api/profile", json={"name": "Ashutosh Dhagat", "college": "Alard"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Ashutosh Dhagat"
    assert body["college"] == "Alard"
    assert body["education"] is None  # untouched fields stay unknown

    # Partial update: only the sent fields change.
    resp = client.put("/api/profile", json={"skills": ["Python", "FastAPI"]})
    body = resp.json()
    assert body["name"] == "Ashutosh Dhagat"
    assert body["skills"] == ["Python", "FastAPI"]

    # null clears a scalar.
    resp = client.put("/api/profile", json={"college": None})
    assert resp.json()["college"] is None
    assert resp.json()["name"] == "Ashutosh Dhagat"


async def test_who_am_i_empty_profile_is_honest(agent):
    resp = await _run_chat(agent, "Who am I?")
    assert "I don't have that information yet" in resp
    # Never invented: no name appears from nowhere.
    assert "Ashutosh" not in resp


async def test_who_am_i_answers_from_profile(agent):
    client.put(
        "/api/profile",
        json={"name": "Ashutosh Dhagat", "college": "Alard College"},
    )
    resp = await _run_chat(agent, "Who am I?")
    assert "Ashutosh Dhagat" in resp
    assert "Alard College" in resp


async def test_who_am_i_partial_profile(agent):
    client.put("/api/profile", json={"name": "Ashutosh Dhagat"})
    resp = await _run_chat(agent, "Who am I?")
    assert "Ashutosh Dhagat" in resp
    # Unknown fields are not invented — only the known name is stated.


async def test_remember_preference_stored_in_profile(agent):
    resp = await _run_chat(agent, "Remember that I prefer concise answers")
    assert "concise answers" in resp
    body = client.get("/api/profile").json()
    assert "concise answers" in body["preferences"]


async def test_remember_non_preference_does_not_touch_profile(agent):
    await _run_chat(agent, "remember that my current project is Spidey")
    body = client.get("/api/profile").json()
    assert body["preferences"] == []


# --- 2. Memory forget --------------------------------------------------------


def _classifier_intent(text):
    return RuleBasedProvider()._classify(text)["intent"]


@pytest.mark.parametrize(
    "text,intent",
    [
        ("forget that I prefer concise answers", "memory_forget"),
        ("forget my previous preference", "memory_forget"),
        ("what do you remember about me?", "memory_list"),
        ("forget everything", "memory_forget_all"),
        ("forget this document", "attachment_delete"),
        ("who am I?", "who_am_i"),
        ("what files have I uploaded?", "attachment_list"),
        ("find the paper where I mentioned RAG", "attachment_search"),
        ("delete notes.txt", "attachment_delete"),
    ],
)
def test_phase2_classifier_intents(text, intent):
    assert _classifier_intent(text) == intent


def test_dont_forget_is_not_a_deletion():
    # "don't forget ..." asks to REMEMBER — never a memory deletion.
    assert _classifier_intent("don't forget to call mom") != "memory_forget"


async def test_memory_forget_flow(agent):
    cid = "p2-forget-flow"
    await _run_chat(agent, "remember that my favorite color is p2blue")
    listed = await _run_chat(agent, "what do you remember about me?")
    assert "p2blue" in listed

    forgotten = await _run_chat(agent, "forget that my favorite color is p2blue")
    assert "Forgot" in forgotten
    assert "p2blue" in forgotten

    listed_again = await _run_chat(agent, "what do you remember about me?")
    assert "p2blue" not in listed_again


async def test_memory_forget_no_match_is_honest(agent):
    resp = await _run_chat(agent, "forget that the moon is made of p2cheese")
    assert "couldn't find" in resp


async def test_memory_forget_all_requires_confirmation(agent):
    await _run_chat(agent, "remember that p2bulk marker one")
    await _run_chat(agent, "remember that p2bulk marker two")

    resp = await _run_chat(agent, "forget everything")
    payload = json.loads(resp)
    assert payload["needs_confirmation"] is True
    assert "ALL" in payload["proposal"]
    token = payload["confirm_token"]

    # Nothing deleted before approval.
    with get_session() as session:
        remaining = session.execute(
            select(Memory).where(Memory.content.like("%p2bulk%"))
        ).scalars().all()
    assert len(remaining) == 2

    # Approving runs the wipe through the normal confirmed flow.
    pending = agent.pop_pending(token)
    engine = WorkflowEngine()
    run = engine.create_run("confirmed: wipe memories")

    done = await agent.run_confirmed(pending, run, engine)
    assert "Fresh start" in done

    with get_session() as session:
        remaining = session.execute(
            select(Memory).where(Memory.content.like("%p2bulk%"))
        ).scalars().all()
    assert remaining == []


async def test_memory_tool_forget_action_unit():
    tool = TOOL_REGISTRY["memory"]

    saved = await tool.execute(action="save", content="p2unit forget me please")
    assert "saved" in saved
    out = await tool.execute(action="forget", query="p2unit forget me please")
    assert out["forgot"]["content"] == "p2unit forget me please"
    listed = await tool.execute(action="list")
    assert all("p2unit" not in m["content"] for m in listed["memories"])


# --- 3. Conversational file management ---------------------------------------


async def test_attachment_list_via_chat(agent):
    cid = "p2-att-list"
    _attach_doc(cid, "notes.txt", "Project notes: something about RAG here.")
    resp = await _run_chat(agent, "What files have I uploaded?", conversation_id=cid)
    assert "notes.txt" in resp


async def test_attachment_list_empty_conversation(agent):
    resp = await _run_chat(
        agent, "What files have I uploaded?", conversation_id="p2-att-empty"
    )
    assert "No files" in resp


async def test_attachment_search_via_chat(agent):
    cid = "p2-att-search"
    _attach_doc(
        cid,
        "paper.txt",
        "In this paper we study RAG (Retrieval-Augmented Generation) for tutoring.",
    )
    _attach_doc(cid, "other.txt", "Grocery list: milk and eggs.")
    resp = await _run_chat(
        agent, "Find the paper where I mentioned RAG", conversation_id=cid
    )
    assert "paper.txt" in resp
    assert "other.txt" not in resp
    # A real snippet from the stored text travels with the hit.
    assert "Retrieval-Augmented" in resp


async def test_attachment_search_no_match(agent):
    cid = "p2-att-nomatch"
    _attach_doc(cid, "notes.txt", "Nothing relevant here.")
    resp = await _run_chat(
        agent, "Find the document where I mentioned p2zebra", conversation_id=cid
    )
    assert "didn't find" in resp


async def test_attachment_delete_requires_confirmation(agent):
    cid = "p2-att-del"
    _attach_doc(cid, "notes.txt", "Delete me via chat.")
    resp = await _run_chat(agent, "Delete notes.txt", conversation_id=cid)
    payload = json.loads(resp)
    assert payload["needs_confirmation"] is True
    assert "notes.txt" in payload["proposal"]

    # Still there before approval.
    with get_session() as session:
        rows = session.execute(
            select(ConversationAttachment).where(
                ConversationAttachment.conversation_id == cid
            )
        ).scalars().all()
    assert len(rows) == 1

    pending = agent.pop_pending(payload["confirm_token"])
    engine = WorkflowEngine()
    run = engine.create_run("confirmed: delete attachment")

    done = await agent.run_confirmed(pending, run, engine)
    assert "notes.txt" in done
    assert "Deleted" in done

    with get_session() as session:
        rows = session.execute(
            select(ConversationAttachment).where(
                ConversationAttachment.conversation_id == cid
            )
        ).scalars().all()
    assert rows == []


async def test_forget_this_document_resolves_attachment(agent):
    cid = "p2-att-forgetdoc"
    _attach_doc(cid, "report.pdf", "Quarterly report contents.")
    resp = await _run_chat(agent, "Forget this document", conversation_id=cid)
    payload = json.loads(resp)
    assert payload["needs_confirmation"] is True
    assert "report.pdf" in payload["proposal"]


async def test_attachment_tool_actions_unit():
    tool = TOOL_REGISTRY["attachments"]
    cid = "p2-att-unit"
    _attach_doc(cid, "a.txt", "alpha beta p2gamma")
    listed = await tool.execute(action="list", conversation_id=cid)
    assert [a["filename"] for a in listed["attachments"]] == ["a.txt"]
    assert listed["attachments"][0]["kind"] == "document"
    assert listed["attachments"][0]["created_at"]

    found = await tool.execute(
        action="search", conversation_id=cid, query="p2gamma"
    )
    assert len(found["matches"]) == 1
    assert found["matches"][0]["filename"] == "a.txt"
    assert "p2gamma" in found["matches"][0]["snippet"]

    missing = await tool.execute(
        action="search", conversation_id=cid, query="p2nothing"
    )
    assert missing["matches"] == []

    deleted = await tool.execute(action="delete", id=found["matches"][0]["id"])
    assert deleted["filename"] == "a.txt"
    listed2 = await tool.execute(action="list", conversation_id=cid)
    assert listed2["attachments"] == []
    # delete is confirmation-gated like other destructive actions.
    assert tool.permission_for("delete") == "confirm"
    assert tool.permission_for("list") == "read"


# --- 4. Vision architecture --------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("llava", True),
        ("llava:13b", True),
        ("moondream", True),
        ("qwen2-vl:7b", True),
        ("qwen2.5-vl", True),
        ("llama3.2-vision:11b", True),
        ("bakllava", True),
        ("qwen3:0.6b", False),
        ("llama3.1:8b", False),
        ("", False),
    ],
)
def test_vision_name_heuristic(model, expected):
    assert model_name_supports_vision(model) is expected


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _show_client(caps=None, exc=None):
    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            if exc is not None:
                raise exc
            return _FakeResp({"capabilities": caps or []})

    return _FakeClient


async def test_vision_supported_via_show_capabilities(monkeypatch):
    # Name heuristic misses, /api/show reports vision -> True.
    monkeypatch.setattr(httpx, "AsyncClient", _show_client(["vision"]))
    provider = OllamaProvider("http://127.0.0.1:11434", "custom-model")
    assert await provider.vision_supported() is True


async def test_vision_unsupported_via_show_capabilities(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _show_client([]))
    provider = OllamaProvider("http://127.0.0.1:11434", "custom-model")
    assert await provider.vision_supported() is False


async def test_vision_unreachable_is_honest_false(monkeypatch):
    monkeypatch.setattr(
        httpx, "AsyncClient", _show_client(exc=ConnectionError("down"))
    )
    provider = OllamaProvider("http://127.0.0.1:11434", "custom-model")
    assert await provider.vision_supported() is False


async def test_vision_result_cached(monkeypatch):
    calls = []

    class _CountingClient(_show_client(["vision"])):
        async def post(self, url, **kwargs):
            calls.append(url)
            return await super().post(url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _CountingClient)
    provider = OllamaProvider("http://127.0.0.1:11434", "custom-model")
    assert await provider.vision_supported() is True
    assert await provider.vision_supported() is True
    assert len(calls) == 1  # second call served from cache


async def test_image_ask_without_vision_is_honest(agent):
    # rule_based has no vision hook -> the exact spec'd honest reply.
    cid = "p2-vision-no"
    _attach_image(cid)
    resp = await _run_chat(agent, "What's in this image?", conversation_id=cid)
    assert resp == VISION_UNSUPPORTED_REPLY
    assert "Pull a vision model like llava" in resp


async def test_image_ask_what_is_variant(agent):
    # "What is in this image?" (uncontracted) hits the same vision gate.
    cid = "p2-vision-no2"
    _attach_image(cid)
    resp = await _run_chat(
        agent, "What is in this image?", conversation_id=cid
    )
    assert resp == VISION_UNSUPPORTED_REPLY


async def test_vision_supported_path_sends_images_array(monkeypatch):
    cid = "p2-vision-yes"
    _attach_image(cid, data=b"\x89PNG vision-bytes")
    expected_b64 = base64.b64encode(b"\x89PNG vision-bytes").decode("ascii")
    captured = {}

    class _GenClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            if url.endswith("/api/show"):
                return _FakeResp({"capabilities": ["vision"]})
            captured["payload"] = kwargs.get("json")
            return _FakeResp({"response": "I see a red square."})

    monkeypatch.setattr(httpx, "AsyncClient", _GenClient)
    provider = OllamaProvider("http://127.0.0.1:11434", "llava")
    vision_agent = SpideyAgent(provider, TOOL_REGISTRY)

    engine = WorkflowEngine()
    run = engine.create_run("What's in this image?")
    resp = await vision_agent.run(
        "What's in this image?", run, engine, conversation_id=cid
    )
    assert "images" in captured["payload"]
    assert captured["payload"]["images"] == [expected_b64]
    assert "red square" in resp


def test_system_status_reports_vision_supported():
    resp = client.get("/api/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "vision_supported" in body
    # rule_based (no vision hook) in the test env -> honestly False.
    assert body["vision_supported"] is False


# --- 5. Alembic revisions ----------------------------------------------------


def test_alembic_phase2_revisions_upgrade_and_downgrade(tmp_path, monkeypatch):
    """The two Phase 2 revisions upgrade AND downgrade cleanly.

    The scratch DB is stamped at the previous head (f3a8c7d2e1b4); the
    conversation_attachments table is created in its pre-revision shape
    first (stamp writes no DDL), then the upgrade/downgrade cycle exercises
    exactly the two new revisions.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    from app.config import settings

    db_path = tmp_path / "phase2_check.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    eng = create_engine(f"sqlite:///{db_path}")
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE conversation_attachments ("
                    "id VARCHAR(36) NOT NULL, "
                    "conversation_id VARCHAR(36) NOT NULL, "
                    "filename VARCHAR(512) NOT NULL, "
                    "kind VARCHAR(16) NOT NULL, "
                    "extracted_text TEXT NOT NULL, "
                    "created_at DATETIME NOT NULL, "
                    "PRIMARY KEY (id))"
                )
            )
        command.stamp(cfg, "f3a8c7d2e1b4")
        command.upgrade(cfg, "head")

        assert "user_profile" in inspect(eng).get_table_names()
        cols = {
            c["name"] for c in inspect(eng).get_columns("user_profile")
        }
        assert {
            "user_id",
            "name",
            "education",
            "college",
            "projects",
            "skills",
            "goals",
            "preferences",
            "updated_at",
        } <= cols
        att_cols = {
            c["name"]
            for c in inspect(eng).get_columns("conversation_attachments")
        }
        assert "data_base64" in att_cols

        command.downgrade(cfg, "-1")  # drops the data_base64 column
        att_cols = {
            c["name"]
            for c in inspect(eng).get_columns("conversation_attachments")
        }
        assert "data_base64" not in att_cols
        assert "user_profile" in inspect(eng).get_table_names()

        command.downgrade(cfg, "-1")  # drops user_profile
        assert "user_profile" not in inspect(eng).get_table_names()

        command.upgrade(cfg, "head")
        assert "user_profile" in inspect(eng).get_table_names()
        att_cols = {
            c["name"]
            for c in inspect(eng).get_columns("conversation_attachments")
        }
        assert "data_base64" in att_cols
    finally:
        eng.dispose()


def test_attach_image_size_cap():
    resp = client.post(
        "/api/chat/attach",
        files={
            "file": ("big.png", io.BytesIO(b"\x89PNG" + b"x" * (6 * 1024 * 1024)),
                     "image/png")
        },
        data={"conversation_id": "p2-img-cap"},
    )
    assert resp.status_code == 422
