"""Language detection + sentence shaping for chat.

``detect_language`` classifies a user message as English ('en'), Hindi in
Devanagari script ('hi'), or Hinglish — Hindi written in Latin script
('hinglish'). The agent threads the detected language through the provider
so replies come back in the same language the user wrote in; Hindi input is
never translated into an English response.

``split_sentences`` / ``make_voice_summary`` shape text for streaming and
for the short voice summary the frontend speaks first: pure truncation of
the first sentences, never a rephrased or different factual claim.
"""
from __future__ import annotations

import re

# Devanagari block: any of these codepoints means Hindi script.
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")

# Distinctive Hinglish markers (Latin-script Hindi words). "assignment" and
# friends are deliberately NOT here — plain English must not misfire.
_HINGLISH_MARKERS = (
    "mujhe",
    "baje",
    "chahiye",
    "kya",
    "kal",
    "parso",
    "aaj",
    "yaad",
    "dila",
    "kaam",
    "mera",
    "meri",
    "mere",
    "batao",
    "kaise",
    "kitna",
    "kab",
    "kahan",
    "hai",
    "hain",
    "hoon",
    "karo",
    "karna",
    "dena",
    "theek",
    "bhai",
    "zara",
    "abhi",
)
# A single one of these is distinctive enough to call the text Hinglish on
# its own; the rest need at least two hits to avoid false positives on
# plain English ("hai" alone could be a typo'd "hi", etc.).
_HIGHLY_DISTINCTIVE = {"mujhe", "baje", "chahiye", "kya"}

# Sentence terminators, including the Devanagari danda.
_SENTENCE_END = re.compile(r"[^.!?\u0964]+[.!?\u0964]+\s*|[^.!?\u0964]+$")

# Initialisms like M.E.W. or U.S.A.: their inner periods are not
# sentence ends. Without protection the splitter shreds "M.E.W."
# into "M." "E." "W." … — and the voice summary becomes the nonsense
# "At your service. M.", which TTS would speak literally.
_INITIALISM = re.compile(r"\b(?:[A-Z]\.){2,}")

# Fenced code blocks are dropped entirely from speech — TTS must never
# read code aloud.
_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
# Tool/debug lines: dropped (they are not speakable content).
_TOOL_LINE = re.compile(r"^\s*[\[✓✗]")
# "From `notes.txt`:" labels survive as "From file:" (which file rarely
# matters aloud, and the backticked name reads badly).
_FROM_FILE = re.compile(r"^\s*From\s+`[^`]+`\s*:\s*", re.IGNORECASE)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_STAR = re.compile(r"\*([^*\n]+)\*")
_HEADING = re.compile(r"^\s*#{1,6}\s+")
_BLOCKQUOTE = re.compile(r"^\s*>\s?")
_BULLET = re.compile(r"^\s*(?:[-*•–—]|\d+[.)])\s+")
_HRULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")
_SENTENCE_PUNCT = re.compile(r"[.!?\u0964…:]$")

# Control characters (except whitespace) must never reach a prompt or TTS.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_chars(text: str) -> str:
    """Drop non-whitespace control characters from ``text``."""
    return _CONTROL_CHARS.sub("", text or "")


def detect_language(text: str, preferred: str = "auto") -> str:
    """Return 'en', 'hi', or 'hinglish' for ``text``.

    ``preferred`` overrides detection: anything other than "auto" is
    returned as-is ('hindi' normalizes to 'hi'). With "auto":
    Devanagari script -> 'hi'; >= 2 Hinglish markers (or 1 highly
    distinctive one) -> 'hinglish'; otherwise 'en'.
    """
    pref = (preferred or "auto").strip().lower()
    if pref != "auto":
        if pref in ("hindi", "hind"):
            return "hi"
        if pref in ("en", "english"):
            return "en"
        if pref == "hinglish":
            return "hinglish"
        return pref
    text = text or ""
    if _DEVANAGARI.search(text):
        return "hi"
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    hits = [w for w in _HINGLISH_MARKERS if w in words]
    if len(hits) >= 2:
        return "hinglish"
    if len(hits) == 1 and hits[0] in _HIGHLY_DISTINCTIVE:
        return "hinglish"
    return "en"


def split_sentences(text: str) -> list[str]:
    """Split text into sentence chunks, keeping each terminator attached.

    The whitespace following a sentence stays with that chunk, so
    ``"".join(split_sentences(t))`` reassembles ``t`` exactly (this is what
    the streaming path relies on for delta reassembly).

    Initialisms (``M.E.W.``, ``U.S.A.``) are shielded from splitting
    first and restored afterwards, so exact reassembly still holds.
    """
    src = text or ""
    spans: dict[str, str] = {}

    def _protect(m: "re.Match[str]") -> str:
        key = f"\ue000{len(spans)}\ue001"  # private-use: cannot collide
        spans[key] = m.group(0)
        return key

    protected = _INITIALISM.sub(_protect, src)
    chunks = [c for c in _SENTENCE_END.findall(protected) if c.strip()]
    if not spans:
        return chunks
    restored = []
    for chunk in chunks:
        for key, val in spans.items():
            chunk = chunk.replace(key, val)
        restored.append(chunk)
    return restored


def strip_for_speech(text: str) -> str:
    """Render ``text`` speakable: the single TTS-hygiene funnel.

    * Markdown → plain speech: headings lose ``#``, bold/italic lose their
      markers, links/images keep only their visible text, blockquotes lose
      ``>``, list items become sentences, table rows become sentences.
    * Fenced code blocks are dropped ENTIRELY (TTS must never read code).
    * Tool/debug lines (``[Attachment …]``, ``✓``/``✗`` status lines) are
      dropped; ``From `file`:`` labels survive as ``From file:``.
    * Control characters are stripped.

    Idempotent: safe to apply more than once.
    """
    src = strip_control_chars(text or "")
    # Code fences first — their contents may contain markdown-looking text.
    src = _FENCED_CODE.sub(" ", src)
    out_lines: list[str] = []
    for raw in src.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _TOOL_LINE.match(line):
            continue  # [debug …], ✓/✗ status lines: not speakable
        if _HRULE.match(line):
            continue
        line = _FROM_FILE.sub("From file: ", line)
        line = _HEADING.sub("", line)
        line = _BLOCKQUOTE.sub("", line)
        if _TABLE_SEP.match(line):
            continue  # |---|---| separator row
        if line.count("|") >= 2:
            # Table row -> one sentence per cell.
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells:
                line = ". ".join(cells)
        else:
            line = _BULLET.sub("", line)
        line = _IMAGE.sub(lambda m: m.group(1) or "image", line)
        line = _LINK.sub(lambda m: m.group(1), line)
        line = _INLINE_CODE.sub(lambda m: m.group(1), line)
        line = _BOLD.sub(lambda m: m.group(2), line)
        line = _ITALIC_STAR.sub(lambda m: m.group(1), line)
        line = line.strip()
        if not line:
            continue
        if not _SENTENCE_PUNCT.search(line):
            line += "."
        out_lines.append(line)
    return " ".join(out_lines)


def sanitize_for_prompt(text: str, limit: int = 12000) -> str:
    """Sanitize untrusted text before it enters a provider prompt.

    Strips control characters, collapses blank noise, and bounds the length
    so a hostile or merely huge document cannot blow up the prompt.
    """
    src = strip_control_chars(text or "")
    src = re.sub(r"\n{3,}", "\n\n", src).strip()
    if len(src) > limit:
        src = src[:limit].rstrip() + "…"
    return src


def make_voice_summary(text: str, lang: str = "en") -> str:
    """Short spoken-first summary: the first <=2 sentences, max ~280 chars.

    Pure truncation of the response text — never a rephrased or different
    factual claim. ``lang`` is accepted for API symmetry (all three
    languages share the same terminators) and currently unused.

    TTS hygiene: markdown / code / tool lines are stripped first via
    :func:`strip_for_speech` so the spoken summary is clean speech, not
    markup read aloud.
    """
    _ = lang
    clean = strip_for_speech(text or "")
    sentences = [c for c in split_sentences(clean) if c.strip()]
    summary = "".join(sentences[:2]).strip()
    return summary[:280]
