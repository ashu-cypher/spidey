import re

from app.providers.base import AIProvider

_REMEMBER = re.compile(r"\bremember\s+(?:that\s+)?(.+)", re.IGNORECASE)
_RECALL = re.compile(
    r"\b(what do you know|what am i learning|what.*\bremember\b|do you remember|recall\b)",
    re.IGNORECASE,
)
_TASK_CREATE = re.compile(
    r"\b((create|add)\s+(a\s+)?task|remind me to|todo\b)", re.IGNORECASE
)
_TASK_LIST = re.compile(
    r"\b(list|show)\b.{0,20}\btasks?\b|\bmy tasks\b", re.IGNORECASE
)
_CALCULATE = re.compile(
    r"\b(calculat|compute|what is|what's|\d\s*[\+\-\*\/\%\^]|\bplus\b|\bminus\b|\btimes\b|\bdivided\b)",
    re.IGNORECASE,
)
_GREETING = re.compile(
    r"\b(hello|hi|hey|good morning|good afternoon|good evening)\b", re.IGNORECASE
)
_HELP = re.compile(r"\bhelp\b|what can you do", re.IGNORECASE)


class RuleBasedProvider(AIProvider):
    name = "rule_based"

    def _classify(self, text: str) -> dict:
        if _REMEMBER.search(text):
            return {
                "intent": "remember",
                "requires_memory": True,
                "requires_tools": True,
                "tools": ["memory"],
                "response_mode": "confirm",
            }
        if _RECALL.search(text):
            return {
                "intent": "recall_memory",
                "requires_memory": True,
                "requires_tools": True,
                "tools": ["memory"],
                "response_mode": "answer",
            }
        if _TASK_CREATE.search(text):
            return {
                "intent": "task_create",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["tasks"],
                "response_mode": "confirm",
            }
        if _TASK_LIST.search(text):
            return {
                "intent": "task_list",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["tasks"],
                "response_mode": "list",
            }
        if _CALCULATE.search(text):
            return {
                "intent": "calculate",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["calculator"],
                "response_mode": "answer",
            }
        if _GREETING.search(text):
            return {
                "intent": "greeting",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
                "response_mode": "greeting",
            }
        if _HELP.search(text):
            return {
                "intent": "help",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
                "response_mode": "help",
            }
        return {
            "intent": "chat_fallback",
            "requires_memory": False,
            "requires_tools": False,
            "tools": [],
            "response_mode": "chat",
        }

    async def aclassify_intent(self, text: str) -> dict:
        return self._classify(text)

    async def agenerate(self, text: str, context: str = "") -> str:
        if context:
            return context
        intent = self._classify(text)["intent"]
        if intent == "greeting":
            return "Hello. Spidey here — what are we tackling?"
        if intent == "help":
            return (
                "I can calculate, remember things, manage tasks, and chat. "
                "Try: 'calculate 12 * 8', 'remember that I am learning Python', "
                "or 'create a task for tomorrow'."
            )
        return (
            "Noted. Give me something concrete — a calculation, "
            "something to remember, or a task."
        )
