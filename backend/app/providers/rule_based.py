import re

from app.providers.base import AIProvider

_REMEMBER = re.compile(r"\bremember\s+(?:that\s+)?(.+)", re.IGNORECASE)
_RECALL = re.compile(
    r"\b(what do you know|what am i learning|what.*\bremember\b|do you remember|recall\b)",
    re.IGNORECASE,
)
_TASK_CREATE = re.compile(
    r"\b((create|add)\s+(a\s+)?task|todo\b)", re.IGNORECASE
)
_TASK_LIST = re.compile(
    r"\b(list|show)\b.{0,20}\btasks?\b|\bmy tasks\b", re.IGNORECASE
)
# Phase 5 intents. "remind me to" used to route to task_create; it now owns
# reminders. These are checked BEFORE the generic task intents below.
_REMINDER_CREATE = re.compile(
    r"\bremind me to\b|\bset\s+(?:a\s+)?reminders?\b", re.IGNORECASE
)
_REMINDER_COMPLETE = re.compile(
    r"\b(complete|finish|mark)\b.{0,30}\breminders?\b"
    r"|\breminders?\b.{0,20}\b(done|completed)\b",
    re.IGNORECASE,
)
_REMINDER_DELETE = re.compile(
    r"\b(delete|remove|drop|cancel)\b.{0,30}\breminders?\b"
    r"|\breminders?\b.{0,20}\b(delete|remove|drop)\b",
    re.IGNORECASE,
)
_REMINDER_LIST = re.compile(
    r"\b(list|show)\b.{0,20}\breminders?\b|\bmy reminders?\b", re.IGNORECASE
)
_TASK_DELETE = re.compile(
    r"\b(delete|remove|drop|cancel)\b.{0,30}\btasks?\b"
    r"|\btasks?\b.{0,20}\b(delete|remove|drop)\b",
    re.IGNORECASE,
)
_TASK_COMPLETE = re.compile(
    r"\b(complete|finish|mark)\b.{0,30}\btasks?\b"
    r"|\btasks?\b.{0,20}\b(done|completed)\b",
    re.IGNORECASE,
)
# Web search — checked AFTER _KNOWLEDGE so "search my documents" stays RAG.
_WEB_SEARCH = re.compile(
    r"\bsearch\b.{0,20}\b(web|internet|online)\b"
    r"|\bsearch\s+the\s+web\s+for\b"
    r"|\b(find|get)\b.{0,25}\binformation\s+about\b"
    r"|\bgoogle\b",
    re.IGNORECASE,
)
_DOCUMENT_CREATE = re.compile(
    r"\b(create|make|write)\b.{0,25}\b(documents?|notes?)\b", re.IGNORECASE
)
_CODE_EXPLAIN = re.compile(
    r"\bexplain\b.{0,40}\bcode\b"
    r"|\bwhat\s+does\s+(this|the)\s+code\s+do\b"
    r"|\bwalk\s+me\s+through\s+(this|the)\s+code\b",
    re.IGNORECASE,
)
_CALCULATE = re.compile(
    r"\b(calculat|compute|what is|what's|\d\s*[\+\-\*\/\%\^]|\bplus\b|\bminus\b|\btimes\b|\bdivided\b)",
    re.IGNORECASE,
)
_GREETING = re.compile(
    r"\b(hello|hi|hey|good morning|good afternoon|good evening)\b", re.IGNORECASE
)
_HELP = re.compile(r"\bhelp\b|what can you do", re.IGNORECASE)
# Knowledge base (RAG) intents — kept distinct from memory intents: the memory
# tool holds personal facts, the rag tool searches uploaded documents.
_KNOWLEDGE = re.compile(
    r"\b(search|find|look)\b.{0,30}\b(my\s+)?(documents?|files?)\b"
    r"|\bwhat does my document say\b"
    r"|\b(in|from|within)\s+my\s+(documents?|files?)\b"
    r"|\bmy\s+(documents?|files?)\s+say\b",
    re.IGNORECASE,
)
_SUMMARIZE_DOC = re.compile(
    r"\bsummariz(e|ing)\b.{0,40}\b(this\s+)?(document|file)\b"
    r"|\bsummarize\s+my\s+(\S+)",
    re.IGNORECASE,
)
# Resume intelligence (Phase 4) — checked before the generic intents below so
# "analyze my resume" / "improve my CV" always win over chat_fallback.
_RESUME_IMPROVE = re.compile(
    r"\b(improve|rewrite|polish|fix|upgrade|strengthen|tailor)\b"
    r".{0,40}\b(my\s+)?(resume|cv)\b",
    re.IGNORECASE,
)
_RESUME_ANALYZE = re.compile(
    r"\b(analy[sz]e|analysis|check|review|audit|critique|feedback\s+on|look\s+at)\b"
    r".{0,40}\b(my\s+)?(resume|cv)\b"
    r"|\b(my\s+)?(resume|cv)\s+(analysis|review|feedback)\b",
    re.IGNORECASE,
)


class RuleBasedProvider(AIProvider):
    name = "rule_based"

    def _classify(self, text: str) -> dict:
        if _RESUME_IMPROVE.search(text):
            return {
                "intent": "resume_improve",
                "requires_memory": True,
                "requires_tools": True,
                "tools": ["resume"],
                "response_mode": "grounded",
            }
        if _RESUME_ANALYZE.search(text):
            return {
                "intent": "resume_analyze",
                "requires_memory": True,
                "requires_tools": True,
                "tools": ["resume"],
                "response_mode": "grounded",
            }
        if _SUMMARIZE_DOC.search(text):
            return {
                "intent": "summarize_document",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["rag"],
                "response_mode": "grounded",
            }
        if _KNOWLEDGE.search(text):
            return {
                "intent": "knowledge_search",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["rag"],
                "response_mode": "grounded",
            }
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
        if _REMINDER_CREATE.search(text):
            return {
                "intent": "reminder_create",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["reminders"],
                "response_mode": "confirm",
            }
        if _REMINDER_COMPLETE.search(text):
            return {
                "intent": "reminder_complete",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["reminders"],
                "response_mode": "confirm",
            }
        if _REMINDER_DELETE.search(text):
            return {
                "intent": "reminder_delete",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["reminders"],
                "response_mode": "confirm",
            }
        if _REMINDER_LIST.search(text):
            return {
                "intent": "reminder_list",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["reminders"],
                "response_mode": "list",
            }
        if _TASK_DELETE.search(text):
            return {
                "intent": "task_delete",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["tasks"],
                "response_mode": "confirm",
            }
        if _TASK_COMPLETE.search(text):
            return {
                "intent": "task_complete",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["tasks"],
                "response_mode": "confirm",
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
        if _WEB_SEARCH.search(text):
            return {
                "intent": "web_search",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["search"],
                "response_mode": "answer",
            }
        if _DOCUMENT_CREATE.search(text):
            return {
                "intent": "document_create",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["documents"],
                "response_mode": "confirm",
            }
        if _CODE_EXPLAIN.search(text):
            return {
                "intent": "code_explain",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["code"],
                "response_mode": "answer",
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
                "I can calculate, remember things, manage tasks and reminders, "
                "search the web, search your uploaded documents, create "
                "documents/notes, explain code, analyze your resume, and chat. "
                "Try: 'calculate 12 * 8', 'remind me to call mom tomorrow', "
                "'search the web for quantum computing', "
                "'create a document titled Notes with content hello', or "
                "'explain this code: ...'."
            )
        return (
            "Noted. Give me something concrete — a calculation, "
            "something to remember, or a task."
        )
