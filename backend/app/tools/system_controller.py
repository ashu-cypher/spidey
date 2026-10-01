"""MISSION J.A.R.V.I.S. — system controller tool ("system").

Controlled, sandboxed local-system access. This is NOT the disabled ShellTool:
every action here is gated by design, and the gating is documented below.

GATING MODEL
------------
* ``metrics`` is read-only: safe to auto-run, no confirmation ever.
* ``run`` with an allowlisted command runs immediately. The allowlist is a
  fixed set of read-only inspection commands (``ls``, ``ps``, ``df``, ...).
  ``cat``/``head``/``tail`` are special: they run ONLY for absolute paths
  under ``~/workspace`` or ``/tmp`` (expanduser + resolve + prefix check;
  ``..`` escapes are rejected). Anything outside the sandbox is *denied*
  outright (ToolError, no confirmation offered).
* ``run`` with anything else (e.g. ``rm``, ``sudo``, ``curl``) does NOT run:
  the tool returns ``{"needs_confirmation": True, "proposal": ...}`` and the
  agent's standard confirmation gate (``SpideyAgent._action_permission`` ->
  pending token -> ``pop_pending``/``run_confirmed`` -> the chat route's
  ``confirm_token`` flow) takes over. After the user confirms, the agent
  re-invokes this tool with ``_confirmed=True`` and the command executes.
* ``open_website`` ALWAYS requires confirmation: the tool returns
  ``needs_confirmation`` on a fresh call, and on the confirmed call returns
  ``{"action": "open_website", "url": ...}`` — the FRONTEND performs
  ``window.open`` on that result; the backend never launches a browser.

The classifier intent ``system_status`` ("system status", "cpu usage",
"diagnostics", ...) only ever builds ``{"action": "metrics"}`` args, so
destructive phrasing can never be routed here by the rule-based provider.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from app.tools.base import BaseTool, ToolError

# Read-only inspection commands that may run without confirmation.
_ALLOWLIST = {
    "ls", "ps", "df", "du", "uptime", "whoami", "date", "echo",
    "hostname", "pwd", "free", "id", "uname",
}
# File readers: allowed only for absolute paths under these roots.
_FILE_READERS = {"cat", "head", "tail"}
_READABLE_ROOTS = ("~/workspace", "/tmp")
_RUN_TIMEOUT = 15.0
_OUTPUT_CAP = 4000


def _readable_roots() -> list[Path]:
    return [Path(r).expanduser().resolve() for r in _READABLE_ROOTS]


def _is_allowed_file_arg(arg: str) -> bool:
    """True when *arg* is an absolute path inside a readable root.

    Rejects ``..`` escapes explicitly; ``Path.resolve()`` normalises the
    rest before the prefix check.
    """
    if ".." in Path(arg).parts:
        return False
    try:
        candidate = Path(arg).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    if not candidate.is_absolute():
        return False
    return any(candidate == root or root in candidate.parents for root in _readable_roots())


def _check_command(command: str) -> tuple[bool, str]:
    """Return (auto_runnable, reason). Non-auto commands need confirmation."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return False, "unparseable command"
    if not parts:
        return False, "empty command"
    prog = Path(parts[0]).name
    if prog in _ALLOWLIST:
        return True, ""
    if prog in _FILE_READERS:
        targets = [a for a in parts[1:] if not a.startswith("-")]
        if not targets:
            return False, "no file given"
        if all(_is_allowed_file_arg(t) for t in targets):
            return True, ""
        return False, "denied-path"
    return False, "not-allowlisted"


class SystemControllerTool(BaseTool):
    name = "system"
    description = (
        "Reads system metrics and runs sandboxed read-only shell commands. "
        "Anything outside the allowlist needs explicit user confirmation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["metrics", "run", "open_website"]},
            "command": {"type": "string"},
            "url": {"type": "string"},
        },
        "required": ["action"],
    }
    output_schema = {"action": "str"}
    permission = "read"
    # Per-args gating is decided by ``permission_for`` below; this static map
    # is the safe default (agent falls back to it when args are absent).
    action_permissions = {"metrics": "read", "run": "confirm", "open_website": "confirm"}

    def permission_for(self, action: str | None, args: dict | None = None) -> str:
        """Per-args permission: allowlisted ``run`` commands are "read",
        everything else under ``run``/``open_website`` is "confirm"."""
        if action == "run" and args:
            auto, _ = _check_command(args.get("command") or "")
            return "read" if auto else "confirm"
        return self.action_permissions.get(action or "", self.permission)

    async def _run(self, **kwargs) -> dict:
        action = kwargs.get("action")
        if action == "metrics":
            return self._metrics()
        if action == "run":
            return await self._run_command(
                kwargs.get("command") or "",
                confirmed=bool(kwargs.get("_confirmed")),
            )
        if action == "open_website":
            return self._open_website(
                kwargs.get("url") or "",
                confirmed=bool(kwargs.get("_confirmed")),
            )
        raise ToolError(f"Unknown system action: {action!r}.")

    def _metrics(self) -> dict:
        import psutil

        metrics: dict = {"action": "metrics"}
        try:
            metrics["cpu_percent"] = float(psutil.cpu_percent(interval=0.2))
        except Exception:
            metrics["cpu_percent"] = 0.0
        try:
            vm = psutil.virtual_memory()
            metrics["ram"] = {
                "percent": float(vm.percent),
                "used_gb": round(vm.used / (1024**3), 2),
                "total_gb": round(vm.total / (1024**3), 2),
            }
        except Exception:
            metrics["ram"] = {"percent": 0.0, "used_gb": 0.0, "total_gb": 0.0}
        try:
            metrics["disk"] = {"percent": float(psutil.disk_usage("/").percent)}
        except Exception:
            metrics["disk"] = {"percent": 0.0}
        try:
            metrics["uptime_seconds"] = int(time.time() - psutil.boot_time())
        except Exception:
            metrics["uptime_seconds"] = 0
        # Battery sensors are absent on most servers/VMs — guarded, may be None.
        try:
            batt = psutil.sensors_battery()
            metrics["battery"] = (
                {"percent": int(batt.percent), "plugged": bool(batt.power_plugged)}
                if batt is not None
                else None
            )
        except Exception:
            metrics["battery"] = None
        try:
            metrics["host"] = socket.gethostname()
        except Exception:
            metrics["host"] = "unknown"
        return metrics

    async def _run_command(self, command: str, confirmed: bool) -> dict:
        if not command.strip():
            raise ToolError("No command given.")
        auto, reason = _check_command(command)
        if reason == "denied-path":
            # Outside the readable sandbox: hard denial, no confirmation path.
            raise ToolError(
                "Denied, sir: file readers may only touch files under "
                "~/workspace or /tmp."
            )
        if not auto and not confirmed:
            # Outside the allowlist: hand to the agent's pending-confirmation
            # flow (pop_pending / run_confirmed / chat confirm_token).
            return {
                "needs_confirmation": True,
                "proposal": f"Run shell command: {command}",
            }
        parts = shlex.split(command)
        prog = Path(parts[0]).name
        if prog in _FILE_READERS:
            # The sandbox check validated these paths with expanduser(); the
            # subprocess gets no shell, so expand "~" here for real execution.
            parts = [
                p if p.startswith("-") else os.path.expanduser(p) for p in parts
            ]
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                parts,
                capture_output=True,
                text=True,
                timeout=_RUN_TIMEOUT,
                # Never a shell: no injection via metacharacters.
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {int(_RUN_TIMEOUT)}s, sir.")
        except FileNotFoundError:
            raise ToolError(f"Command not found: {parts[0]}.")
        except Exception:
            raise ToolError("The command could not be executed.")
        stdout = (completed.stdout or "")[:_OUTPUT_CAP]
        stderr = (completed.stderr or "")[:_OUTPUT_CAP]
        return {
            "action": "run",
            "command": command,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    def _open_website(self, url: str, confirmed: bool) -> dict:
        url = (url or "").strip()
        try:
            parsed = urlparse(url)
        except Exception:
            raise ToolError("That URL doesn't look valid, sir.")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ToolError(
                "Denied, sir: only http(s) URLs with a host may be opened."
            )
        if not confirmed:
            return {
                "needs_confirmation": True,
                "proposal": f"Open website: {url}",
            }
        # The backend never launches a browser itself; the frontend performs
        # window.open on this result after the user confirms.
        return {"action": "open_website", "url": url}
