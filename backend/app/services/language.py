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

# Initialisms like J.A.R.V.I.S. or U.S.A.: their inner periods are not
# sentence ends. Without protection the splitter shreds "J.A.R.V.I.S."
# into "J." "A." "R." … — and the voice summary becomes the nonsense
# "At your service, sir. J.", which TTS would speak literally.
_INITIALISM = re.compile(r"\b(?:[A-Z]\.){2,}")


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

    Initialisms (``J.A.R.V.I.S.``, ``U.S.A.``) are shielded from splitting
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


def make_voice_summary(text: str, lang: str = "en") -> str:
    """Short spoken-first summary: the first <=2 sentences, max ~280 chars.

    Pure truncation of the response text — never a rephrased or different
    factual claim. ``lang`` is accepted for API symmetry (all three
    languages share the same terminators) and currently unused.
    """
    _ = lang
    sentences = [c for c in split_sentences(text or "") if c.strip()]
    summary = "".join(sentences[:2]).strip()
    return summary[:280]
