"""Project Intelligence — the "Project Brain" (spec 8).

Read-only analysis of a project directory ON THE SERVER (when MEW runs on
the user's own machine, that IS the user's machine — the reply says so
honestly; in this sandbox it is the sandbox VM).

Action ``analyze`` scans:
  - folder structure (depth-limited, skips venv/node_modules/.git)
  - README (first ~40 lines)
  - dependencies (package.json, requirements.txt, pyproject.toml)
  - source files by language
  - TODO/FIXME/XXX markers
  - test files
  - git status when available

Action ``search`` greps for a pattern (e.g. "where is authentication
implemented" -> search for "auth").

Code MODIFICATION is deliberately not here: edits go through the existing
confirmation-gated flow. This tool only reads.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from app.tools.base import BaseTool, ToolError

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".env", "dist", "build", ".next", ".nuxt", "target", "bin", "obj",
    ".idea", ".vscode", "coverage", ".pytest_cache", ".mypy_cache",
}
_SKIP_FILES = {".DS_Store", "Thumbs.db"}
_MAX_DEPTH = 4
_MAX_FILES = 400

_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK|BUG)\b[:\s]*(.*)", re.IGNORECASE)
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb",
    ".php", ".c", ".h", ".cpp", ".cs", ".swift", ".kt", ".vue", ".svelte",
}


def _safe_resolve(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        # Relative paths resolve against the backend dir — never the whole
        # filesystem. Absolute paths are used as-is.
        p = (Path(__file__).resolve().parents[2] / p)
    return p.resolve()


def _tree(root: Path) -> tuple[list[str], list[Path]]:
    """Depth-limited tree; returns (lines, source_files)."""
    lines: list[str] = []
    sources: list[Path] = []

    def walk(dirpath: Path, prefix: str, depth: int) -> None:
        if depth > _MAX_DEPTH or len(sources) > _MAX_FILES:
            return
        try:
            entries = sorted(
                dirpath.iterdir(), key=lambda e: (e.is_file(), e.name.lower())
            )
        except (PermissionError, OSError):
            return
        entries = [
            e for e in entries
            if e.name not in _SKIP_DIRS and e.name not in _SKIP_FILES
            and not e.name.startswith(".git")
        ]
        for i, entry in enumerate(entries):
            last = i == len(entries) - 1
            branch = "└── " if last else "├── "
            lines.append(f"{prefix}{branch}{entry.name}")
            if entry.is_dir():
                walk(entry, prefix + ("    " if last else "│   "), depth + 1)
            elif entry.suffix.lower() in _CODE_EXTS:
                if len(sources) < _MAX_FILES:
                    sources.append(entry)

    lines.append(root.name or str(root))
    walk(root, "", 0)
    return lines, sources


def _read_head(path: Path, max_lines: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return ""
    return "\n".join(text.splitlines()[:max_lines])


def _dependencies(root: Path) -> dict:
    deps: dict = {}
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            deps["node"] = sorted(
                list((data.get("dependencies") or {}).keys())
                + list((data.get("devDependencies") or {}).keys())
            )[:30]
            if data.get("scripts"):
                deps["npm_scripts"] = sorted(data["scripts"].keys())[:20]
        except (json.JSONDecodeError, OSError):
            pass
    req = root / "requirements.txt"
    if req.exists():
        try:
            lines = [
                l.strip().split("#")[0].strip()
                for l in req.read_text(encoding="utf-8").splitlines()
            ]
            deps["python"] = [l for l in lines if l][:30]
        except OSError:
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        m = re.search(
            r"dependencies\s*=\s*\[(.*?)\]",
            pyproject.read_text(encoding="utf-8", errors="replace"),
            re.DOTALL,
        )
        if m:
            deps["python_pyproject"] = re.findall(r'"([^"]+)"', m.group(1))[:30]
    return deps


def _find_todos(sources: list[Path], root: Path) -> list[str]:
    todos: list[str] = []
    for f in sources[:120]:  # bound the scan
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = _TODO_RE.search(line)
            if m:
                note = (m.group(2) or "").strip()[:80]
                todos.append(f"{f.relative_to(root)}:{i} {m.group(1).upper()} {note}")
                if len(todos) >= 25:
                    return todos
    return todos


def _git_info(root: Path) -> dict:
    info: dict = {}
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if branch.returncode == 0:
            info["branch"] = branch.stdout.strip()
            log = subprocess.run(
                ["git", "-C", str(root), "log", "--oneline", "-5"],
                capture_output=True, text=True, timeout=5,
            )
            if log.returncode == 0:
                info["recent_commits"] = log.stdout.strip().splitlines()
            status = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            if status.returncode == 0:
                dirty = status.stdout.strip().splitlines()
                info["uncommitted_changes"] = len(dirty)
    except (OSError, subprocess.SubprocessError):
        pass
    return info


class ProjectTool(BaseTool):
    name = "project"
    description = (
        "Project intelligence: read-only analysis of a project directory — "
        "structure, README, dependencies, source files, TODOs, tests, git. "
        "Paths refer to the machine running MEW's backend."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["analyze", "search"]},
            "path": {"type": "string"},
            "pattern": {"type": "string", "description": "grep pattern for search"},
        },
        "required": ["action", "path"],
    }
    output_schema = {"analysis": "object"}
    permission = "read"
    timeout = 60.0

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "analyze").strip().lower()
        raw_path = (kwargs.get("path") or "").strip()
        if not raw_path:
            raise ToolError("Which project directory should I look at? Give me a path.")
        root = _safe_resolve(raw_path)
        if not root.exists():
            raise ToolError(
                f"That path doesn't exist on the machine running MEW: {raw_path}. "
                "Paths refer to the backend's machine, not your chat device."
            )
        if not root.is_dir():
            raise ToolError(f"That's a file, not a directory: {raw_path}")
        if action == "search":
            pattern = (kwargs.get("pattern") or "").strip()
            if not pattern:
                raise ToolError("What should I search for in the project?")
            return {"search": self._search(root, pattern)}
        if action == "analyze":
            return {"analysis": self._analyze(root)}
        raise ToolError(f"Unknown project action: {action!r}.")

    def _analyze(self, root: Path) -> dict:
        tree_lines, sources = _tree(root)
        readme = ""
        for candidate in ("README.md", "README.rst", "README.txt", "README"):
            p = root / candidate
            if p.exists():
                readme = _read_head(p)
                break
        by_ext: dict[str, int] = {}
        for f in sources:
            by_ext[f.suffix.lower()] = by_ext.get(f.suffix.lower(), 0) + 1
        tests = [str(f.relative_to(root)) for f in sources if "test" in f.stem.lower()]
        entry_points = []
        for candidate in (
            "app/main.py", "main.py", "src/main.py", "src/index.ts",
            "src/index.js", "index.js", "app.py", "manage.py",
        ):
            if (root / candidate).exists():
                entry_points.append(candidate)
        return {
            "path": str(root),
            "structure": "\n".join(tree_lines[:120]),
            "readme_head": readme,
            "dependencies": _dependencies(root),
            "source_files": len(sources),
            "by_language": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])[:10]),
            "entry_points": entry_points,
            "test_files": tests[:15],
            "todos": _find_todos(sources, root),
            "git": _git_info(root),
        }

    def _search(self, root: Path, pattern: str) -> dict:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)
        _, sources = _tree(root)
        hits: list[str] = []
        files_hit: set[str] = set()
        for f in sources[:200]:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    rel = str(f.relative_to(root))
                    files_hit.add(rel)
                    if len(hits) < 30:
                        hits.append(f"{rel}:{i}: {line.strip()[:120]}")
        return {
            "pattern": pattern,
            "files_matched": sorted(files_hit)[:20],
            "matches": hits,
        }
