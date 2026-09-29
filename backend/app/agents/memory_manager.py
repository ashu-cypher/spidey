from app.tools.base import BaseTool


class MemoryManager:
    """Thin agent-facing wrapper over the memory tool."""

    def __init__(self, memory_tool: BaseTool) -> None:
        self._memory_tool = memory_tool

    async def retrieve_relevant(self, query: str, limit: int = 5) -> list[dict]:
        result = await self._memory_tool.execute(
            action="recall", query=query, limit=limit
        )
        return result.get("results", [])

    async def save(
        self,
        content: str,
        category: str | None = None,
        importance: float | None = None,
    ) -> dict:
        return await self._memory_tool.execute(
            action="save",
            content=content,
            category=category,
            importance=importance,
        )
