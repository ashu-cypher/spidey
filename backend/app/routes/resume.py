"""Phase 4 — resume HTTP API.

  POST   /api/resume/upload                       multipart CV upload (<= 10 MB, .pdf/.docx/.txt)
  POST   /api/resume/analyze                      {"version_id"} -> full analysis
  POST   /api/resume/job-match                    {"version_id","job_description"} -> match + job-specific version
  POST   /api/resume/versions/{id}/improve        suggestions + new improved version
  GET    /api/resume/versions                     list versions (newest first)
  GET    /api/resume/versions/{id}                version detail
  POST   /api/resume/versions/{id}/restore        new version copying that content (never mutates)
  GET    /api/resume/versions/{id}/download       ?format=txt|md file download
  GET    /api/resume/versions/{id}/compare/{other_id}  unified line diff

Upload originals are persisted under ``backend/uploads/`` (gitignored, never
committed). Versions are append-only. No tracebacks reach users: every failure
becomes an HTTPException with a clean, user-safe message.
"""
from __future__ import annotations

import difflib

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.rag.pipeline import MAX_UPLOAD_BYTES, extract_text, validate_upload
from app.tools import TOOL_REGISTRY
from app.tools.base import ToolError
from app.tools.resume import _save_resume_upload, detect_sections

router = APIRouter(prefix="/api/resume", tags=["resume"])

_RESUME_EXTENSIONS = {".pdf", ".docx", ".txt"}

resume_tool = TOOL_REGISTRY["resume"]


class AnalyzeRequest(BaseModel):
    version_id: str


class JobMatchRequest(BaseModel):
    version_id: str
    job_description: str


@router.post("/upload")
async def upload_resume(request: Request, file: UploadFile = File(...)):
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_UPLOAD_BYTES + 1024 * 1024:
                raise HTTPException(413, "File is too large — the limit is 10 MB.")
        except ValueError:
            pass
    try:
        data = await file.read()
    except Exception:
        raise HTTPException(400, "Couldn't read the uploaded file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is too large — the limit is 10 MB.")
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")
    filename = file.filename or "resume"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _RESUME_EXTENSIONS:
        raise HTTPException(
            422, "Unsupported file type — upload a .pdf, .docx or .txt CV."
        )
    try:
        validate_upload(filename, data)
        text = extract_text(ext, data, filename)
    except ToolError as exc:
        raise HTTPException(422, exc.user_message)
    saved_path = _save_resume_upload(filename, data)
    try:
        created = await resume_tool.execute(
            action="create_version",
            content=text,
            label=f"v1 — {filename}",
            source_filename=filename,
            created_from="upload",
        )
    except ToolError as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(400, exc.user_message)
    version = created["version"]
    version["upload_path"] = str(saved_path)
    return {"version": version}


@router.post("/analyze")
async def analyze_resume(req: AnalyzeRequest):
    try:
        result = await resume_tool.execute(action="analyze", version_id=req.version_id)
    except ToolError as exc:
        raise HTTPException(404, exc.user_message)
    return result


@router.post("/job-match")
async def job_match_resume(req: JobMatchRequest):
    if not req.job_description.strip():
        raise HTTPException(422, "job_description must not be empty.")
    try:
        result = await resume_tool.execute(
            action="job_match",
            version_id=req.version_id,
            job_description=req.job_description.strip(),
        )
    except ToolError as exc:
        raise HTTPException(404, exc.user_message)
    # Save a job-specific version (content unchanged — records what this
    # content was matched against). Never mutates existing versions.
    saved = await _save_job_match_version(req.version_id, req.job_description)
    return {"job_match": result["job_match"], "version": saved}


async def _save_job_match_version(version_id: str, job_description: str) -> dict:
    """Store a new version recording a job-match run (content unchanged)."""
    from sqlalchemy import select

    from app.database import get_session
    from app.models import ResumeVersion

    with get_session() as session:
        row = session.execute(
            select(ResumeVersion).where(ResumeVersion.id == version_id)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Resume version not found.")
        content = row.content
        source_filename = row.source_filename
    first_line = next(
        (ln.strip() for ln in job_description.splitlines() if ln.strip()), ""
    )
    try:
        created = await resume_tool.execute(
            action="create_version",
            content=content,
            label=f"job match — {first_line[:48]}",
            source_filename=source_filename,
            created_from="job_match",
        )
    except ToolError as exc:
        raise HTTPException(400, exc.user_message)
    return created["version"]


@router.post("/versions/{version_id}/improve")
async def improve_resume(version_id: str):
    try:
        result = await resume_tool.execute(action="improve", version_id=version_id)
    except ToolError as exc:
        raise HTTPException(404, exc.user_message)
    return result


@router.get("/versions")
async def list_resume_versions():
    result = await resume_tool.execute(action="list")
    return result


@router.get("/versions/{version_id}")
async def get_resume_version(version_id: str):
    result = await resume_tool.execute(action="list")
    for v in result["versions"]:
        if v["id"] == version_id:
            return {"version": v}
    raise HTTPException(404, "Resume version not found.")


@router.post("/versions/{version_id}/restore")
async def restore_resume_version(version_id: str):
    """Create a NEW version copying that version's content (never mutates)."""
    result = await resume_tool.execute(action="list")
    source = next((v for v in result["versions"] if v["id"] == version_id), None)
    if source is None:
        raise HTTPException(404, "Resume version not found.")
    try:
        created = await resume_tool.execute(
            action="create_version",
            content=source["content"],
            label=f"restored from v{source['version_number']}",
            source_filename=source["source_filename"],
            created_from=source["id"],
        )
    except ToolError as exc:
        raise HTTPException(400, exc.user_message)
    return created


@router.get("/versions/{version_id}/download")
async def download_resume_version(version_id: str, format: str = "txt"):
    if format not in ("txt", "md"):
        raise HTTPException(422, "format must be 'txt' or 'md'.")
    result = await resume_tool.execute(action="list")
    version = next((v for v in result["versions"] if v["id"] == version_id), None)
    if version is None:
        raise HTTPException(404, "Resume version not found.")
    content = version["content"]
    filename = f"resume-v{version['version_number']}.{format}"
    if format == "md":
        content = _to_markdown(content, version["label"])
    return PlainTextResponse(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _to_markdown(text: str, label: str) -> str:
    """Render plain CV text as Markdown with ## section headers."""
    sections = detect_sections(text)
    header = sections.get("header", [])
    lines = [f"# {label or 'Resume'}", ""]
    lines.extend(header)
    for name in (
        "summary",
        "experience",
        "education",
        "projects",
        "skills",
        "certifications",
        "achievements",
    ):
        body = sections.get(name)
        if body:
            lines += ["", f"## {name.capitalize()}", ""]
            lines.extend(body)
    return "\n".join(lines).strip() + "\n"


@router.get("/versions/{version_id}/compare/{other_id}")
async def compare_resume_versions(version_id: str, other_id: str):
    result = await resume_tool.execute(action="list")
    by_id = {v["id"]: v for v in result["versions"]}
    a, b = by_id.get(version_id), by_id.get(other_id)
    if a is None or b is None:
        raise HTTPException(404, "Resume version not found.")
    diff = list(
        difflib.unified_diff(
            a["content"].splitlines(),
            b["content"].splitlines(),
            fromfile=f"v{a['version_number']}",
            tofile=f"v{b['version_number']}",
            lineterm="",
        )
    )
    return {
        "from_version": a["version_number"],
        "to_version": b["version_number"],
        "diff": diff,
    }
