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
