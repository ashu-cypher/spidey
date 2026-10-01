from app.tools.base import BaseTool, ToolError
from app.tools.calculator import CalculatorTool
from app.tools.code import CodeTool
from app.tools.documents import DocumentTool
from app.tools.memory_tool import MemoryTool
from app.tools.rag_tool import RAGTool
from app.tools.reminders import ReminderTool
from app.tools.resume import ResumeTool
from app.tools.search import SearchTool
from app.tools.shell import ShellTool
from app.tools.system_controller import SystemControllerTool
from app.tools.tasks import TaskTool

__all__ = [
    "BaseTool",
    "CalculatorTool",
    "CodeTool",
    "DocumentTool",
    "MemoryTool",
    "RAGTool",
    "ReminderTool",
    "ResumeTool",
    "SearchTool",
    "ShellTool",
    "SystemControllerTool",
    "TaskTool",
    "ToolError",
    "TOOL_REGISTRY",
]

TOOL_REGISTRY: dict[str, BaseTool] = {
    "calculator": CalculatorTool(),
    "memory": MemoryTool(),
    "tasks": TaskTool(),
    "reminders": ReminderTool(),
    "rag": RAGTool(),
    "resume": ResumeTool(),
    "search": SearchTool(),
    "documents": DocumentTool(),
    "code": CodeTool(),
    # Registered but always refuses: the classifier must never route here.
    "shell": ShellTool(),
    # Sandboxed system access: metrics auto-run; allowlisted commands auto-run;
    # everything else is confirmation-gated by the agent (permission_for).
    "system": SystemControllerTool(),
}
