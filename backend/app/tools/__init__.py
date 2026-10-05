from app.tools.base import BaseTool, ToolError
from app.tools.attachments import AttachmentTool
from app.tools.briefing import BriefingTool
from app.tools.calculator import CalculatorTool
from app.tools.code import CodeTool
from app.tools.command_center import CommandCenterTool
from app.tools.computer import ComputerTool
from app.tools.documents import DocumentTool
from app.tools.generate import GenerateTool
from app.tools.goals import GoalsTool
from app.tools.history import HistoryTool
from app.tools.knowledge import KnowledgeTool
from app.tools.learning import LearningTool
from app.tools.memory_tool import MemoryTool
from app.tools.news import NewsTool
from app.tools.planner import PlannerTool
from app.tools.project import ProjectTool
from app.tools.rag_tool import RAGTool
from app.tools.reminders import ReminderTool
from app.tools.research import ResearchTool
from app.tools.resume import ResumeTool
from app.tools.search import SearchTool
from app.tools.shell import ShellTool
from app.tools.syllabus import SyllabusTool
from app.tools.system_controller import SystemControllerTool
from app.tools.tasks import TaskTool
from app.tools.wikipedia import WikipediaTool

__all__ = [
    "AttachmentTool",
    "BaseTool",
    "BriefingTool",
    "CalculatorTool",
    "CodeTool",
    "CommandCenterTool",
    "ComputerTool",
    "DocumentTool",
    "GenerateTool",
    "GoalsTool",
    "HistoryTool",
    "KnowledgeTool",
    "LearningTool",
    "MemoryTool",
    "PlannerTool",
    "ProjectTool",
    "RAGTool",
    "ReminderTool",
    "ResearchTool",
    "ResumeTool",
    "SearchTool",
    "ShellTool",
    "SyllabusTool",
    "SystemControllerTool",
    "TaskTool",
    "ToolError",
    "TOOL_REGISTRY",
    "WikipediaTool",
]

TOOL_REGISTRY: dict[str, BaseTool] = {
    "calculator": CalculatorTool(),
    "memory": MemoryTool(),
    "attachments": AttachmentTool(),
    "tasks": TaskTool(),
    "reminders": ReminderTool(),
    "rag": RAGTool(),
    "resume": ResumeTool(),
    "search": SearchTool(),
    "research": ResearchTool(),
    "briefing": BriefingTool(),
    "command_center": CommandCenterTool(),
    "goals": GoalsTool(),
    "history": HistoryTool(),
    "wikipedia": WikipediaTool(),
    "documents": DocumentTool(),
    "code": CodeTool(),
    # Registered but always refuses: the classifier must never route here.
    "shell": ShellTool(),
    # Computer control: interface scaffolding only — every action honestly
    # reports "not implemented". The classifier must never route here.
    "computer": ComputerTool(),
    "generate": GenerateTool(),
    "knowledge": KnowledgeTool(),
    "learning": LearningTool(),
    "news": NewsTool(),
    "project": ProjectTool(),
    # Syllabus intelligence + academic planner: structured academic
    # documents and study planning over them.
    "syllabus": SyllabusTool(),
    "planner": PlannerTool(),
    # Sandboxed system access: metrics auto-run; allowlisted commands auto-run;
    # everything else is confirmation-gated by the agent (permission_for).
    "system": SystemControllerTool(),
}
