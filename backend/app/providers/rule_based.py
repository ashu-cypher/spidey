import re

from app.config import settings
from app.providers.base import AIProvider

_REMEMBER = re.compile(r"\bremember\s+(?:that\s+)?(.+)", re.IGNORECASE)
_RECALL = re.compile(
    r"\b(what do you know|what am i learning|what.*\bremember\b|do you remember|recall\b)",
    re.IGNORECASE,
)
_TASK_CREATE = re.compile(
    r"\b((create|add)\s+(a\s+)?task|todo\b)", re.IGNORECASE
)
# Natural phrasing "add <thing> to my tasks" — checked BEFORE the
# delete/complete intents because "finish" in the task title would otherwise
# trip _TASK_COMPLETE ("finish ... tasks").
_TASK_CREATE_ADD = re.compile(r"\badd\b.{0,60}\bto my tasks?\b", re.IGNORECASE)
_TASK_LIST = re.compile(
    r"\b(list|show)\b.{0,20}\btasks?\b|\bmy tasks\b", re.IGNORECASE
)
# Phase 5 intents. "remind me to" used to route to task_create; it now owns
# reminders. These are checked BEFORE the generic task intents below.
_REMINDER_CREATE = re.compile(
    r"\bremind me to\b|\bremind me (in|at)\b|\bset\s+(?:a\s+)?reminders?\b",
    re.IGNORECASE,
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
    r"|\btasks?\b.{0,20}\b(done|completed)\b"
    r"|\bmark\b.{0,50}\bcomplete\b",
    re.IGNORECASE,
)
# Web search — checked AFTER _KNOWLEDGE so "search my documents" stays RAG.
_WEB_SEARCH = re.compile(
    r"\bsearch\b.{0,20}\b(web|internet|online)\b"
    r"|\bsearch\s+the\s+web\s+for\b"
    r"|\b(find|get)\b.{0,25}\binformation\s+about\b"
    r"|\bsearch\b.{0,30}\b(latest|news)\b"
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
# System status (MISSION J.A.R.V.I.S.) — benign read-only phrases only.
# Destructive phrasing is deliberately never routed to the system tool; the
# classifier only ever builds {"action": "metrics"} args for this intent.
_SYSTEM_STATUS = re.compile(
    r"\bsystem\s+status\b"
    r"|\bcpu\s+usage\b"
    r"|\bmemory\s+usage\b"
    r"|\bdisk\s+usage\b"
    r"|\bhow\s+is\s+the\s+system\b"
    r"|\brun(ning)?\s+diagnostics\b"
    r"|\bdiagnostics\b",
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

# Conversation continuity — pronoun follow-ups ("why would I use it?",
# "tell me more about it") and recall of the current conversation ("what
# did we discuss earlier?"). Both need the request's conversation history.
# The follow-up only fires when the history yields a real topic; without
# one it falls through so we never invent an answer.
_FOLLOWUP_PRONOUN = re.compile(
    r"\b(it|that|this|they|them)\b.{0,30}\b(why|how|what|when|where|who)\b"
    r"|\b(why|how|what|when|where|who)\b.{0,30}\b(it|that|this|they|them)\b",
    re.IGNORECASE,
)
_FOLLOWUP_BARE = re.compile(
    r"^\s*(why\??|tell me more\.?|go on\.?|and then\??)\s*$", re.IGNORECASE
)
# "tell me more" with an optional pronoun tail, e.g. "tell me more about
# it" / "can you tell me more about that?" — conversational follow-ups,
# not standalone commands.
_FOLLOWUP_MORE = re.compile(
    r"\btell me more\b(\s+about\s+(it|that|this|them))?[\s.?!]*$",
    re.IGNORECASE,
)
_CONVERSATION_RECALL = re.compile(
    r"\bwhat did we (discuss|talk about)\b"
    r"|\bwhat were we talking about\b"
    r"|\bwhat have we (been )?(discussing|talking about|discussed)\b"
    r"|\bsummariz(e|ing)\b.{0,20}\b(our|this)\b.{0,20}\bconversation\b",
    re.IGNORECASE,
)

# Question scaffolding stripped to reveal the topic of a prior user turn
# ("What is RAG?" -> "RAG"; "remind me to call mom" -> "call mom").
_TOPIC_LEADERS = re.compile(
    r"^(?:what(?:'s|s)?|which|who|whom|whose|when|where|why|how|is|are|was|were"
    r"|do|does|did|can|could|should|would|will|shall|may|might|have|has|had"
    r"|tell me|explain|define|describe|remind me to|remind me|create a task"
    r"|add a task|search(?: the web)? for|google)\b\s*",
    re.IGNORECASE,
)
_TOPIC_ARTICLES = re.compile(r"^(?:the|a|an|my|this|that)\s+", re.IGNORECASE)


def _extract_topic(text: str) -> str:
    """Pull the key phrase from a user turn ("What is RAG?" -> "RAG").

    Used for follow-up and conversation-recall replies. Real data only: an
    empty string means no topic could be found and the caller must fall
    through instead of inventing one.
    """
    cleaned = re.sub(r"[?!.,;:]+$", "", (text or "").strip())
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _TOPIC_LEADERS.sub("", cleaned).strip()
        cleaned = _TOPIC_ARTICLES.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+(please|pls)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    # Bound the length so a long prior message can't hijack the reply.
    return cleaned[:80] if len(cleaned) >= 2 else ""


def _last_user_turn(history: list[dict] | None) -> str:
    for turn in reversed(history or []):
        if isinstance(turn, dict) and turn.get("role") == "user":
            content = str(turn.get("content") or "").strip()
            if content:
                return content
    return ""


class RuleBasedProvider(AIProvider):
    name = "rule_based"

    def _classify(self, text: str, history: list[dict] | None = None) -> dict:
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
        if _CONVERSATION_RECALL.search(text):
            # Answered from the request's conversation history (real data);
            # no tools, no memory writes.
            return {
                "intent": "conversation_recall",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
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
        if _TASK_CREATE_ADD.search(text):
            return {
                "intent": "task_create",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["tasks"],
                "response_mode": "confirm",
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
        if _SYSTEM_STATUS.search(text):
            return {
                "intent": "system_status",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["system"],
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
        if (
            _FOLLOWUP_PRONOUN.search(text)
            or _FOLLOWUP_BARE.search(text)
            or _FOLLOWUP_MORE.search(text)
        ):
            # Pronoun follow-up ("why would I use it?", "tell me more").
            # Only a real topic from the conversation history may ground the
            # reply; without one, fall through to greeting/help/chat_fallback
            # below instead of inventing an answer. Checked after every tool
            # intent so it never shadows a real command.
            topic = _extract_topic(_last_user_turn(history))
            if topic:
                return {
                    "intent": "chat_followup",
                    "requires_memory": False,
                    "requires_tools": False,
                    "tools": [],
                    "response_mode": "chat",
                    "topic": topic,
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

    async def aclassify_intent(
        self, text: str, history: list[dict] | None = None
    ) -> dict:
        return self._classify(text, history)

    async def agenerate(
        self, text: str, context: str = "", history: list[dict] | None = None
    ) -> str:
        if context:
            return context
        classification = self._classify(text, history)
        intent = classification["intent"]
        if intent == "conversation_recall":
            # Real data only: topics come from the request's history, never
            # invented. Cap at the 3 most recent user turns.
            topics = [
                t
                for t in (
                    _extract_topic(str(turn.get("content") or ""))
                    for turn in (history or [])
                    if isinstance(turn, dict) and turn.get("role") == "user"
                )
                if t
            ][-3:]
            if not topics:
                return (
                    "We haven't discussed anything yet in this conversation, sir."
                )
            return f"Earlier, sir, we discussed: {', then '.join(topics)}."
        if intent == "chat_followup":
            # The topic is guaranteed non-empty by _classify; the reply stays
            # honest and conversational with one concrete follow-up offer.
            topic = classification.get("topic") or _extract_topic(
                _last_user_turn(history)
            )
            return (
                f"On {topic}, sir — happy to go deeper on that. Shall I search "
                f"your documents for what they say about {topic}, or look it "
                "up on the web?"
            )
        if intent == "greeting":
            return (
                f"At your service, sir. {settings.persona_name} online "
                "and at your disposal."
            )
        if intent == "help":
            return (
                "Certainly, sir. I can calculate, remember things, manage "
                "tasks and reminders, search the web, search your uploaded "
                "documents, create documents and notes, explain code, analyze "
                "your resume, report system status, and chat. Try: "
                "'calculate 12 * 8', 'remind me to call mom tomorrow', "
                "'system status', 'search the web for quantum computing', or "
                "'explain this code: ...'."
            )
        return (
            "Understood, sir — though I shall need something more concrete. "
            "A calculation, something to remember, or a task, perhaps?"
        )
