"""Phase 5 — shell tool STUB (spec 31: no unrestricted computer control).

Registered as "shell" so the name exists in the registry, but ``execute()``
always refuses. The classifier must NEVER route to it.
"""
from app.tools.base import BaseTool, ToolError

_REFUSAL = (
    "Shell access is not enabled in SPIDEY v1. Computer control requires "
    "explicit user opt-in and is out of scope for v1."
)


class ShellTool(BaseTool):
    name = "shell"
    description = "Shell access (disabled in SPIDEY v1)."
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    output_schema = {}
    permission = "confirm"

    async def _run(self, **kwargs) -> dict:
        raise ToolError(_REFUSAL)
