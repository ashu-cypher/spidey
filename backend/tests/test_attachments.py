"""MEW upgrade — conversation file attachments as chat context.

Covers: POST /api/chat/attach (metadata + row stored, kind
classification), attachment injection into agent.run/run_stream context
("explain this" resolves against the stored text, no re-upload), and the
Alembic revision upgrading + downgrading cleanly.
"""
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.spidey_agent import SpideyAgent
from app.database import get_session
from app.main import create_app
from app.models import ConversationAttachment, ResumeVersion
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.workflows.engine import WorkflowEngine

client = TestClient(create_app(), raise_server_exceptions=False)

RESUME_TEXT = """\
Ashutosh Dhagat
ashutosh@example.com | Pune, India

SUMMARY
Computer Engineering student focused on AI systems.

EDUCATION
B.E. Computer Engineering, Alard College of Engineering, 2028

EXPERIENCE
AI Intern — built a RAG chatbot used by 200 students.

SKILLS
Python, FastAPI, React, PostgreSQL
"""

DOC_TEXT = "Project notes: the reactor runs on clean arc energy. Next milestone Friday."


def _attach(filename: str, content: bytes, conversation_id: str = "conv-test"):
    return client.post(
        "/api/chat/attach",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        data={"conversation_id": conversation_id},
    )


def _stored(attachment_id: str) -> ConversationAttachment:
    with get_session() as session:
        row = session.execute(
            select(ConversationAttachment).where(
                ConversationAttachment.id == attachment_id
            )
        ).scalar_one()
        session.expunge(row)
        return row


# --- endpoint ------------------------------------------------------------


def test_attach_txt_stores_row_and_returns_metadata():
    resp = _attach("notes.txt", DOC_TEXT.encode(), conversation_id="conv-meta")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["conversation_id"] == "conv-meta"
    assert body["filename"] == "notes.txt"
    assert body["kind"] == "document"
    assert body["id"]
    assert body["created_at"]

    row = _stored(body["id"])
    assert row.conversation_id == "conv-meta"
    assert "arc energy" in row.extracted_text


def test_attach_classifies_resume_kind():
    resp = _attach("my-cv.txt", RESUME_TEXT.encode(), conversation_id="conv-resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "resume"


def test_attach_classifies_resume_by_content_not_just_name():
    # No "resume"/"cv" in the filename — the resume tool's section detector
    # (reused as a documented heuristic) still classifies it.
    resp = _attach(
        "background.txt", RESUME_TEXT.encode(), conversation_id="conv-resume2"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "resume"


def test_attach_image_stores_filename_only():
    resp = client.post(
        "/api/chat/attach",
        files={"file": ("photo.png", io.BytesIO(b"\x89PNG fake"), "image/png")},
        data={"conversation_id": "conv-img"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "image"
    row = _stored(body["id"])
    # Honest: no vision model — only the filename is meaningfully stored.
    assert "no vision model" in row.extracted_text
    assert "photo.png" in row.filename


def test_attach_rejects_unsupported_type():
    resp = client.post(
        "/api/chat/attach",
        files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        data={"conversation_id": "conv-bad"},
    )
    assert resp.status_code == 422


def test_attach_rejects_empty_file():
    resp = client.post(
        "/api/chat/attach",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        data={"conversation_id": "conv-bad"},
    )
    assert resp.status_code == 422


def test_attach_text_bounded_at_write_time():
    big = ("word " * 5000).encode()  # ~25k chars
    resp = _attach("big.txt", big, conversation_id="conv-big")
    assert resp.status_code == 200, resp.text
    row = _stored(resp.json()["id"])
    assert len(row.extracted_text) <= 8000


# --- attachment context in the agent ---------------------------------------


async def _run_agent(message: str, conversation_id: str | None) -> str:
    agent = SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run(message)
    return await agent.run(
        message, run, engine, conversation_id=conversation_id
    )


async def test_explain_this_resolves_against_attachment():
    _attach("notes.txt", DOC_TEXT.encode(), conversation_id="conv-ctx")
    resp = await _run_agent("explain this", "conv-ctx")
    # No re-upload: the stored text grounds the reply.
    assert "arc energy" in resp
    assert "[Attachment: notes.txt (document)]" in resp


async def test_most_recent_three_attachments_only():
    for i in range(4):
        _attach(
            f"n{i}.txt",
            f"unique-marker-{i}".encode(),
            conversation_id="conv-three",
        )
    resp = await _run_agent("summarize this", "conv-three")
    assert "unique-marker-3" in resp
    assert "unique-marker-0" not in resp  # oldest of the four is dropped


async def test_document_qa_without_attachment_is_honest():
    resp = await _run_agent("explain this", "conv-empty-never-used")
    assert "nothing attached" in resp.lower()


async def test_stream_includes_attachment_context():
    _attach("notes.txt", DOC_TEXT.encode(), conversation_id="conv-stream")
    agent = SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("explain this")
    events: list[tuple[str, dict]] = []

    async def emit(event_type: str, data: dict):
        events.append((event_type, data))

    result = await agent.run_stream(
        "explain this", run, engine, emit=emit, conversation_id="conv-stream"
    )
    assert "arc energy" in result
    assert any(t == "done" for t, _ in events)


# --- resume save/show intents ----------------------------------------------


async def _seed_resume_version(content: str, label: str = "v1 upload") -> dict:
    return await TOOL_REGISTRY["resume"].execute(
        action="create_version",
        content=content,
        label=label,
        source_filename="cv.txt",
        user_id="local",
    )


def _version_count() -> int:
    with get_session() as session:
        return len(
            session.execute(
                select(ResumeVersion).where(ResumeVersion.user_id == "local")
            )
            .scalars()
            .all()
        )


async def test_save_that_version_creates_a_version():
    await _seed_resume_version(RESUME_TEXT, label="v-seed")
    before = _version_count()
    resp = await _run_agent("save that version", None)
    assert _version_count() == before + 1
    assert "snapshot" in resp.lower() or "saved" in resp.lower()


async def test_save_that_version_copies_content_verbatim():
    # No-invention guard: the snapshot is byte-identical to the source.
    await _seed_resume_version(RESUME_TEXT, label="v-verbatim")
    await _run_agent("save that version", None)
    with get_session() as session:
        newest = (
            session.execute(
                select(ResumeVersion)
                .where(ResumeVersion.user_id == "local")
                .order_by(ResumeVersion.version_number.desc())
            )
            .scalars()
            .first()
        )
        assert newest is not None
        # Byte-identical modulo the tool's documented strip() of the stored
        # version: nothing invented, nothing added.
        assert newest.content == RESUME_TEXT.strip()
        assert "snapshot" in newest.label


async def test_save_that_version_with_no_cv_is_honest():
    from sqlalchemy import delete

    with get_session() as session:
        session.execute(delete(ResumeVersion))
    resp = await _run_agent("save that version", None)
    assert "don't have your cv" in resp.lower()


async def test_show_me_the_final_version_returns_latest():
    await _seed_resume_version(RESUME_TEXT, label="v-final-check")
    resp = await _run_agent("show me the final version", None)
    assert "arc energy" not in resp  # not the attachment text
    assert "Alard College of Engineering" in resp


# --- alembic upgrade + downgrade -------------------------------------------


def test_alembic_upgrade_and_downgrade_cleanly(tmp_path, monkeypatch):
    """The conversation_attachments revision upgrades AND downgrades cleanly.

    The scratch DB is stamped at the previous head first: older revisions
    carry postgres-specific DDL (pgvector ALTER COLUMN) that sqlite cannot
    replay — pre-existing and out of scope — so the upgrade/downgrade cycle
    exercises exactly the new revision.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.config import settings

    db_path = tmp_path / "alembic_check.db"
    monkeypatch.setattr(
        settings, "database_url", f"sqlite:///{db_path}"
    )
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    # Absolute script location: the ini's relative `script_location` only
    # resolves when pytest runs from backend/.
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    command.stamp(cfg, "e8f1a2b3c4d5")  # previous head; new revision is next
    command.upgrade(cfg, "head")
    eng = create_engine(f"sqlite:///{db_path}")
    try:
        assert "conversation_attachments" in inspect(eng).get_table_names()
        cols = {
            c["name"]
            for c in inspect(eng).get_columns("conversation_attachments")
        }
        assert {
            "id",
            "conversation_id",
            "filename",
            "kind",
            "extracted_text",
            "created_at",
        } <= cols
        idx_cols = [
            list(ix["column_names"])
            for ix in inspect(eng).get_indexes("conversation_attachments")
        ]
        assert any("conversation_id" in cols_ for cols_ in idx_cols)

        command.downgrade(cfg, "-1")
        assert "conversation_attachments" not in inspect(eng).get_table_names()

        command.upgrade(cfg, "head")
        assert "conversation_attachments" in inspect(eng).get_table_names()
    finally:
        eng.dispose()
