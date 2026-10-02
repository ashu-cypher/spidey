"""Output Generation tool — create real files from content.

Actions:
  pdf      markdown text -> PDF (via fpdf2, standard fonts)
  docx     markdown text -> DOCX (via python-docx)
  markdown plain markdown file (.md)
  text     plain text file (.txt)
  csv      rows (list of lists) -> CSV file
  outline  structured presentation outline (.md) — used when the user asks
           for a PPTX, which MEW does not generate (honest limitation).

Every action writes into the shared ``generated/`` directory, verifies the
file exists and is non-empty, and returns a download URL. Nothing is faked:
if a conversion library is missing or the write fails, the tool raises
ToolError with the real reason.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from pathlib import Path

from app.tools.base import BaseTool, ToolError

_GENERATED_DIR = Path(__file__).resolve().parents[2] / "generated"

_FORMATS = ("pdf", "docx", "markdown", "text", "csv", "outline")

# fpdf2 core fonts are latin-1 only: normalize common unicode first.
_UNICODE_FIXES = {
    "\u2014": "--", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2022": "-", "\u2026": "...",
    "\u00a0": " ", "\u2192": "->", "\u2190": "<-", "\u2713": "[x]",
}


def _to_latin1(text: str) -> str:
    for src, dst in _UNICODE_FIXES.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE_RE = re.compile(r"`(.+?)`")


def _strip_md_inline(text: str) -> str:
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    text = _CODE_RE.sub(r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def _markdown_to_pdf_bytes(title: str, markdown: str) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.multi_cell(0, 10, _to_latin1(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    in_code = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            pdf.set_font("Courier", "", 9)
            pdf.multi_cell(0, 5, _to_latin1(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue
        if not line.strip():
            pdf.ln(3)
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", line.strip())
        if m:
            level = len(m.group(1))
            size = {1: 15, 2: 13, 3: 11}[level]
            pdf.set_font("Helvetica", "B", size)
            pdf.multi_cell(0, 8, _to_latin1(_strip_md_inline(m.group(2))), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2)
            continue
        m = re.match(r"^(\s*[-*]|\s*\d+\.)\s+(.*)", line)
        if m:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, _to_latin1("- " + _strip_md_inline(m.group(2))), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            continue
        pdf.set_font("Helvetica", "", 10)
        # Inline bold: split the paragraph into segments.
        parts = _BOLD_RE.split(_strip_md_inline(line))
        # _strip_md_inline already removed bold markers; render plain.
        pdf.multi_cell(0, 6, _to_latin1(_strip_md_inline(line)), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def _markdown_to_docx_bytes(title: str, markdown: str) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading(title, level=0)
    in_code = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            p = doc.add_paragraph()
            run = p.add_run(line)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            continue
        if not line.strip():
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", line.strip())
        if m:
            doc.add_heading(_strip_md_inline(m.group(2)), level=len(m.group(1)))
            continue
        m = re.match(r"^(\s*[-*]|\s*\d+\.)\s+(.*)", line)
        if m:
            doc.add_paragraph(_strip_md_inline(m.group(2)), style="List Bullet")
            continue
        p = doc.add_paragraph()
        for i, seg in enumerate(_BOLD_RE.split(line)):
            run = p.add_run(_strip_md_inline(seg) if i % 2 == 0 else seg)
            if i % 2 == 1:
                run.bold = True
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _rows_to_csv_bytes(rows: list) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        if isinstance(row, (list, tuple)):
            writer.writerow(row)
        else:
            writer.writerow([row])
    return buf.getvalue().encode("utf-8")


class GenerateTool(BaseTool):
    name = "generate"
    description = (
        "Generate real output files: PDF, DOCX, Markdown, TXT, CSV, or a "
        "presentation outline. Writes to the generated/ folder and returns "
        "a download URL. PPTX is not supported — use 'outline' instead."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["pdf", "docx", "markdown", "text", "csv", "outline"],
            },
            "title": {"type": "string"},
            "content": {"type": "string", "description": "Markdown/text body"},
            "rows": {
                "type": "array",
                "description": "Rows for CSV (list of lists)",
            },
        },
        "required": ["action"],
    }
    output_schema = {
        "file": "object",
    }
    # Low-risk write: creates a new file in generated/, never overwrites
    # user data. Auto-executed (no confirmation) per spec 16.
    permission = "low_write"

    def __init__(self) -> None:
        _GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "").strip().lower()
        if action not in _FORMATS:
            raise ToolError(
                f"Unknown format {action!r}. Supported: pdf, docx, markdown, "
                "text, csv, outline. (PPTX is not supported — ask for an "
                "'outline' instead.)"
            )
        title = (kwargs.get("title") or "").strip() or "MEW output"
        content = kwargs.get("content") or ""
        if action == "csv":
            rows = kwargs.get("rows")
            if not rows:
                raise ToolError("CSV needs 'rows' (a list of lists).")
            data = _rows_to_csv_bytes(rows)
            ext = "csv"
        elif action == "pdf":
            if not content.strip():
                raise ToolError("Nothing to put in the PDF — content is empty.")
            try:
                data = _markdown_to_pdf_bytes(title, content)
            except ImportError:
                raise ToolError("PDF library (fpdf2) isn't installed.")
            ext = "pdf"
        elif action == "docx":
            if not content.strip():
                raise ToolError("Nothing to put in the DOCX — content is empty.")
            try:
                data = _markdown_to_docx_bytes(title, content)
            except ImportError:
                raise ToolError("DOCX library (python-docx) isn't installed.")
            ext = "docx"
        elif action == "markdown":
            data = content.encode("utf-8")
            ext = "md"
        elif action == "outline":
            # Structured presentation outline, honest about PPTX.
            body = content.strip() or "## Outline\n\n(Add your points here.)"
            data = (
                f"# {title} — presentation outline\n\n"
                f"> MEW generates real PPTX files only when python-pptx is "
                f"available; this outline is the portable starting point.\n\n"
                f"{body}\n"
            ).encode("utf-8")
            ext = "md"
        else:  # text
            data = content.encode("utf-8")
            ext = "txt"

        # Verify: write, then confirm the file exists and is non-empty.
        file_id = uuid.uuid4().hex
        filename = f"{file_id}.{ext}"
        path = _GENERATED_DIR / filename
        path.write_bytes(data)
        if not path.exists() or path.stat().st_size == 0:
            raise ToolError("File write failed verification — nothing was created.")
        return {
            "file": {
                "id": file_id,
                "title": title,
                "format": ext,
                "size_bytes": path.stat().st_size,
                "download_url": f"/api/generated/{file_id}/download",
            }
        }

    def resolve_path(self, file_id: str) -> Path | None:
        """Server-side path lookup for the download route."""
        if not re.fullmatch(r"[0-9a-f]{32}", file_id or ""):
            return None
        for ext in ("pdf", "docx", "md", "txt", "csv"):
            p = _GENERATED_DIR / f"{file_id}.{ext}"
            if p.exists():
                return p
        return None
