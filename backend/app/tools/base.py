import asyncio
from abc import ABC, abstractmethod


class ToolError(Exception):
    """A tool failure with a user-safe message."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    timeout: float = 30.0
    # Permission level for the tool's actions (Phase 5, spec 21):
    #   "read"      — safe to run automatically
    #   "low_write" — low-risk writes, auto-executed
    #   "confirm"   — needs explicit user confirmation via chat
    # ``action_permissions`` overrides the default per action name.
    permission: str = "read"
    action_permissions: dict[str, str] = {}

    @abstractmethod
    async def _run(self, **kwargs) -> dict:
        raise NotImplementedError

    async def execute(self, **kwargs) -> dict:
        try:
            return await asyncio.wait_for(self._run(**kwargs), timeout=self.timeout)
        except TimeoutError:
            raise ToolError(f"{self.name} timed out.")
        except ToolError:
            raise
        except Exception:
            raise ToolError(f"{self.name} ran into a problem.")
