from app.tools.base import BaseTool, ToolError
from app.tools.calculator import CalculatorTool
from app.tools.memory_tool import MemoryTool
from app.tools.rag_tool import RAGTool
from app.tools.tasks import TaskTool

__all__ = [
    "BaseTool",
    "CalculatorTool",
    "MemoryTool",
    "RAGTool",
    "TaskTool",
    "ToolError",
    "TOOL_REGISTRY",
]

TOOL_REGISTRY: dict[str, BaseTool] = {
    "calculator": CalculatorTool(),
    "memory": MemoryTool(),
    "tasks": TaskTool(),
    "rag": RAGTool(),
}
