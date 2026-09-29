"""Phase 3 — RAG knowledge base tests (sqlite backend, hashing embeddings).

The whole suite pins the DEV hashing embedding provider so results are
deterministic regardless of whether sentence-transformers is installed.
"""
import io

import pytest

from app.agents.spidey_agent import (
    LOW_CONFIDENCE_REPLY,
    RAG_CONFIDENCE_THRESHOLD,
    SpideyAgent,
)
from app.providers.rule_based import RuleBasedProvider
from app.rag import embeddings
from app.rag.pipeline import (
    MAX_UPLOAD_BYTES,
    chunk_text,
    extract_text,
    ingest_document,
    list_documents,
    validate_upload,
)
from app.tools import TOOL_REGISTRY
from app.tools.base import ToolError
from app.workflows.engine import WorkflowEngine


@pytest.fixture(autouse=True)
def _hashing_provider(monkeypatch):
    monkeypatch.setattr(
        embeddings, "_provider", embeddings.HashingEmbeddingProvider()
    )


# ---------------------------------------------------------------------------
# minimal valid PDF writer (hand-rolled; pypdf must extract the text)
# ---------------------------------------------------------------------------

def _make_pdf(text: str) -> bytes:
    content = f"BT /F1 24 Tf 100 700 Td ({text}) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream"
        % (len(content), content.encode("latin-1")),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i)
        out.write(body)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objs) + 1))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objs) + 1, xref_pos)
    )
    return out.getvalue()


def _make_docx(text: str) -> bytes:
    from docx import Document as DocxDocument

    doc = DocxDocument()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------

def test_chunking_overlap_and_count():
    words = [f"w{i}" for i in range(25)]
    chunks = chunk_text(" ".join(words), words_per_chunk=10, overlap_words=3)
    assert len(chunks) == 4  # [0:10] [7:17] [14:24] [21:25]
    assert chunks[0].split() == words[0:10]
    # overlap: tail of chunk 0 == head of chunk 1
    assert chunks[1].split()[:3] == words[7:10]
    assert chunks[2].split()[:3] == words[14:17]
    assert chunks[3].split() == words[21:25]


def test_chunking_empty():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


# ---------------------------------------------------------------------------
# validation / extraction
# ---------------------------------------------------------------------------

def test_validate_rejects_bad_extension():
    with pytest.raises(ToolError):
        validate_upload("evil.exe", b"data")


def test_validate_rejects_oversize():
    with pytest.raises(ToolError):
        validate_upload("big.txt", b"x" * (MAX_UPLOAD_BYTES + 1))


def test_extract_rejects_empty_text():
    with pytest.raises(ToolError):
        extract_text(".txt", b"   \n  ", "blank.txt")


def test_pdf_extraction_roundtrip():
    data = _make_pdf("SPIDEY RAG pipeline test about the quick brown fox.")
    text = extract_text(".pdf", data, "test.pdf")
    assert "quick brown fox" in text


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def test_ingest_txt():
    doc = ingest_document(b"The quick brown fox jumps over the lazy dog.", "fox.txt")
    assert doc["filename"] == "fox.txt"
    assert doc["content_type"] == "text/plain"
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == 1
    assert doc["created_at"]


def test_ingest_pdf():
    data = _make_pdf("SPIDEY RAG pipeline test document about hedgehogs.")
    doc = ingest_document(data, "hedgehog.pdf")
    assert doc["filename"] == "hedgehog.pdf"
    assert doc["content_type"] == "application/pdf"
    assert doc["chunk_count"] >= 1


def test_ingest_docx():
    data = _make_docx("Spidey docx ingest test about banana bread recipes.")
    doc = ingest_document(data, "recipes.docx")
    assert doc["filename"] == "recipes.docx"
    assert "officedocument" in doc["content_type"]
    assert doc["chunk_count"] >= 1


def test_ingest_bad_type_raises_user_safe_error():
    with pytest.raises(ToolError) as exc:
        ingest_document(b"data", "nope.exe")
    assert "traceback" not in exc.value.user_message.lower()


def test_ingest_empty_pdf_raises():
    with pytest.raises(ToolError):
        ingest_document(_make_pdf("   "), "blank.pdf")


def test_list_documents_includes_metadata():
    ingest_document(b"metadata probe alpha beta gamma", "probe.txt")
    docs = list_documents()
    probe = next(d for d in docs if d["filename"] == "probe.txt")
    assert {"filename", "content_type", "created_at", "status", "chunk_count"} <= set(
        probe.keys()
    )


# ---------------------------------------------------------------------------
# retrieval via the rag tool
# ---------------------------------------------------------------------------

async def test_rag_search_returns_right_document_with_metadata():
    rag = TOOL_REGISTRY["rag"]
    fox = ingest_document(
        b"The zebra xylophone concerto premiered at the grand symphony hall.",
        "retrieval_probe.txt",
    )
    ingest_document(
        b"Banana bread recipes call for ripe bananas, flour and sugar.",
        "banana_probe.txt",
    )
    out = await rag.execute(action="search", query="where did the zebra xylophone concerto premiere")
    results = out["results"]
    assert results, "expected at least one hit"
    top = results[0]
    assert top["document_id"] == fox["id"]
    assert top["chunk_id"]
    assert top["chunk_index"] == 0
    assert top["source"] == "retrieval_probe.txt"
    assert top["created_at"]
    assert top["score"] > RAG_CONFIDENCE_THRESHOLD
    assert "zebra xylophone" in top["content"]


async def test_rag_search_low_confidence_scores():
    rag = TOOL_REGISTRY["rag"]
    ingest_document(
        b"The zebra xylophone concerto premiered at the grand symphony hall.",
        "retrieval_probe2.txt",
    )
    out = await rag.execute(
        action="search", query="quantum astrophysics zxqwv unrelated nonsense"
    )
    for r in out["results"]:
        assert r["score"] < RAG_CONFIDENCE_THRESHOLD


async def test_rag_list_documents():
    rag = TOOL_REGISTRY["rag"]
    ingest_document(b"list probe content here", "listprobe.txt")
    out = await rag.execute(action="list_documents")
    assert any(d["filename"] == "listprobe.txt" for d in out["documents"])


# ---------------------------------------------------------------------------
# classifier + agent wiring
# ---------------------------------------------------------------------------

async def test_classifier_knowledge_search():
    provider = RuleBasedProvider()
    c = await provider.aclassify_intent("search my documents for the refund policy")
    assert c["intent"] == "knowledge_search"
    assert c["tools"] == ["rag"]
    assert c["response_mode"] == "grounded"


async def test_classifier_summarize_document():
    provider = RuleBasedProvider()
    c = await provider.aclassify_intent("summarize my annual report")
    assert c["intent"] == "summarize_document"
    assert c["tools"] == ["rag"]


async def test_agent_grounded_answer_with_citation():
    provider = RuleBasedProvider()
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    ingest_document(
        b"The wombat knitting club meets every Thursday at the lighthouse.",
        "grounded_probe.txt",
    )
    engine = WorkflowEngine()
    run = engine.create_run("search my documents for the wombat knitting club")
    resp = await agent.run(
        "search my documents for the wombat knitting club", run, engine
    )
    assert resp.startswith("Based on your uploaded documents")
    assert "[grounded_probe.txt, chunk 0]" in resp
    assert engine.get_run(run.workflow_id).status == "completed"


async def test_agent_low_confidence_reply_exact():
    provider = RuleBasedProvider()
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    ingest_document(
        b"The wombat knitting club meets every Thursday at the lighthouse.",
        "grounded_probe2.txt",
    )
    engine = WorkflowEngine()
    run = engine.create_run("search my documents for quantum astrophysics zxqwv")
    resp = await agent.run(
        "search my documents for quantum astrophysics zxqwv", run, engine
    )
    assert resp == LOW_CONFIDENCE_REPLY


def test_compose_facts_no_results_is_low_confidence():
    facts = SpideyAgent._compose_facts(
        "knowledge_search", "search my documents for x", {"rag": {"results": []}}, []
    )
    assert facts == LOW_CONFIDENCE_REPLY
