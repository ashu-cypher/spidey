"""Phase 5 — code explanation tool.

``explain {"code", "language"?}`` -> {"explanation", "observations"}.

With a real model provider configured, the provider generates the deep
explanation. With the offline ``rule_based`` provider (or if the provider
call fails), the tool is honest about that and falls back to structural
observations derived by regex — never inventing behavior the code doesn't
show.
"""
from __future__ import annotations

import re

from app.tools.base import BaseTool, ToolError

_OFFLINE_NOTE = (
    "No language model is configured (offline mode), so this is a structural "
    "overview rather than a deep explanation. Configure a model provider for "
    "a full line-by-line explanation."
)

_FUNC_PATTERNS = [
    re.compile(r"^\s*def\s+([A-Za-z_][\w]*)\s*\(", re.MULTILINE),          # python
    re.compile(r"^\s*function\s+([A-Za-z_][\w]*)\s*\(", re.MULTILINE),     # js
    re.compile(r"^\s*(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=\s*(?:async\s*)?\(", re.MULTILINE),  # js arrow/fn-expr
    re.compile(r"^\s*fn\s+([A-Za-z_][\w]*)\s*\(", re.MULTILINE),          # rust
    re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\]]+\s+([A-Za-z_][\w]*)\s*\([^;]*\)\s*\{", re.MULTILINE),  # java/c#
]
_CLASS_PATTERNS = [
    re.compile(r"^\s*class\s+([A-Za-z_][\w]*)", re.MULTILINE),
    re.compile(r"^\s*(?:struct|interface|enum)\s+([A-Za-z_][\w]*)", re.MULTILINE),
]
_IMPORT_PATTERNS = [
    re.compile(r"^\s*(?:import|from)\s+(\S+)", re.MULTILINE),              # python
    re.compile(r"^\s*(?:import|require)\s*\(?['\"]([^'\"]+)['\"]", re.MULTILINE),
    re.compile(r"#include\s*[<\"]([^>\"]+)[>\"]"),                        # c/c++
]


def guess_language(code: str) -> str:
    """Best-effort language guess from syntax markers; 'unknown' if unsure."""
    if re.search(r"^\s*(def |import |from \w+ import |print\()", code, re.M):
        return "python"
    if re.search(r"\b(function|const |let |=>|console\.log|require\()", code):
        return "javascript"
    if re.search(r"^\s*(fn |let mut |impl )", code, re.M):
        return "rust"
    if re.search(r"#include\s*[<\"]", code):
        return "c++"
    if re.search(r"^\s*package\s+\w+", code, re.M) and "func " in code:
        return "go"
    return "unknown"


def structural_observations(code: str) -> list[str]:
    """Honest, regex-derived facts about the snippet — no invented behavior."""
    lines = code.splitlines()
    non_blank = [ln for ln in lines if ln.strip()]
    obs = [
        f"{len(lines)} lines total ({len(non_blank)} non-blank).",
    ]
    funcs: list[str] = []
    for pat in _FUNC_PATTERNS:
        funcs.extend(pat.findall(code))
    seen = list(dict.fromkeys(funcs))[:12]
    if seen:
        obs.append(f"Defines function(s): {', '.join(seen)}.")
    classes: list[str] = []
    for pat in _CLASS_PATTERNS:
        classes.extend(pat.findall(code))
    seen_c = list(dict.fromkeys(classes))[:8]
    if seen_c:
        obs.append(f"Defines class/struct(s): {', '.join(seen_c)}.")
    imports: list[str] = []
    for pat in _IMPORT_PATTERNS:
        imports.extend(pat.findall(code))
    seen_i = list(dict.fromkeys(imports))[:10]
    if seen_i:
        obs.append(f"References: {', '.join(seen_i)}.")
    comments = sum(
        1 for ln in non_blank if ln.strip().startswith(("#", "//", "--", "*"))
    )
    if comments:
        obs.append(f"Contains {comments} comment line(s).")
    if re.search(r"\bTODO\b|\bFIXME\b|\bXXX\b", code):
        obs.append("Contains TODO/FIXME markers.")
    if re.search(r"\b(try|except|catch|finally)\b", code):
        obs.append("Includes error-handling blocks (try/except or try/catch).")
    if re.search(r"\b(await|async)\b", code):
        obs.append("Uses async/await — likely does I/O or concurrent work.")
    loops = len(re.findall(r"^\s*(for|while)\b", code, re.M))
    if loops:
        obs.append(f"Contains {loops} loop(s).")
    return obs


class CodeTool(BaseTool):
    name = "code"
    description = "Explains code snippets (structural offline, deep with a model)."
    input_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "language": {"type": "string"},
        },
        "required": ["code"],
    }
    output_schema = {"explanation": "str", "observations": "list"}
    permission = "read"

    async def _run(self, **kwargs) -> dict:
        code = kwargs.get("code") or ""
        if not code.strip():
            raise ToolError("No code provided to explain.")
        language = (kwargs.get("language") or "").strip() or guess_language(code)
        observations = structural_observations(code)

        from app.providers import get_provider  # lazy: avoids import cycles

        provider = get_provider()
        if provider.name != "rule_based":
            try:
                explanation = await provider.agenerate(
                    text=(
                        f"Explain this {language} code clearly and concisely. "
                        "Describe what it does, its inputs/outputs, and any "
                        "notable edge cases. Do not invent behavior the code "
                        "doesn't show."
                    ),
                    context=code,
                )
                return {
                    "explanation": explanation,
                    "observations": observations,
                    "language": language,
                }
            except Exception:
                pass  # fall through to the honest structural fallback
        return {
            "explanation": _OFFLINE_NOTE,
            "observations": [
                f"Language guess: {language}.",
                *observations,
            ],
            "language": language,
        }
