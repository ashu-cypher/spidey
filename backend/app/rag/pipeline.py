"""Phase 3 — RAG ingest pipeline (spec section 27).

validate -> extract -> chunk -> embed -> store.

Upload originals are persisted under ``backend/uploads/`` (gitignored, never
committed). Every failure raises :class:`ToolError` with a user-safe message —
no tracebacks ever reach the user.
"""
from __future__ import annotations

import io
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.database import get_session
from app.models import Document, DocumentChunk
from app.rag.embeddings import get_embedding_provider
from app.tools.base import ToolError

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

CHUNK_WORDS = 400
CHUNK_OVERLAP_WORDS = 50

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validate_upload(filename: str, data: bytes) -> str:
    """Validate a candidate upload; return the lowercase extension.

    Raises :class:`ToolError` with a user-safe message on any problem.
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ToolError(
            f"Unsupported file type {ext or '(none)'} — "
            "upload a .pdf, .docx, .txt or .md file."
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise ToolError("File is too large — the limit is 10 MB.")
    return ext


def extract_text(ext: str, data: bytes, filename: str) -> str:
    """Extract plain text from an uploaded file. Raises ToolError if empty."""
    text = ""
    if ext == ".pdf":
        text = _extract_pdf(data)
    elif ext == ".docx":
        text = _extract_docx(data)
    elif ext in {".txt", ".md"}:
        text = _extract_text(data)
    text = _clean(text)
    if not text:
        raise ToolError(f"Couldn't read any text from {filename or 'that file'}.")
    return text


def _clean(text: str) -> str:
    # Security: strip control characters (except whitespace) so hostile or
    # malformed document bytes can never reach a prompt or TTS verbatim.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        raise ToolError("That PDF couldn't be opened — it may be corrupted.")
    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        if text.strip():
            pages.append(f"[Page {i + 1}]\n{text}")
    text = "\n\n".join(pages).strip()
    if not text:
        # Scanned/image PDF: no extractable text, and no OCR is configured.
        # Be honest instead of pretending.
        raise ToolError(
            "I received the PDF, but it has no extractable text — it's "
            "likely a scanned document. OCR isn't configured, so I can't "
            "read it yet."
        )
    return text


def _extract_docx(data: bytes) -> str:
    import io

    from docx import Document as DocxDocument

    try:
        doc = DocxDocument(io.BytesIO(data))
    except Exception:
        raise ToolError("That .docx file couldn't be opened — it may be corrupted.")
    parts: list[str] = []
    # Paragraphs with heading structure preserved.
    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        style = (p.style.name or "").lower()
        if style.startswith("heading"):
            level = "".join(c for c in style if c.isdigit()) or "1"
            parts.append(f"\n{'#' * int(level)} {text}\n")
        else:
            parts.append(text)
    # Tables: render as markdown tables.
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [(c.text or "").strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                rows.append(cells)
        if rows:
            parts.append("")
            # Header row + separator
            parts.append("| " + " | ".join(rows[0]) + " |")
            parts.append("| " + " | ".join("---" for _ in rows[0]) + " |")
            for r in rows[1:]:
                parts.append("| " + " | ".join(r) + " |")
            parts.append("")
    text = "\n".join(parts).strip()
    if not text:
        raise ToolError("That .docx file had no readable text.")
    return text


def _extract_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    raise ToolError("That text file couldn't be decoded.")


def chunk_text(
    text: str,
    words_per_chunk: int = CHUNK_WORDS,
    overlap_words: int = CHUNK_OVERLAP_WORDS,
) -> list[str]:
    """Split text into ~``words_per_chunk``-word chunks with word overlap.

    Overlap keeps context across chunk boundaries so a query matching the tail
    of one chunk still finds the neighboring chunk relevant.
    """
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    step = max(1, words_per_chunk - overlap_words)
    for start in range(0, len(words), step):
        piece = words[start : start + words_per_chunk]
        if not piece:
            break
        chunks.append(" ".join(piece))
        if start + words_per_chunk >= len(words):
            break
    return chunks


def _save_upload(filename: str, data: bytes) -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "upload"
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
    path.write_bytes(data)
    return path


def ingest_document(
    data: bytes,
    filename: str,
    content_type: str | None = None,
    user_id: str = "local",
) -> dict:
    """Full ingest: validate -> extract -> chunk -> embed -> store.

    Returns a JSON-serializable document dict. Raises ToolError (user-safe) on
    any validation or extraction failure.
    """
    ext = validate_upload(filename, data)
    text = extract_text(ext, data, filename)
    chunks = chunk_text(text)
    if not chunks:
        raise ToolError(f"Couldn't read any text from {filename or 'that file'}.")

    saved_path = _save_upload(filename or "upload", data)
    provider = get_embedding_provider()
    embeddings = provider.embed(chunks)
    now = _utcnow()

    with get_session() as session:
        doc = Document(
            user_id=user_id,
            title=filename,
            filename=filename,
            content_type=content_type or _CONTENT_TYPES[ext],
            source=str(saved_path),
            status="ready",
            chunk_count=len(chunks),
            created_at=now,
        )
        session.add(doc)
        session.flush()  # populate doc.id
        for index, (content, vector) in enumerate(zip(chunks, embeddings)):
            session.add(
                DocumentChunk(
                    document_id=doc.id,
                    chunk_index=index,
                    content=content,
                    embedding=list(vector),
                    source=filename,
                    created_at=now,
                )
            )
        return _document_to_dict(doc)


def _document_to_dict(doc: Document) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "title": doc.title,
        "content_type": doc.content_type,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "source": doc.source,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def list_documents(user_id: str = "local") -> list[dict]:
    from sqlalchemy import select

    with get_session() as session:
        rows = (
            session.execute(
                select(Document)
                .where(Document.user_id == user_id)
                .order_by(Document.created_at.desc())
            )
            .scalars()
            .all()
        )
        return [_document_to_dict(row) for row in rows]


def delete_document(document_id: str, user_id: str = "local") -> dict:
    from sqlalchemy import delete, select

    with get_session() as session:
        doc = session.execute(
            select(Document).where(
                Document.id == document_id, Document.user_id == user_id
            )
        ).scalar_one_or_none()
        if doc is None:
            raise ToolError("Document not found.")
        session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        file_path = doc.source
        session.delete(doc)
    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass
    return {"deleted": document_id}
