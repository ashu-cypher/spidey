"""Syllabus Intelligence — structured extraction from academic documents.

When a syllabus / curriculum / exam-timetable / assignment-notice text is
ingested (the same PDF text the chat attach endpoint extracts), deterministic
heuristics pull out STRUCTURED data: university, course, semester, academic
year, subjects (name, code, credits, marks), per-subject units/topics, term
work, practicals/orals, exam types, marks distribution, exam dates and
deadlines.

Extraction strategy:
  1. Deterministic pass (regex + structure detection) — always runs.
  2. LLM-assisted fallback — only when the deterministic pass finds no
     subjects AND a real LLM provider is configured. The model is asked to
     structure the text into the same schema; fields it cannot find stay
     null/empty. This path is labeled ``derived_by="llm"``. Nothing is ever
     invented: absent fields stay null/empty lists.

Records are stored via MemoryTool under the ``[syllabus-doc]`` namespace as
JSON, each carrying its source document name (and page refs when the text
carries them). Multiple documents cross-reference: syllabus + timetable +
notice feed ``academic_timeline()``.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from app.tools.base import BaseTool, ToolError
from app.tools.memory_tool import MemoryTool

logger = logging.getLogger("spidey")

_mem = MemoryTool()

_MARKER = "[syllabus-doc]"

# ---------------------------------------------------------------------------
# Deterministic extraction
# ---------------------------------------------------------------------------

# Subject headers, in priority order. (1) SPPU-style numeric course codes,
# (2) alpha codes like CO-301 / CS 401, (3) explicit "Subject:" labels,
# (4) markdown headings carrying a code token.
_SUBJECT_PATTERNS = [
    re.compile(r"^(?:sub(?:ject)?\.?\s*[:\-–—]?\s*)?(\d{5,6})\s*[:.\-–—]\s*(.+?)\s*$"),
    re.compile(
        r"^((?!unit\b|chapter\b|module\b|sem(?:ester)?\b)[A-Z]{2,5}\s*[-]?\s*\d{3,4}[A-Z]{0,3})"
        r"\s*[:.\-–—]\s*(.+?)\s*$"
    ),
    re.compile(r"^(?:subject|course)\s*[:\-–—]\s*(.+?)\s*$", re.IGNORECASE),
]
_CODE_IN_TEXT = re.compile(r"(\d{5,6}|[A-Z]{2,5}\s*[-]?\s*\d{3,4}[A-Z]{0,3})")
_MD_HEADING = re.compile(r"^#{1,4}\s*(.+?)\s*$")

_UNIT_RE = re.compile(
    r"^(?:unit|chapter|module)\s*[-#:.\s]*"
    r"(\d+|i{1,3}|iv|v|vi{0,3}|ix|x)\s*[:.\-–—]?\s*(.+?)\s*$",
    re.IGNORECASE,
)
_ROMAN_TO_INT = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}


def _unit_number(raw: str) -> int | None:
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    return _ROMAN_TO_INT.get(raw)
_TOPIC_RE = re.compile(
    r"^\s*(?:[-•*–—]|\d{1,2}[.)]|\([a-z]\)|[a-z][.)])\s+(.+?)\s*$"
)
_CREDITS_RE = re.compile(r"\bcredits?\s*[:\-–—]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_MARKS_RES = {
    "insem": re.compile(r"\bin[\s\-]?sem(?:ester)?\s*[:\-–—]?\s*(\d+)", re.IGNORECASE),
    "endsem": re.compile(r"\bend[\s\-]?sem(?:ester)?\s*[:\-–—]?\s*(\d+)", re.IGNORECASE),
    "theory": re.compile(r"\btheory\s*[:\-–—]?\s*(\d+)\s*(?:marks?)?", re.IGNORECASE),
    "term_work": re.compile(r"\bterm[\s\-]?work\s*[:\-–—]?\s*(\d+)", re.IGNORECASE),
    "practical": re.compile(r"\bpracticals?\s*[:\-–—]?\s*(\d+)", re.IGNORECASE),
    "oral": re.compile(r"\boral\s*[:\-–—]?\s*(\d+)", re.IGNORECASE),
    "total": re.compile(r"\btotal\s*(?:marks?)?\s*[:\-–—]?\s*(\d+)", re.IGNORECASE),
}
_UNIVERSITY_RE = re.compile(
    r"(?i)\b(university|institute of technology|college of engineering)\b"
)
_DEGREE_RE = re.compile(
    r"(?i)\b((?:B\.?\s?E\.?|B\.?\s?Tech|M\.?\s?Tech|MCA|BCA|MBA))\.?\s*"
    r"([A-Za-z][A-Za-z &()\-]{0,60})"
)
_SEMESTER_RE = re.compile(r"(?i)\bsem(?:ester)?\s*[:\-–—]?\s*([IVXivx]{1,4}|\d{1,2})\b")
_YEAR_RE = re.compile(r"\b(20\d{2})\s*[-–/]\s*(\d{2}|\d{4})\b")

_MONTHS = (
    "jan january feb february mar march apr april may jun june "
    "jul july aug august sep sept september oct october nov november dec december"
).split()
_DATE_RES = [
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b"),
    re.compile(
        r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s*,?\s*(20\d{2})\b",
        re.IGNORECASE,
    ),
]
_DEADLINE_WORDS = re.compile(
    r"(?i)\b(deadline|last date|submission|due date|submit by|assignment)\b"
)
_TIMETABLE_WORDS = re.compile(r"(?i)\b(time[\s\-]?table|examination schedule|exam schedule)\b")
_EXAM_TYPE_WORDS = {
    "practical": re.compile(r"(?i)\bpracticals?\b"),
    "oral": re.compile(r"(?i)\boral\b"),
    "term_work": re.compile(r"(?i)\bterm[\s\-]?work\b"),
    "theory": re.compile(r"(?i)\b(theory|written|in[\s\-]?sem|end[\s\-]?sem)\b"),
}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def detect_doc_type(text: str) -> str:
    """syllabus | timetable | notice — keyword + structure heuristics."""
    head = "\n".join(text.splitlines()[:15])
    date_lines = sum(1 for line in text.splitlines() if any(rx.search(line) for rx in _DATE_RES))
    if _TIMETABLE_WORDS.search(head):
        return "timetable"
    if _DEADLINE_WORDS.search(head):
        return "notice"
    if date_lines >= 2:
        return "timetable"
    return "syllabus"


def _parse_date(text: str) -> str | None:
    """First date found in ``text`` -> ISO ``YYYY-MM-DD`` (or None)."""
    m = _DATE_RES[0].search(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    m = _DATE_RES[1].search(text)
    if m:
        d = int(m.group(1))
        mon = m.group(2).lower()[:3]
        month = ["jan", "feb", "mar", "apr", "may", "jun",
                 "jul", "aug", "sep", "oct", "nov", "dec"].index(mon) + 1
        try:
            return date(int(m.group(3)), month, d).isoformat()
        except ValueError:
            return None
    return None


def _header_meta(lines: list[str]) -> dict:
    meta: dict = {
        "university": None, "course": None, "semester": None, "academic_year": None,
    }
    head = lines[:8]
    for line in head:
        if meta["university"] is None and _UNIVERSITY_RE.search(line):
            meta["university"] = _clean(line)
    for line in head:
        m = _DEGREE_RE.search(line)
        if m:
            branch = re.split(
                r"\s+-\s+|\b[Ss]em(?:ester)?\b|\(20\d{2}", m.group(2)
            )[0]
            meta["course"] = _clean(
                f"{m.group(1)} {branch}".replace(".", "")
            )
            break
    for line in head:
        m = _SEMESTER_RE.search(line)
        if m:
            meta["semester"] = m.group(1).upper()
            break
    for line in head:
        m = _YEAR_RE.search(line)
        if m:
            y2 = m.group(2)
            meta["academic_year"] = (
                f"{m.group(1)}-{y2}" if len(y2) == 4 else f"{m.group(1)}-{y2}"
            )
            break
    return meta


def _split_subject_name_code(raw: str) -> tuple[str, str | None]:
    """'Database Management Systems (CO301)' -> (name, code)."""
    raw = _clean(raw)
    m = re.search(r"\(([^)]{2,20})\)\s*$", raw)
    if m and _CODE_IN_TEXT.fullmatch(m.group(1).strip()):
        return _clean(raw[: m.start()]), m.group(1).strip()
    m = _CODE_IN_TEXT.search(raw)
    if m and (m.start() < 12 or m.end() > len(raw) - 12):
        name = _clean(raw[: m.start()] + " " + raw[m.end():])
        return name or raw, m.group(1).strip()
    return raw, None


def _exam_types_for(block_text: str, marks: dict) -> list[str]:
    types: list[str] = []
    low = block_text.lower()
    if marks.get("insem") or marks.get("endsem") or marks.get("theory") \
            or "theory" in low or "end-sem" in low or "endsem" in low:
        types.append("theory")
    if marks.get("practical") or "practical" in low:
        types.append("practical")
    if marks.get("term_work") or "term work" in low:
        types.append("term_work")
    if marks.get("oral") or re.search(r"\boral\b", low):
        types.append("oral")
    return types


def _parse_subject_block(name: str, code: str | None, lines: list[str]) -> dict:
    units: list[dict] = []
    cur: dict | None = None
    marks: dict = {}
    credits: float | None = None
    block_text = "\n".join(lines)
    for line in lines:
        um = _UNIT_RE.match(line.strip())
        if um:
            if cur:
                units.append(cur)
            num = _unit_number(um.group(1))
            cur = {"number": str(num) if num else um.group(1).lstrip("0") or "0",
                   "title": _clean(um.group(2)), "topics": []}
            continue
        cm = _CREDITS_RE.search(line)
        if cm and credits is None:
            try:
                credits = float(cm.group(1))
            except ValueError:
                credits = None
        for key, rx in _MARKS_RES.items():
            mm = rx.search(line)
            if mm and key not in marks:
                try:
                    marks[key] = int(mm.group(1))
                except ValueError:
                    pass
        tm = _TOPIC_RE.match(line)
        if tm and cur is not None:
            topic = _clean(tm.group(1))
            if topic and topic not in cur["topics"]:
                cur["topics"].append(topic)
    if cur:
        units.append(cur)
    # Renumber-safe: keep numbers as found; sort numerically when possible.
    def _num(u: dict) -> int:
        try:
            return int(u["number"])
        except ValueError:
            return 999
    units.sort(key=_num)
    total_marks = marks.get("total")
    if total_marks is None:
        total_marks = sum(
            marks.get(k, 0) for k in ("insem", "endsem", "theory", "practical", "oral")
        ) or None
    return {
        "name": name,
        "code": code,
        "credits": credits,
        "marks": total_marks,
        "marks_distribution": marks,
        "units": units,
        "exam_types": _exam_types_for(block_text, marks),
        "has_term_work": bool(marks.get("term_work")) or "term work" in block_text.lower(),
        "has_practical": bool(marks.get("practical")) or bool(
            _EXAM_TYPE_WORDS["practical"].search(block_text)),
        "has_oral": bool(marks.get("oral")),
    }


def _subject_starts(line: str, next_lines: list[str]) -> tuple[str, str | None] | None:
    """A line that opens a new subject block -> (name, code) or None."""
    s = line.strip()
    if not s or len(s) > 120:
        return None
    if _UNIT_RE.match(s):
        return None
    for rx in _SUBJECT_PATTERNS[:2]:
        m = rx.match(s)
        if m:
            name, code = m.group(2), m.group(1)
            return _clean(name), code.strip()
    m = _SUBJECT_PATTERNS[2].match(s)
    if m:
        return _split_subject_name_code(m.group(1))
    m = _MD_HEADING.match(s)
    if m:
        heading = _clean(m.group(1))
        cm = _CODE_IN_TEXT.search(heading)
        if cm:
            name = _clean(heading[: cm.start()] + " " + heading[cm.end():]) or heading
            return name, cm.group(1).strip()
        # Heading with no code: accept as a subject only when a Unit/Credits
        # line follows shortly (avoids swallowing section titles).
        for nl in next_lines[:4]:
            if _UNIT_RE.match(nl.strip()) or _CREDITS_RE.search(nl):
                return heading, None
    return None


def extract_syllabus(text: str, source_name: str) -> dict:
    """Deterministic syllabus extraction. Never invents: missing -> null/[]."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    meta = _header_meta(lines)
    subjects: list[dict] = []
    cur_name: str | None = None
    cur_code: str | None = None
    cur_lines: list[str] = []
    # Pre-subject lines may hold document-level term-work/practical info.
    for i, line in enumerate(lines):
        hit = _subject_starts(line, lines[i + 1: i + 6])
        if hit:
            if cur_name is not None:
                subjects.append(_parse_subject_block(cur_name, cur_code, cur_lines))
            cur_name, cur_code = hit
            cur_lines = []
        elif cur_name is not None:
            cur_lines.append(line)
    if cur_name is not None:
        subjects.append(_parse_subject_block(cur_name, cur_code, cur_lines))
    return {
        "source_name": source_name,
        "doc_type": "syllabus",
        "derived_by": "deterministic",
        **meta,
        "subjects": subjects,
        "exam_dates": [],
        "deadlines": [],
    }


def _exam_type_of_line(line: str) -> str:
    low = line.lower()
    if _EXAM_TYPE_WORDS["practical"].search(low):
        return "practical"
    if _EXAM_TYPE_WORDS["oral"].search(low):
        return "oral"
    if _EXAM_TYPE_WORDS["term_work"].search(low):
        return "term_work"
    return "theory"


def extract_timetable(text: str, source_name: str) -> dict:
    """Deterministic exam-timetable extraction: dates + subject + type."""
    exam_dates: list[dict] = []
    for line in text.splitlines():
        iso = _parse_date(line)
        if not iso:
            continue
        exam_type = _exam_type_of_line(line)
        # Subject = the line with date/time/exam-type tokens stripped.
        subj = line
        for rx in _DATE_RES:
            subj = rx.sub(" ", subj)
        subj = re.sub(r"\([^)]*\)", " ", subj)  # "(210254)" course codes
        subj = re.sub(
            r"(?i)\b(\d{1,2}:\d{2}\s*(?:am|pm)?|morning|evening|afternoon|"
            r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
            r"theory|practical|practicals|oral|term[\s\-]?work|written|"
            r"in[\s\-]?sem|end[\s\-]?sem)\b",
            " ", subj,
        )
        subj = re.sub(r"[-–—:|;,]+", " ", subj)
        subj = _clean(subj)
        # Drop generic timetable headers ("Date Subject Time").
        if not subj or len(subj) < 3 or re.fullmatch(r"(?i)(date|day|time|subject)(\s+(date|day|time|subject))*", subj):
            continue
        exam_dates.append({
            "subject": subj,
            "date": iso,
            "type": exam_type,
            "source": source_name,
        })
    # Deduplicate identical (subject, date) pairs, keep first.
    seen: set[tuple[str, str]] = set()
    uniq: list[dict] = []
    for e in exam_dates:
        key = (e["subject"].lower(), e["date"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return {
        "source_name": source_name,
        "doc_type": "timetable",
        "derived_by": "deterministic",
        "university": None, "course": None, "semester": None, "academic_year": None,
        "subjects": [],
        "exam_dates": sorted(uniq, key=lambda e: e["date"]),
        "deadlines": [],
    }


def extract_notice(text: str, source_name: str) -> dict:
    """Deterministic notice extraction: deadlines (assignments, submissions)."""
    deadlines: list[dict] = []
    for line in text.splitlines():
        if not _DEADLINE_WORDS.search(line):
            continue
        iso = _parse_date(line)
        if not iso:
            continue
        title = _clean(re.sub(r"(?i)\b(deadline|last date|due date)\s*[:\-–—]?\s*", "", line))
        title = re.sub(r"(?i)^of\s+", "", title)
        for rx in _DATE_RES:
            title = rx.sub(" ", title)
        title = _clean(re.sub(r"[-–—:|;,]+", " ", title))
        deadlines.append({"title": title[:160], "date": iso, "source": source_name})
    seen: set[tuple[str, str]] = set()
    uniq = []
    for d in deadlines:
        key = (d["title"].lower(), d["date"])
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return {
        "source_name": source_name,
        "doc_type": "notice",
        "derived_by": "deterministic",
        "university": None, "course": None, "semester": None, "academic_year": None,
        "subjects": [],
        "exam_dates": [],
        "deadlines": sorted(uniq, key=lambda d: d["date"]),
    }


# ---------------------------------------------------------------------------
# LLM-assisted fallback (only when a real LLM provider is configured)
# ---------------------------------------------------------------------------

_LLM_SCHEMA_HINT = """Return ONLY valid JSON (no fences, no commentary) with this shape:
{"university": string|null, "course": string|null, "semester": string|null,
 "academic_year": string|null,
 "subjects": [{"name": string, "code": string|null, "credits": number|null,
   "marks": number|null, "marks_distribution": {}, "exam_types": [],
   "has_term_work": false, "has_practical": false, "has_oral": false,
   "units": [{"number": string, "title": string, "topics": [string]}]}]}
Rules: use ONLY facts present in the text. Fields you cannot find stay null or []. Never invent subject names, codes, units, or marks."""


async def _llm_structure(text: str, source_name: str) -> dict | None:
    """Ask the configured LLM to structure the text. None when unavailable
    or when the model output is unusable — the deterministic result stands."""
    try:
        from app.providers.manager import LLM_PROVIDER_NAMES, get_provider
    except Exception:
        return None
    try:
        provider = get_provider()
    except Exception:
        return None
    if getattr(provider, "name", "") not in LLM_PROVIDER_NAMES:
        return None
    try:
        raw = await provider.agenerate(
            "Extract the academic syllabus structure from the document text below.\n"
            + _LLM_SCHEMA_HINT,
            context=text[:8000],
        )
    except Exception as exc:
        logger.warning("syllabus LLM fallback failed: %s", exc)
        return None
    try:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
        data = json.loads(cleaned)
        if not isinstance(data, dict) or not data.get("subjects"):
            return None
        doc = {
            "source_name": source_name,
            "doc_type": "syllabus",
            "derived_by": "llm",
            "university": data.get("university"),
            "course": data.get("course"),
            "semester": data.get("semester"),
            "academic_year": data.get("academic_year"),
            "subjects": [],
            "exam_dates": [],
            "deadlines": [],
        }
        for s in data.get("subjects", []):
            if not isinstance(s, dict) or not s.get("name"):
                continue
            units = []
            for u in s.get("units") or []:
                if isinstance(u, dict) and u.get("title"):
                    units.append({
                        "number": str(u.get("number") or ""),
                        "title": str(u.get("title")),
                        "topics": [str(t) for t in (u.get("topics") or []) if t][:20],
                    })
            doc["subjects"].append({
                "name": str(s["name"]),
                "code": s.get("code"),
                "credits": s.get("credits"),
                "marks": s.get("marks"),
                "marks_distribution": s.get("marks_distribution") or {},
                "units": units,
                "exam_types": [str(t) for t in (s.get("exam_types") or [])],
                "has_term_work": bool(s.get("has_term_work")),
                "has_practical": bool(s.get("has_practical")),
                "has_oral": bool(s.get("has_oral")),
            })
        return doc if doc["subjects"] else None
    except Exception as exc:
        logger.warning("syllabus LLM output unusable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Store (memory namespace, append-only, newest record per source wins)
# ---------------------------------------------------------------------------

def _serialize(doc: dict) -> str:
    return f"{_MARKER} {doc['source_name']} :: " + json.dumps(doc, ensure_ascii=False)


def _parse_record(content: str) -> dict | None:
    if not content.startswith(_MARKER):
        return None
    try:
        return json.loads(content.split("::", 1)[1])
    except (json.JSONDecodeError, IndexError):
        return None


async def load_documents() -> list[dict]:
    """All ingested academic documents, newest record per source first."""
    res = await _mem.execute(action="recall", query="syllabus-doc", limit=60)
    docs: list[dict] = []
    for m in res.get("results", []):
        parsed = _parse_record(m.get("content", ""))
        if parsed:
            docs.append(parsed)
    seen: dict[str, dict] = {}
    for d in docs:
        seen[d.get("source_name", "")] = d
    return list(seen.values())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _acronym(name: str) -> str:
    return "".join(w[0] for w in re.findall(r"[A-Za-z]+", name) if w)[:6].lower()


def _is_subsequence(query: str, name: str) -> bool:
    """Abbreviation check: 'dbms' matches 'database management systems'
    (letters appear in order). Guards length so 'ai' doesn't match everything."""
    if len(query) < 3 or len(query) > len(name):
        return False
    it = iter(name)
    return all(ch in it for ch in query)


def find_subject(query: str, subjects: list[dict]) -> dict | None:
    """Fuzzy subject lookup: exact, code, substring, acronym ('AI' ->
    'Artificial Intelligence'). Returns None when nothing matches."""
    q = _norm(query)
    if not q:
        return None
    for s in subjects:
        if _norm(s.get("name", "")) == q or _norm(s.get("code") or "") == q:
            return s
    for s in subjects:
        name, code = s.get("name", ""), s.get("code") or ""
        if q == _norm(code) or (len(q) >= 2 and q in _norm(name)):
            return s
    for s in subjects:
        if q == _acronym(s.get("name", "")):
            return s
    for s in subjects:
        if _is_subsequence(q, _norm(s.get("name", ""))):
            return s
    # Word-overlap fallback: every significant query word in the name.
    words = [w for w in re.findall(r"[a-z]{3,}", query.lower()) if w not in
             {"the", "and", "for", "subject"}]
    if words:
        for s in subjects:
            low = s.get("name", "").lower()
            if all(w in low for w in words):
                return s
    return None


def all_subjects(docs: list[dict]) -> list[dict]:
    """Every subject across syllabus docs, tagged with its source."""
    out = []
    for d in docs:
        for s in d.get("subjects", []):
            s2 = dict(s)
            s2["_source"] = d.get("source_name")
            out.append(s2)
    return out


def match_exam_to_subject(exam_subject: str, subjects: list[dict]) -> dict | None:
    """Best-effort cross-reference of a timetable row to a known subject."""
    hit = find_subject(exam_subject, subjects)
    if hit:
        return hit
    # Timetable rows sometimes carry "Subject — code" mashups; try tokens.
    for token in re.findall(r"[A-Za-z]{3,}", exam_subject):
        hit = find_subject(token, subjects)
        if hit:
            return hit
    return None


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Official syllabus fetch — public university curriculum documents.
# The user shouldn't have to hunt down and upload their syllabus when it's
# a public document. Verified URLs only; factual structure extraction.
# ---------------------------------------------------------------------------

OFFICIAL_SYLLABI = {
    # SPPU 2024 Pattern, Third Year Computer Engineering, Semester V.
    # Verified 2026-10-05: official curriculum PDF hosted by the university.
    "sppu_te_comp_2024_sem5": {
        "label": "SPPU Computer Engineering 2024 Pattern — Semester V",
        "url": "https://engg.dypvp.edu.in/DownloadS3File.aspx?file=TE-2024-Syllabus",
        "university": "Savitribai Phule Pune University",
        "course": "Computer Engineering",
        "pattern": "2024",
        "semester": 5,
    },
}


async def fetch_official_syllabus(key: str) -> dict:
    """Download a verified official syllabus PDF, extract its text, and
    return (text, source_name). Raises ToolError on failure."""
    import httpx
    import os

    entry = OFFICIAL_SYLLABI.get(key)
    if not entry:
        raise ToolError(f"Unknown official syllabus: {key}")
    # Proxy-aware client (same pattern as search/whatsapp): works in
    # sandboxes and on normal machines.
    proxy = (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("HTTP_PROXY")
    )
    verify: str | bool = True
    ca = os.environ.get("SSL_CERT_FILE")
    if ca and os.path.exists(ca):
        verify = ca
    try:
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True,
            trust_env=False, proxy=proxy, verify=verify,
        ) as client:
            resp = await client.get(entry["url"])
            resp.raise_for_status()
            data = resp.content
    except Exception as e:
        logger.warning("official syllabus fetch failed: %s", e)
        raise ToolError(
            f"Couldn't download the official syllabus ({entry['label']}). "
            "Check your network or upload the PDF manually."
        )
    if len(data) < 10_000 or not data.startswith(b"%PDF"):
        raise ToolError(
            "The download didn't return a valid PDF — the university may "
            "have moved the file. Upload the PDF manually instead."
        )
    # Extract text with pypdf (same as the RAG pipeline).
    try:
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(data))
        parts = []
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            if t.strip():
                parts.append(f"[page {i + 1}]\n{t}")
        text = "\n".join(parts)
    except Exception as e:
        logger.warning("official syllabus pdf extract failed: %s", e)
        raise ToolError("Couldn't read the downloaded syllabus PDF.")
    if len(text.strip()) < 500:
        raise ToolError("The downloaded PDF had no readable text.")
    return {"text": text, "entry": entry}


class SyllabusTool(BaseTool):
    name = "syllabus"
    description = (
        "Syllabus intelligence: ingest academic documents (syllabus, exam "
        "timetable, notices) into structured records, then query subjects, "
        "units, term work, practicals, exam dates, and a combined academic "
        "timeline. Every answer cites its source document."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "ingest", "documents", "subjects", "subject", "term_work",
                    "practicals", "exams", "units", "search", "timeline",
                    "fetch_official",
                ],
            },
            "syllabus_key": {
                "type": "string",
                "description": (
                    "Key into OFFICIAL_SYLLABI, e.g. "
                    "'sppu_te_comp_2024_sem5'. Only for fetch_official."
                ),
            },
            "document_text": {"type": "string"},
            "source_name": {"type": "string"},
            "doc_type": {
                "type": "string",
                "enum": ["auto", "syllabus", "timetable", "notice"],
            },
            "subject": {"type": "string"},
            "query": {"type": "string"},
        },
        "required": ["action"],
    }
    output_schema = {"result": "object"}
    permission = "read"
    action_permissions = {"ingest": "low_write", "fetch_official": "low_write"}

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "").strip().lower()
        if action == "fetch_official":
            return {"fetched": await self._fetch_official(kwargs)}
        if action == "ingest":
            return {"ingested": await self._ingest(kwargs)}
        if action == "documents":
            docs = await load_documents()
            return {"documents": [
                {"source_name": d.get("source_name"), "doc_type": d.get("doc_type"),
                 "derived_by": d.get("derived_by"),
                 "subjects": len(d.get("subjects", [])),
                 "exam_dates": len(d.get("exam_dates", [])),
                 "deadlines": len(d.get("deadlines", []))}
                for d in docs
            ]}
        if action == "subjects":
            docs = await load_documents()
            subs = all_subjects(docs)
            if not subs:
                raise ToolError(
                    "No syllabus data yet — ingest a syllabus document first "
                    "(attach the PDF and say 'this is my syllabus')."
                )
            return {"subjects": [
                {"name": s["name"], "code": s.get("code"),
                 "credits": s.get("credits"), "marks": s.get("marks"),
                 "units": len(s.get("units", [])),
                 "exam_types": s.get("exam_types", []),
                 "source": s.get("_source")}
                for s in subs
            ]}
        if action == "subject":
            s = await self._resolve_subject(kwargs.get("subject") or "")
            return {"subject": s}
        if action == "term_work":
            docs = await load_documents()
            subs = [s for s in all_subjects(docs) if s.get("has_term_work")]
            if not all_subjects(docs):
                raise ToolError("No syllabus data yet — ingest a syllabus first.")
            return {"term_work_subjects": [
                {"name": s["name"], "code": s.get("code"),
                 "marks": (s.get("marks_distribution") or {}).get("term_work"),
                 "source": s.get("_source")} for s in subs]}
        if action == "practicals":
            docs = await load_documents()
            subs = [s for s in all_subjects(docs)
                    if s.get("has_practical") or s.get("has_oral")]
            if not all_subjects(docs):
                raise ToolError("No syllabus data yet — ingest a syllabus first.")
            return {"practical_subjects": [
                {"name": s["name"], "code": s.get("code"),
                 "exam_types": s.get("exam_types", []),
                 "source": s.get("_source")} for s in subs]}
        if action == "exams":
            return {"exams": await self._exams(kwargs.get("subject"))}
        if action == "units":
            s = await self._resolve_subject(kwargs.get("subject") or "")
            return {"units": {
                "subject": s["name"], "code": s.get("code"),
                "units": s.get("units", []), "source": s.get("_source")}}
        if action == "search":
            return {"results": await self._search(kwargs.get("query") or "")}
        if action == "timeline":
            return {"timeline": await self._timeline()}
        raise ToolError(f"Unknown syllabus action: {action!r}.")

    # -- ingest -----------------------------------------------------------
    async def _fetch_official(self, kwargs: dict) -> dict:
        """Download a verified official syllabus and ingest it.

        Fixes the 'No syllabus data yet' dead-end: when the user's
        syllabus is a public university document, MEW fetches it itself
        instead of demanding an upload.
        """
        key = (kwargs.get("syllabus_key") or "").strip()
        if not key:
            # Default to the user's known context: SPPU Computer
            # Engineering 2024 pattern, Semester V (from user profile).
            key = "sppu_te_comp_2024_sem5"
        fetched = await fetch_official_syllabus(key)
        entry = fetched["entry"]
        result = await self._ingest({
            "document_text": fetched["text"],
            "source_name": f"official-{key}.pdf",
            "doc_type": "syllabus",
        })
        result["label"] = entry["label"]
        result["note"] = (
            "Fetched automatically from the official university curriculum. "
            "Verify against your college's copy — elective choices and "
            "minor variations may differ."
        )
        return result

    async def _ingest(self, kwargs: dict) -> dict:
        text = (kwargs.get("document_text") or "").strip()
        source = (kwargs.get("source_name") or "").strip() or "unnamed-document"
        if not text:
            raise ToolError("Nothing to ingest — the document text is empty.")
        if len(text) < 50:
            raise ToolError(
                "That text is too short to be a syllabus — attach the full PDF."
            )
        want = (kwargs.get("doc_type") or "auto").strip().lower()
        dtype = detect_doc_type(text) if want == "auto" else want
        if dtype == "timetable":
            doc = extract_timetable(text, source)
        elif dtype == "notice":
            doc = extract_notice(text, source)
        else:
            doc = extract_syllabus(text, source)
            if not doc["subjects"]:
                # LLM-assisted fallback: only when a real LLM is configured.
                llm_doc = await _llm_structure(text, source)
                if llm_doc:
                    doc = llm_doc
        await _mem.execute(
            action="save", content=_serialize(doc),
            category="study", importance=0.8,
        )
        return {
            "source_name": source,
            "doc_type": doc["doc_type"],
            "derived_by": doc["derived_by"],
            "subjects": [
                {"name": s["name"], "code": s.get("code"),
                 "units": len(s.get("units", []))}
                for s in doc.get("subjects", [])
            ],
            "exam_dates": len(doc.get("exam_dates", [])),
            "deadlines": len(doc.get("deadlines", [])),
            "low_confidence": (
                doc["doc_type"] == "syllabus" and not doc.get("subjects")
            ),
        }

    # -- queries ----------------------------------------------------------
    async def _resolve_subject(self, query: str) -> dict:
        docs = await load_documents()
        subs = all_subjects(docs)
        if not subs:
            raise ToolError("No syllabus data yet — ingest a syllabus first.")
        hit = find_subject(query, subs)
        if not hit:
            names = ", ".join(f"\"{s['name']}\"" for s in subs)
            raise ToolError(
                f"I couldn't find a subject matching {query!r}. "
                f"Your subjects: {names}."
            )
        return hit

    async def _exams(self, subject_query: str | None) -> list[dict]:
        docs = await load_documents()
        subs = all_subjects(docs)
        rows: list[dict] = []
        for d in docs:
            for e in d.get("exam_dates", []):
                matched = match_exam_to_subject(e.get("subject", ""), subs)
                rows.append({
                    "subject": matched["name"] if matched else e.get("subject"),
                    "code": matched.get("code") if matched else None,
                    "date": e.get("date"),
                    "type": e.get("type"),
                    "source": e.get("source"),
                })
        if subject_query:
            hit = find_subject(subject_query, subs)
            name = hit["name"] if hit else subject_query
            rows = [r for r in rows
                    if _norm(name) in _norm(r["subject"]) or _norm(r["subject"]) in _norm(name)]
        today = date.today().isoformat()
        for r in rows:
            try:
                r["days_left"] = (date.fromisoformat(r["date"]) - date.today()).days
            except (ValueError, TypeError):
                r["days_left"] = None
        rows.sort(key=lambda r: (r["date"] or "9999", r["subject"] or ""))
        return [r for r in rows if (r.get("date") or "") >= today] or rows

    async def _search(self, query: str) -> list[dict]:
        q = query.strip().lower()
        if not q:
            raise ToolError("What should I search your syllabus for?")
        docs = await load_documents()
        subs = all_subjects(docs)
        if not subs:
            raise ToolError("No syllabus data yet — ingest a syllabus first.")
        hits: list[dict] = []
        for s in subs:
            if q in s["name"].lower() or q in (s.get("code") or "").lower():
                hits.append({"kind": "subject", "subject": s["name"],
                             "detail": f"{len(s.get('units', []))} units",
                             "source": s.get("_source")})
            for u in s.get("units", []):
                hay = f"{u.get('title', '')} {' '.join(u.get('topics', []))}".lower()
                if q in hay:
                    hits.append({
                        "kind": "unit", "subject": s["name"],
                        "detail": f"Unit {u.get('number')}: {u.get('title')}",
                        "source": s.get("_source")})
        return hits[:20]

    async def _timeline(self) -> dict:
        docs = await load_documents()
        if not docs:
            raise ToolError("No syllabus data yet — ingest a syllabus first.")
        subs = all_subjects(docs)
        exams = await self._exams(None)
        deadlines: list[dict] = []
        for d in docs:
            deadlines.extend(d.get("deadlines", []))
        deadlines.sort(key=lambda x: x.get("date") or "9999")
        return {
            "subjects": [{"name": s["name"], "code": s.get("code"),
                          "units": len(s.get("units", [])),
                          "source": s.get("_source")} for s in subs],
            "exams": exams,
            "deadlines": deadlines,
            "sources": [d.get("source_name") for d in docs],
        }
