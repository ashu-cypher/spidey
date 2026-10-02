"""Computer control architecture (spec 9) — INTERFACE ONLY.

This module defines the capability surface for future computer interaction:
browser navigation, clicking, typing, screenshots, file operations, terminal
commands. Every method currently reports honestly that it is NOT implemented.

This is deliberate scaffolding: the spec asks for the tool abstraction to
exist with permission gating, while actual control must never be faked.
When a real implementation lands, it replaces these stubs — the interface
stays the same.

Safety: everything here is permission "confirm" (or refused outright), so
the agent can never auto-execute computer control even by accident.
"""
from __future__ import annotations

from app.tools.base import BaseTool, ToolError

_NOT_IMPLEMENTED = (
    "Computer control isn't implemented yet — this is the interface "
    "placeholder. MEW will tell you plainly rather than pretend to click, "
    "type, or see your screen."
)


class ComputerTool(BaseTool):
    name = "computer"
    description = (
        "Computer/browser control interface (NOT IMPLEMENTED — scaffolding "
        "only). Future: screenshots, clicking, typing, file ops, terminal."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "screenshot", "click", "type_text", "read_screen",
                    "open_app", "list_windows", "run_terminal",
                ],
            },
        },
        "required": ["action"],
    }
    output_schema = {"result": "object"}
    # Nothing here may auto-run, ever.
    permission = "confirm"

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "").strip().lower()
        if action not in (
            "screenshot", "click", "type_text", "read_screen",
            "open_app", "list_windows", "run_terminal",
        ):
            raise ToolError(f"Unknown computer action: {action!r}.")
        # Honest refusal: the capability does not exist yet.
        return {
            "implemented": False,
            "action": action,
            "message": _NOT_IMPLEMENTED,
        }
