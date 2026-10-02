"""Phase 4 — resume intelligence tool (spec section 37).

Rule-based CV parsing, analysis, improvement suggestions, job matching, and
append-only versioning. Honest by construction:

* **parse/analyze** only inspect text that is already in the CV — they never
  invent content;
* **improve** rewrites only *rephrase* existing bullets (stronger action
  verbs, tighter phrasing). They never add skills, numbers, dates, or
  achievements. A test-enforced invariant guarantees every skill token in a
  suggestion already exists in the CV text;
* **job_match** reports matched vs missing skills explicitly and never claims
  the user has a skill the CV doesn't evidence.

Actions: parse, analyze, improve, job_match, create_version, latest, list.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.database import get_session
from app.models import ResumeVersion
from app.rag.pipeline import UPLOAD_DIR, extract_text, validate_upload
from app.tools.base import BaseTool, ToolError


# ---------------------------------------------------------------------------
# Skill lexicon (canonical names) + aliases
# ---------------------------------------------------------------------------

_SKILLS: list[str] = [
    # programming languages
    "python", "javascript", "typescript", "java", "c++", "c#", "go", "rust",
    "kotlin", "swift", "php", "ruby", "scala", "r", "matlab", "dart",
    # web
    "react", "angular", "vue.js", "node.js", "next.js", "django", "flask",
    "fastapi", "spring boot", "express.js", "html", "css", "tailwind css",
    "redux", "svelte",
    # data / ml
    "sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "pandas", "numpy", "scikit-learn", "pytorch", "tensorflow", "keras",
    "machine learning", "deep learning", "natural language processing",
    "computer vision", "data analysis", "data visualization", "tableau",
    "power bi", "excel", "apache spark", "hadoop", "airflow", "artificial intelligence",
    # devops / cloud
    "aws", "azure", "google cloud", "docker", "kubernetes", "terraform",
    "jenkins", "ci/cd", "git", "github", "gitlab", "linux", "nginx",
    "prometheus", "grafana", "ansible",
    # mobile
    "react native", "flutter", "android", "ios",
    # testing
    "pytest", "jest", "selenium", "junit", "cypress", "playwright",
    # practices / architecture
    "agile", "scrum", "kanban", "project management", "code review",
    "test driven development", "rest api", "graphql", "microservices",
    "system design", "design patterns", "object oriented programming",
    "websockets",
    # soft / professional
    "communication", "leadership", "teamwork", "problem solving", "mentoring",
    "stakeholder management", "time management", "collaboration",
    "adaptability", "critical thinking",
]

_ALIASES: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "reactjs": "react",
    "react.js": "react",
    "vue": "vue.js",
    "node": "node.js",
    "nodejs": "node.js",
    "nextjs": "next.js",
    "postgres": "postgresql",
    "mongo": "mongodb",
    "k8s": "kubernetes",
    "ml": "machine learning",
    "nlp": "natural language processing",
    "gcp": "google cloud",
    "sklearn": "scikit-learn",
    "tdd": "test driven development",
    "oop": "object oriented programming",
    "rest": "rest api",
}


def _skill_pattern(term: str) -> "re.Pattern[str]":
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w)(?:{escaped})(?!\w)", re.IGNORECASE)


_SKILL_PATTERNS: list[tuple["re.Pattern[str]", str]] = [
    (_skill_pattern(term), term) for term in _SKILLS
] + [(_skill_pattern(alias), canon) for alias, canon in _ALIASES.items()]


def extract_skills(text: str) -> set[str]:
    """Canonical skill names found in *text* (case-insensitive, alias-aware)."""
    found: set[str] = set()
    for pattern, canonical in _SKILL_PATTERNS:
        if pattern.search(text):
            found.add(canonical)
    return found


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

_SECTION_ORDER = [
    "summary",
    "education",
    "experience",
    "projects",
    "skills",
    "certifications",
    "achievements",
]

_SECTION_PATTERNS: dict[str, list[str]] = {
    "summary": [
        r"professional\s+summary",
        r"\bsummary\b",
        r"\bobjective\b",
        r"career\s+objective",
        r"\bprofile\b",
    ],
    "education": [
        r"\beducation\b",
        r"educational\s+qualifications?",
        r"academic\s+background",
        r"\bacademics?\b",
    ],
    "experience": [
        r"work\s+experience",
        r"professional\s+experience",
        r"employment\s+history",
        r"work\s+history",
        r"\bexperience\b",
    ],
    "projects": [
        r"key\s+projects?",
        r"personal\s+projects?",
        r"selected\s+projects?",
        r"\bprojects?\b",
    ],
    "skills": [
        r"technical\s+skills?",
        r"core\s+skills?",
        r"key\s+skills?",
        r"\bcompetencies\b",
        r"tech\s+stack",
        r"\bskills?\b",
    ],
    "certifications": [
        r"\bcertifications?\b",
        r"\blicenses?\b",
        r"\bcertificates?\b",
    ],
    "achievements": [
        r"\bachievements?\b",
        r"\baccomplishments?\b",
        r"\bawards?\b",
        r"\bhonou?rs?\b",
    ],
}

_HEADER_RES: dict[str, "re.Pattern[str]"] = {
    name: re.compile(rf"^\s*(?:{'|'.join(patterns)})\s*:?\s*$", re.IGNORECASE)
    for name, patterns in _SECTION_PATTERNS.items()
}

_BULLET_RE = re.compile(r"^\s*(?:[-*•–—]|\d+[.)])\s+(.*\S)\s*$")
_RAW_TEXT_LIMIT = 6000


def detect_sections(text: str) -> dict[str, list[str]]:
    """Split CV text into sections by header lines.

    A header is a short line that is exactly a known section name
    (case-insensitive, optional trailing colon). Lines before the first
    header become the ``header`` (contact/name) block.
    """
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        matched: str | None = None
        if len(line.strip()) <= 60:
            for name in _SECTION_ORDER:
                if _HEADER_RES[name].match(line):
                    matched = name
                    break
        if matched is not None:
            current = matched
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line.strip())
    return sections


def _bullets(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        m = _BULLET_RE.match(line)
        if m:
            out.append(m.group(1).strip())
        elif len(line.split()) >= 4:
            # Non-bulleted prose lines in experience/projects still count.
            out.append(line)
    return out


def _project_entries(lines: list[str]) -> list[dict]:
    """Group a projects section into entries: {title, bullets}.

    A non-bullet line starts a new project entry (the project name); bullet
    lines attach to the current entry. "Rewrite the second project" targets
    entries[1] — the second PROJECT, not the second bullet.
    """
    entries: list[dict] = []
    current: dict | None = None
    for line in lines:
        m = _BULLET_RE.match(line)
        if m:
            if current is None:
                current = {"title": "", "bullets": []}
                entries.append(current)
            current["bullets"].append(m.group(1).strip())
        elif line.strip():
            current = {"title": line.strip(), "bullets": []}
            entries.append(current)
    return entries


# ---------------------------------------------------------------------------
# Contact extraction
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?[\d][\d\s().-]{6,}[\d]")
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/\S+", re.IGNORECASE)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/\S+", re.IGNORECASE)
_LOCATION_RE = re.compile(r"^[A-Z][A-Za-z .'\-]{1,40},\s*[A-Za-z .'\-]{2,40}$")


def extract_contact(header_lines: list[str], full_text: str) -> dict:
    """Best-effort contact block: name, email, phone, linkedin, github, location."""
    head = "\n".join(header_lines[:12])
    email_m = _EMAIL_RE.search(head) or _EMAIL_RE.search(full_text)
    phone = None
    for m in _PHONE_RE.finditer(head):
        digits = re.sub(r"\D", "", m.group(0))
        if 7 <= len(digits) <= 15:
            phone = m.group(0).strip()
            break
    linkedin_m = _LINKEDIN_RE.search(head)
    github_m = _GITHUB_RE.search(head)
    location = next(
        (ln for ln in header_lines[:10] if _LOCATION_RE.match(ln)), None
    )
    name = next(
        (ln for ln in header_lines if ln and "@" not in ln and len(ln) <= 60), ""
    )
    return {
        "name": name,
        "email": email_m.group(0) if email_m else None,
        "phone": phone,
        "linkedin": linkedin_m.group(0) if linkedin_m else None,
        "github": github_m.group(0) if github_m else None,
        "location": location,
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

WEAK_VERBS = [
    "was responsible for",
    "responsible for",
    "helped",
    "worked on",
    "assisted",
    "tasked with",
    "in charge of",
    "duties included",
    "involved in",
    "participated in",
]

VAGUE_TERMS = [
    "etc.",
    "etc",
    "various",
    "multiple",
    "diverse",
    "a lot of",
    "several different",
    "things",
    "stuff",
]

_NUMBER_RE = re.compile(r"\d|%|\$|€|₹")
_TOKEN_RE = re.compile(r"[a-z0-9+#]+")
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff\U0001f000-\U0001f2ff]"
)
_CORE_SECTIONS = ("summary", "experience", "education", "skills")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _analyze_text(text: str) -> dict:
    sections = detect_sections(text)
    section_names = [s for s in _SECTION_ORDER if sections.get(s)]
    exp_bullets = _bullets(sections.get("experience", []))
    proj_bullets = _bullets(sections.get("projects", []))
    work_bullets = exp_bullets + proj_bullets
    all_bullets = work_bullets + _bullets(sections.get("achievements", []))
    contact = extract_contact(sections.get("header", []), text)
    words = len(text.split())

    issues: list[dict] = []

    for bullet in work_bullets:
        lowered = bullet.lower()
        for weak in WEAK_VERBS:
            if weak in lowered:
                issues.append(
                    {
                        "type": "weak_verb",
                        "detail": (
                            f"Weak phrasing '{weak}' — start the bullet with a "
                            "strong action verb instead."
                        ),
                        "excerpt": bullet[:160],
                    }
                )
                break
        for vague in VAGUE_TERMS:
            if re.search(rf"(?<!\w){re.escape(vague)}(?!\w)", lowered):
                if not _NUMBER_RE.search(bullet):
                    issues.append(
                        {
                            "type": "vague_statement",
                            "detail": (
                                f"'{vague}' is vague and has no numbers — replace "
                                "it with a specific scope, count, or outcome."
                            ),
                            "excerpt": bullet[:160],
                        }
                    )
                break
        if not _NUMBER_RE.search(bullet):
            issues.append(
                {
                    "type": "missing_measurable",
                    "detail": (
                        "No measurable outcome — add a number, %, or $ "
                        "if you have one (never invent one)."
                    ),
                    "excerpt": bullet[:160],
                }
            )

    # Near-duplicate bullets (token Jaccard >= 0.6).
    seen_pairs = 0
    for i in range(len(all_bullets)):
        for j in range(i + 1, len(all_bullets)):
            a, b = all_bullets[i], all_bullets[j]
            ta, tb = _tokens(a), _tokens(b)
            if len(ta) >= 4 and len(tb) >= 4 and _jaccard(ta, tb) >= 0.5:
                issues.append(
                    {
                        "type": "repetition",
                        "detail": (
                            "Two bullets say nearly the same thing — "
                            "merge them or drop one."
                        ),
                        "excerpt": f"{a[:110]}  /  {b[:110]}",
                    }
                )
                seen_pairs += 1
                if seen_pairs >= 5:
                    break
        if seen_pairs >= 5:
            break

    counts = {
        "weak_verb": sum(1 for i in issues if i["type"] == "weak_verb"),
        "vague_statement": sum(1 for i in issues if i["type"] == "vague_statement"),
        "repetition": sum(1 for i in issues if i["type"] == "repetition"),
        "missing_measurable": sum(
            1 for i in issues if i["type"] == "missing_measurable"
        ),
    }
    missing_sections = [s for s in _CORE_SECTIONS if s not in section_names]
    quality_score = (
        100
        - 8 * min(counts["weak_verb"], 3)
        - 6 * min(counts["vague_statement"], 3)
        - 10 * min(counts["repetition"], 2)
        - 4 * min(counts["missing_measurable"], 5)
        - 8 * len(missing_sections)
        - (5 if not contact.get("email") else 0)
    )
    quality_score = max(0, min(100, quality_score))

    # ATS panel.
    sections_ok = all(s in section_names for s in ("experience", "education", "skills"))
    contact_ok = bool(contact.get("email"))
    with_skills = sum(1 for b in work_bullets if extract_skills(b))
    keyword_coverage = (
        round(with_skills / len(work_bullets), 2) if work_bullets else 0.0
    )
    formatting_risks: list[str] = []
    if words > 1200:
        formatting_risks.append(
            "Long CV (>1200 words, ~3+ pages) — recruiters and ATS parsers "
            "prefer 1–2 pages."
        )
    if _EMOJI_RE.search(text):
        formatting_risks.append(
            "Contains emojis or decorative symbols — ATS parsers often garble them."
        )
    if sum(1 for ln in text.splitlines() if ln.startswith("\t")) >= 2:
        formatting_risks.append(
            "Looks like a table/column layout (tab-indented lines) — "
            "ATS may read it out of order."
        )
    if not section_names:
        formatting_risks.append(
            "No standard section headers detected — ATS may misclassify content."
        )
    ats_score = (
        100
        - (25 if not sections_ok else 0)
        - (15 if not contact_ok else 0)
        - 10 * min(len(formatting_risks), 3)
    )
    ats_score = max(0, min(100, ats_score))

    missing_info: list[str] = []
    for s in _CORE_SECTIONS:
        if s not in section_names:
            missing_info.append(f"no {s} section")
    if not contact.get("email"):
        missing_info.append("no contact email")
    if not contact.get("phone"):
        missing_info.append("no phone number")
    if exp_bullets and not any(_NUMBER_RE.search(b) for b in exp_bullets):
        missing_info.append("no measurable achievements in experience")

    # Strengths — real, positive observations from the CV text (never
    # invented: every claim below is computed from the parsed content).
    strengths: list[str] = []
    core_found = [s for s in _CORE_SECTIONS if s in section_names]
    if len(core_found) == len(_CORE_SECTIONS):
        strengths.append(
            "All core sections present "
            f"({', '.join(core_found)})."
        )
    elif len(core_found) >= 2:
        strengths.append(
            f"Core sections present: {', '.join(core_found)}."
        )
    if contact.get("email") and contact.get("phone"):
        strengths.append("Contact block complete — email and phone present.")
    elif contact.get("email"):
        strengths.append("Contact email present.")
    detected_skills = extract_skills(text)
    if detected_skills:
        strengths.append(
            f"{len(detected_skills)} distinct skills detected across the CV."
        )
    measurable = sum(1 for b in exp_bullets if _NUMBER_RE.search(b))
    if exp_bullets and measurable:
        strengths.append(
            f"{measurable}/{len(exp_bullets)} experience bullets include "
            "measurable outcomes (numbers, %, $)."
        )
    if proj_bullets:
        strengths.append(
            f"{len(proj_bullets)} project bullet(s) — projects show applied skill."
        )
    if quality_score >= 70:
        strengths.append(f"Overall quality score {quality_score}/100.")

    # Skill gaps — skills CLAIMED in the skills section but never EVIDENCED
    # in experience/project bullets. Real and checkable: both sets come from
    # the CV text via extract_skills.
    skills_section_text = "\n".join(sections.get("skills", []))
    claimed = extract_skills(skills_section_text)
    evidenced: set[str] = set()
    for b in work_bullets:
        evidenced |= extract_skills(b)
    unevidenced = sorted(claimed - evidenced)

    return {
        "content": {
            "sections_found": section_names,
            "sections_missing": [s for s in _SECTION_ORDER if s not in section_names],
            "bullet_count": len(all_bullets),
            "word_count": words,
        },
        "issues": issues,
        "issue_counts": counts,
        "quality_score": quality_score,
        "strengths": strengths,
        "skill_gaps": {
            "claimed_in_skills_section": sorted(claimed),
            "evidenced_in_experience_or_projects": sorted(evidenced),
            "claimed_but_unevidenced": unevidenced,
        },
        "ats": {
            "sections_ok": sections_ok,
            "contact_ok": contact_ok,
            "keyword_coverage": keyword_coverage,
            "formatting_risks": formatting_risks,
            "score": ats_score,
        },
        "missing_info": missing_info,
        "contact": contact,
    }


# ---------------------------------------------------------------------------
# Improvement rewrites (rephrase only — never invent)
# ---------------------------------------------------------------------------

_VERB_REWRITES: list[tuple["re.Pattern[str]", str]] = [
    (re.compile(r"(?i)\bin\s+charge\s+of\b"), "led"),
    (re.compile(r"(?i)\btasked\s+with\b"), "executed"),
    (re.compile(r"(?i)\bhelped(?:\s+with|\s+in)?\b"), "supported"),
    (re.compile(r"(?i)\bworked\s+on\b"), "delivered"),
    (re.compile(r"(?i)\bassisted(?:\s+with|\s+in)?\b"), "supported"),
    (re.compile(r"(?i)\bduties\s+included\b"), "performed"),
    (re.compile(r"(?i)\binvolved\s+in\b"), "contributed to"),
    (re.compile(r"(?i)\bparticipated\s+in\b"), "contributed to"),
]

# "Responsible for managing X" -> "Managed X". Curated map of the gerunds
# that show up most in CVs; anything unknown falls back to "Led ...".
_GERUND_PAST = {
    "managing": "managed", "leading": "led", "developing": "developed",
    "building": "built", "creating": "created", "designing": "designed",
    "implementing": "implemented", "testing": "tested",
    "maintaining": "maintained", "supporting": "supported",
    "coordinating": "coordinated", "overseeing": "oversaw",
    "driving": "drove", "delivering": "delivered", "writing": "wrote",
    "analyzing": "analyzed", "improving": "improved",
    "mentoring": "mentored", "training": "trained", "handling": "handled",
    "running": "ran", "planning": "planned", "executing": "executed",
    "owning": "owned", "organizing": "organized", "streamlining": "streamlined",
}
_RESPONSIBLE_RE = re.compile(
    r"(?i)^\s*(?:was\s+)?responsible\s+for\s+(\S+)(.*)$"
)


def _rewrite_responsible(bullet: str) -> str | None:
    m = _RESPONSIBLE_RE.match(bullet)
    if not m:
        return None
    first, rest = m.group(1), m.group(2)
    past = _GERUND_PAST.get(first.lower())
    replacement = past if past else "led " + first
    return _capitalize_first((replacement + rest).strip())

_ETC_RE = re.compile(r"\s*,?\s*\betc\.?(?!\w)", re.IGNORECASE)


def _capitalize_first(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1 :]
    return text


def rewrite_bullet(bullet: str) -> tuple[str | None, str | None]:
    """Rule-based rephrase of one bullet.

    Returns ``(improved, reason)`` or ``(None, None)`` when no safe rewrite
    applies. Only swaps weak phrasing for stronger verbs or trims filler —
    the facts, skills, and numbers are untouched.
    """
    improved = _rewrite_responsible(bullet)
    if improved:
        return improved, "Stronger action verb — same facts, no content added."
    for pattern, verb in _VERB_REWRITES:
        if pattern.search(bullet):
            improved = _capitalize_first(pattern.sub(verb, bullet, count=1).strip())
            return improved, (
                "Stronger action verb — same facts, no content added."
            )
    if _ETC_RE.search(bullet):
        improved = _capitalize_first(_ETC_RE.sub("", bullet).strip().rstrip(","))
        return improved, (
            "Removed trailing 'etc.' — name the specific items instead of "
            "trailing off."
        )
    return None, None


async def _polish_with_provider(original: str, draft: str) -> str | None:
    """Optional LLM polish of a rewrite; never allowed to invent.

    Returns the polished bullet, or None to keep the rule-based draft.
    Degrades gracefully: rule_based provider, offline provider, over-long or
    skill-inventing output all fall back to the draft.
    """
    try:
        from app.providers import get_provider

        provider = get_provider()
    except Exception:
        return None
    if provider is None or provider.name == "rule_based":
        return None
    try:
        out = await provider.agenerate(
            "Rewrite this resume bullet with a stronger action verb. Keep the "
            "exact same meaning and facts. Do NOT add skills, numbers, dates, "
            "or achievements that are not already there. Reply with only the "
            "rewritten bullet, nothing else.",
            context=f"Original bullet: {original}\nDraft rewrite: {draft}",
        )
    except Exception:
        return None
    polished = out.strip().strip('"').strip()
    if not polished or len(polished) > 400:
        return None
    # No-invention guard: every skill token in the polished bullet must
    # already appear in the original bullet.
    if extract_skills(polished) - extract_skills(original):
        return None
    # No-invention guard, part 2: no invented NUMBERS either. A small model
    # may obey "don't add skills" while still fabricating "98% satisfaction".
    # Any numeric token (digits, %, currency) not present in the original is
    # a fabrication — fall back to the rule-based draft.
    _NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?%?|\$|€|₹")
    if set(_NUM_TOKEN.findall(polished)) - set(_NUM_TOKEN.findall(original)):
        return None
    return polished


# ---------------------------------------------------------------------------
# Job matching
# ---------------------------------------------------------------------------

_YEARS_RE = re.compile(
    r"(\d+)\s*\+?\s*years?(?:\s+of)?\s+(?:experience\s+(?:in|with)\s+)?"
    r"([a-zA-Z][a-zA-Z0-9+#./&()\- ]{1,40})",
    re.IGNORECASE,
)
_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "our", "are", "will", "have",
    "has", "this", "that", "from", "role", "work", "team", "join", "looking",
    "seeking", "ideal", "candidate", "plus", "etc", "including", "such",
    "about", "into", "over", "under", "between", "through", "within",
}


def _near_match(jd_skill: str, cv_skill: str) -> bool:
    if jd_skill == cv_skill:
        return False
    jt, ct = set(jd_skill.split()), set(cv_skill.split())
    return bool(jt & ct) or jd_skill in cv_skill or cv_skill in jd_skill


def _job_match(cv_text: str, job_description: str) -> dict:
    jd = job_description or ""
    cv_skills = extract_skills(cv_text)
    jd_skills = extract_skills(jd)

    matched = sorted(jd_skills & cv_skills)
    missing = sorted(jd_skills - cv_skills)

    unclear: list[dict] = []
    for j in missing:
        for c in sorted(cv_skills):
            if _near_match(j, c):
                unclear.append(
                    {
                        "jd_skill": j,
                        "cv_skill": c,
                        "note": (
                            f"Your CV mentions '{c}' — clarify how it relates "
                            f"to the required '{j}' if they overlap."
                        ),
                    }
                )
                break

    years_notes: list[str] = []
    for m in _YEARS_RE.finditer(jd):
        years, phrase = m.group(1), m.group(2).strip().rstrip(".,;:")
        canon = next(
            (s for s in _SKILLS + list(_ALIASES.values())
             if s in phrase.lower()),
            None,
        )
        skill_label = canon or phrase
        years_notes.append(
            f"The JD asks for {years}+ years of {skill_label} — make sure "
            "your dates evidence this."
        )

    sections = detect_sections(cv_text)
    work_bullets = _bullets(sections.get("experience", [])) + _bullets(
        sections.get("projects", [])
    )
    relevant_experience = [
        b[:160] for b in work_bullets if extract_skills(b) & set(matched)
    ][:5]

    improvements: list[str] = []
    for s in missing[:8]:
        improvements.append(
            f"The job description asks for '{s}', which doesn't appear in your "
            "CV. If you have real experience with it, add a bullet with a "
            "measurable outcome — don't add it if you don't."
        )
    for u in unclear[:4]:
        improvements.append(u["note"])
    improvements.extend(years_notes[:4])
    if matched:
        improvements.append(
            "Mirror the job description's exact keywords for skills you "
            "genuinely have — ATS systems match on keywords."
        )

    jd_terms = [
        w.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{2,}", jd)
        if w.lower() not in _STOPWORDS
    ]
    freq: dict[str, int] = {}
    for w in jd_terms:
        freq[w] = freq.get(w, 0) + 1
    cv_lower = cv_text.lower()
    keywords_to_consider = list(dict.fromkeys(
        missing
        + [
            w
            for w, n in sorted(freq.items(), key=lambda kv: -kv[1])
            if n >= 2 and w not in cv_lower
        ]
    ))[:15]

    match_score = round(100 * len(matched) / max(1, len(jd_skills)))

    return {
        "matched_skills": matched,
        "missing_skills": missing,
        "unclear_skills": unclear,
        "relevant_experience": relevant_experience,
        "improvements": improvements,
        "keywords_to_consider": keywords_to_consider,
        "match_score": match_score,
        "jd_skill_count": len(jd_skills),
    }


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _version_to_dict(v: ResumeVersion) -> dict:
    return {
        "id": v.id,
        "version_number": v.version_number,
        "label": v.label,
        "source_filename": v.source_filename,
        "created_from": v.created_from,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "content": v.content,
    }


def _next_version_number(session, user_id: str) -> int:
    current = session.execute(
        select(func.max(ResumeVersion.version_number)).where(
            ResumeVersion.user_id == user_id
        )
    ).scalar()
    return int(current or 0) + 1


def _save_resume_upload(filename: str, data: bytes) -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "upload"
    path = UPLOAD_DIR / f"resume_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{safe_name}"
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class ResumeTool(BaseTool):
    name = "resume"
    description = (
        "Parses, analyzes, improves, and job-matches the user's CV/resume. "
        "Versions are append-only: nothing is ever overwritten."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "parse",
                    "analyze",
                    "improve",
                    "job_match",
                    "create_version",
                    "latest",
                    "list",
                ],
            },
            "file_path": {"type": "string"},
            "version_id": {"type": "string"},
            "job_description": {"type": "string"},
            "content": {"type": "string"},
            "label": {"type": "string"},
            "source_filename": {"type": "string"},
            "created_from": {"type": "string"},
            "user_id": {"type": "string"},
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}
    timeout = 120.0

    async def _run(self, **kwargs) -> dict:
        action = kwargs.get("action")
        user_id = kwargs.get("user_id") or "local"

        if action == "parse":
            return self._parse(kwargs)
        if action == "analyze":
            return self._analyze(kwargs, user_id)
        if action == "improve":
            return await self._improve(kwargs, user_id)
        if action == "job_match":
            return self._job_match(kwargs, user_id)
        if action == "create_version":
            return self._create_version(kwargs, user_id)
        if action == "latest":
            return self._latest(user_id)
        if action == "list":
            return self._list(user_id)
        raise ToolError(f"Unknown resume action: {action!r}.")

    # -- actions ---------------------------------------------------------

    def _parse(self, kwargs: dict) -> dict:
        file_path = kwargs.get("file_path") or ""
        path = Path(file_path)
        if not path.is_file():
            raise ToolError("Resume file not found.")
        data = path.read_bytes()
        ext = validate_upload(path.name, data)
        text = extract_text(ext, data, path.name)
        return self._parse_text(text)

    @staticmethod
    def _parse_text(text: str) -> dict:
        sections = detect_sections(text)
        return {
            "sections": sections,
            "raw_text": text[:_RAW_TEXT_LIMIT],
            "contact": extract_contact(sections.get("header", []), text),
        }

    def _get_version(self, version_id: str | None, user_id: str) -> ResumeVersion:
        if not version_id:
            raise ToolError("A version_id is required.")
        with get_session() as session:
            version = session.execute(
                select(ResumeVersion).where(
                    ResumeVersion.id == version_id,
                    ResumeVersion.user_id == user_id,
                )
            ).scalar_one_or_none()
            if version is None:
                raise ToolError("Resume version not found.")
            session.expunge(version)
            return version

    def _analyze(self, kwargs: dict, user_id: str) -> dict:
        if kwargs.get("version_id"):
            version = self._get_version(kwargs.get("version_id"), user_id)
            text = version.content
            version_info = _version_to_dict(version)
        elif kwargs.get("file_path"):
            parsed = self._parse(kwargs)
            text = parsed["raw_text"]
            version_info = None
        else:
            raise ToolError("Analyze needs a version_id or a file_path.")
        analysis = _analyze_text(text)
        analysis["version_id"] = version_info["id"] if version_info else None
        analysis["version_number"] = (
            version_info["version_number"] if version_info else None
        )
        return {"analysis": analysis}

    async def _improve(self, kwargs: dict, user_id: str) -> dict:
        version = self._get_version(kwargs.get("version_id"), user_id)
        sections = detect_sections(version.content)
        target_section = (kwargs.get("section") or "").strip().lower() or None
        target_index = kwargs.get("index")
        target_entry = kwargs.get("entry")
        if target_section is not None:
            # Targeted rewrite, e.g. "rewrite the second project".
            if target_section not in sections or not sections[target_section]:
                raise ToolError(
                    f"Your CV has no {target_section} section to rewrite."
                )
            if target_section == "projects" and isinstance(target_entry, int):
                # The Nth PROJECT (entry), not the Nth bullet.
                entries = _project_entries(sections["projects"])
                if not 0 <= target_entry < len(entries):
                    raise ToolError(
                        f"Your projects section has {len(entries)} "
                        f"project(s) — there is no #{target_entry + 1}."
                    )
                bullets = entries[target_entry]["bullets"]
                title = entries[target_entry]["title"]
                label = (
                    f"project #{target_entry + 1}"
                    + (f" ({title[:40]})" if title else "")
                )
            else:
                bullets = _bullets(sections[target_section])
                if isinstance(target_index, int):
                    if not 0 <= target_index < len(bullets):
                        raise ToolError(
                            f"Your {target_section} section has "
                            f"{len(bullets)} bullet(s) — there is no "
                            f"#{target_index + 1}."
                        )
                    bullets = [bullets[target_index]]
                label = (
                    f"{target_section} #{target_index + 1}"
                    if isinstance(target_index, int)
                    else target_section
                )
        else:
            bullets = _bullets(sections.get("experience", [])) + _bullets(
                sections.get("projects", [])
            )
            label = None
        suggestions: list[dict] = []
        for bullet in bullets:
            improved, reason = rewrite_bullet(bullet)
            if not improved:
                continue
            polished = await _polish_with_provider(bullet, improved)
            final = polished or improved
            # No-invention invariant: every skill token in the suggestion must
            # already exist in the original bullet.
            if extract_skills(final) - extract_skills(bullet):
                final = improved
                reason = (
                    "Stronger action verb — same facts, no content added "
                    "(provider polish rejected: it added new terms)."
                )
            suggestions.append(
                {"original": bullet, "improved": final, "reason": reason}
            )
            if len(suggestions) >= 8:
                break

        if not suggestions:
            # Honest: the bullet already reads well — no safe rewrite exists,
            # so no new version is created and nothing is invented.
            where = f" ({label})" if label else ""
            return {
                "suggestions": [],
                "new_version_id": None,
                "new_version_number": None,
                "note": (
                    f"I couldn't find a safe rewrite for that bullet{where} — "
                    "it already reads well, so I left your CV unchanged."
                ),
            }

        new_text = version.content
        for s in suggestions:
            new_text = new_text.replace(s["original"], s["improved"], 1)

        with get_session() as session:
            number = _next_version_number(session, user_id)
            row = ResumeVersion(
                user_id=user_id,
                version_number=number,
                label=f"v{number} — improved wording",
                content=new_text,
                source_filename=version.source_filename,
                created_from="improvement",
                created_at=_utcnow(),
            )
            session.add(row)
            session.flush()
            new_id = row.id

        return {
            "suggestions": suggestions,
            "new_version_id": new_id,
            "new_version_number": number,
            "note": (
                "Suggestions only rephrase what is already in your CV — "
                "no skills, numbers, or achievements were added."
            ),
        }

    def _job_match(self, kwargs: dict, user_id: str) -> dict:
        jd = (kwargs.get("job_description") or "").strip()
        if not jd:
            raise ToolError("Job matching needs a job_description.")
        version = self._get_version(kwargs.get("version_id"), user_id)
        return {
            "job_match": _job_match(version.content, jd),
            "version_id": version.id,
            "version_number": version.version_number,
        }

    def _create_version(self, kwargs: dict, user_id: str) -> dict:
        content = (kwargs.get("content") or "").strip()
        if not content:
            raise ToolError("Cannot store an empty resume version.")
        with get_session() as session:
            number = _next_version_number(session, user_id)
            row = ResumeVersion(
                user_id=user_id,
                version_number=number,
                label=kwargs.get("label") or f"v{number}",
                content=content,
                source_filename=kwargs.get("source_filename") or "",
                created_from=kwargs.get("created_from"),
                created_at=_utcnow(),
            )
            session.add(row)
            session.flush()
            session.expunge(row)
            return {"version": _version_to_dict(row)}

    def _latest(self, user_id: str) -> dict:
        with get_session() as session:
            row = session.execute(
                select(ResumeVersion)
                .where(ResumeVersion.user_id == user_id)
                .order_by(ResumeVersion.version_number.desc())
            ).scalars().first()
            if row is None:
                return {"version": None}
            session.expunge(row)
            return {"version": _version_to_dict(row)}

    def _list(self, user_id: str) -> dict:
        with get_session() as session:
            rows = (
                session.execute(
                    select(ResumeVersion)
                    .where(ResumeVersion.user_id == user_id)
                    .order_by(ResumeVersion.version_number.desc())
                )
                .scalars()
                .all()
            )
            for row in rows:
                session.expunge(row)
            return {"versions": [_version_to_dict(r) for r in rows]}
