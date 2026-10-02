import re

from app.config import settings
from app.providers.base import AIProvider
from app.services.language import detect_language, split_sentences

_REMEMBER = re.compile(
    r"\bremember\s+(?:that\s+)?(.+)|\byaad\s+rakh(na|o)?\b", re.IGNORECASE
)
_RECALL = re.compile(
    r"\b(what do you know|what am i learning|what.*\bremember\b|do you remember|recall\b)"
    r"|\bkya\s+yaad\s+hai\b|\btumhe\s+kya\s+yaad\b",
    re.IGNORECASE,
)
_TASK_CREATE = re.compile(
    r"\b((create|add)\s+(a\s+)?task|todo\b)", re.IGNORECASE
)
# Natural phrasing "add <thing> to my tasks" — checked BEFORE the
# delete/complete intents because "finish" in the task title would otherwise
# trip _TASK_COMPLETE ("finish ... tasks").
_TASK_CREATE_ADD = re.compile(r"\badd\b.{0,60}\bto my tasks?\b", re.IGNORECASE)
# Hindi/Hinglish task phrasing: "ek kaam add karo", "naya task banao".
# Scoped to an action verb near kaam/task so a bare "kya kaam hai" does not
# misfire into task creation.
_TASK_CREATE_HI = re.compile(
    r"\b(kaam|task)\b.{0,25}\b(add|karo|karna|banao|create)\b"
    r"|\b(add|banao)\b.{0,25}\b(kaam|task)\b",
    re.IGNORECASE,
)
_TASK_LIST = re.compile(
    r"\b(list|show)\b.{0,20}\btasks?\b|\bmy tasks\b"
    r"|\bmere\s+tasks?\b|\btasks?\s+hain\b",
    re.IGNORECASE,
)
# Phase 5 intents. "remind me to" used to route to task_create; it now owns
# reminders. These are checked BEFORE the generic task intents below.
_REMINDER_CREATE = re.compile(
    r"\bremind me to\b|\bremind me (in|at)\b|\bset\s+(?:a\s+)?reminders?\b"
    r"|\byaad\s+dila(na|o|dena)?\b",
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
    r"\b(list|show)\b.{0,20}\breminders?\b|\bmy reminders?\b|\bmere\s+reminders?\b",
    re.IGNORECASE,
)
_TASK_DELETE = re.compile(
    r"\b(delete|remove|drop|cancel)\b.{0,30}\btasks?\b"
    r"|\btasks?\b.{0,20}\b(delete|remove|drop)\b",
    re.IGNORECASE,
)
_TASK_COMPLETE = re.compile(
    r"\b(complete|finish|mark)\b.{0,30}\btasks?\b"
    r"|\btasks?\b.{0,20}\b(done|completed)\b"
    r"|\bmark\b.{0,50}\bcomplete\b"
    r"|\bkaam\s+(ho\s+gaya|complete)\b",
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
# System status (MISSION MEW) — benign read-only phrases only.
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
# Guard for the calculate intent: "what is"/"what's" alone must not route
# plain English questions ("What is RAG?") to the calculator. The intent
# only fires when the message also carries a digit or an explicit math word.
_HAS_MATH = re.compile(
    r"\d"
    r"|\b(plus|minus|times|divided(\s+by)?|multiplied(\s+by)?|modulo|percent|"
    r"percentage|square\s+root|power\s+of)\b",
    re.IGNORECASE,
)
# "What is X?" with no math: the rule-based provider has no built-in answer,
# so the fallback offers a real web search instead of failing or inventing.
_DEFINITION_ASK = re.compile(
    r"\bwhat\s+is\b|\bwhat's\b|\bwho\s+is\b|\bdefine\b", re.IGNORECASE
)
_GREETING = re.compile(
    r"\b(hello|hi|hey|good morning|good afternoon|good evening|namaste)\b"
    r"|नमस्ते",
    re.IGNORECASE,
)
_HELP = re.compile(r"\bhelp\b|what can you do|\bmadad\b", re.IGNORECASE)
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

# MEW upgrade — attachment/resume-as-context intents. "analyze this" /
# "explain this" / "summarize this" resolve against the conversation's
# attached files (document_qa); resume-targeted phrasing without the word
# "resume" routes to the existing resume tool. Checked right after the
# explicit resume patterns (so "analyze my resume" still wins) and before
# the generic intents below. The negative lookahead keeps "explain this
# code" and "summarize this document" on their existing intents.
_DOCUMENT_QA = re.compile(
    r"\b(analy[sz]e|explain|summari[sz]e)\s+(this|that|it)\b"
    r"(?!\s+(code|document|file)\b)"
    r"|\bwhat\s+is\s+the\s+most\s+important\s+part\b",
    re.IGNORECASE,
)
# Section-targeted resume phrasing, no "resume"/"cv" word needed. Kept
# narrow (section nouns, ATS, shorter) so a generic "improve this" stays
# document_qa and never wanders into the resume pipeline.
_RESUME_SECTION = re.compile(
    r"\b(improve|fix|rewrite|strengthen|polish)\b.{0,30}"
    r"\b(projects?|experience|education|skills?|summary|objective)\b.{0,15}"
    r"\bsections?\b"
    r"|\b(improve|fix|rewrite|strengthen|polish)\b.{0,20}"
    r"\b(first|second|third|1st|2nd|3rd)\s+section\b"
    r"|\bmake\s+it\s+(ats[-\s]?friendly|shorter|more\s+professional)\b"
    r"|\bats[-\s]?friendly\b",
    re.IGNORECASE,
)
# Hindi/Hinglish resume phrasing: "isko better bana do" / "is section ko
# professional bana do". Placed before _TASK_CREATE_HI (which looks for
# banao/add near kaam/task) so these never misfire into task creation.
_RESUME_SECTION_HI = re.compile(
    r"\bisko\s+better\s+bana\s+do\b"
    r"|\bis\s+section\s+ko\s+professional\s+bana\s+do\b",
    re.IGNORECASE,
)
_RESUME_SAVE_VERSION = re.compile(
    r"\bsave\s+(that|this)\s+version\b", re.IGNORECASE
)
_RESUME_SHOW_LATEST = re.compile(
    r"\bshow\s+me\s+the\s+(final|latest)\s+version\b"
    r"|\bwhat'?s\s+the\s+(final|latest)\s+version\b",
    re.IGNORECASE,
)
# MEW upgrade — prompt generation. Three modes are distinguished:
#   "build this for me" / "create a todo app for me" -> execute tools
#   "give me a prompt (for X)" -> generate_prompt (this pattern)
#   "explain how to build X" / "kaise banau" -> explanation (chat path)
# The pattern only fires when the word "prompt" names the deliverable, so
# the build/explain phrasings above never match it.
_GENERATE_PROMPT = re.compile(
    r"\bprompt\b.{0,40}\b(build|create|make|write|generate|draft|for)\b"
    r"|\b(build|create|make|write|generate|draft)\b.{0,40}\bprompt\b"
    r"|\bprompt\s+bana\s+do\b",
    re.IGNORECASE,
)

# MEW upgrade — "explain how to build X" / "kaise banau" is an explanation
# request, never a build command: it must not fall into task_create via a
# stray "todo", and it must never trigger generate_prompt. Checked before
# the task intents; it resolves to the existing chat path.
_EXPLAIN_HOW = re.compile(
    r"\bexplain\s+how\s+to\b"
    r"|\bhow\s+do\s+i\s+build\b"
    r"|\bkaise\s+banau\b",
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
        # MEW upgrade — attachment / resume-as-context / prompt intents.
        if _DOCUMENT_QA.search(text):
            return {
                "intent": "document_qa",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["rag"],
                "response_mode": "grounded",
            }
        if _RESUME_SECTION.search(text) or _RESUME_SECTION_HI.search(text):
            return {
                "intent": "resume_improve",
                "requires_memory": True,
                "requires_tools": True,
                "tools": ["resume"],
                "response_mode": "grounded",
            }
        if _RESUME_SAVE_VERSION.search(text):
            return {
                "intent": "resume_save_version",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["resume"],
                "response_mode": "confirm",
            }
        if _RESUME_SHOW_LATEST.search(text):
            return {
                "intent": "resume_show_latest",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["resume"],
                "response_mode": "answer",
            }
        if _GENERATE_PROMPT.search(text):
            return {
                "intent": "generate_prompt",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
                "response_mode": "answer",
            }
        if _EXPLAIN_HOW.search(text):
            # Explanation request, not a build: the existing chat path.
            return {
                "intent": "chat_fallback",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
                "response_mode": "chat",
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
        if _TASK_CREATE_HI.search(text):
            # Hindi/Hinglish "ek kaam add karo" / "naya task banao".
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
        if _CALCULATE.search(text) and _HAS_MATH.search(text):
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
        self, text: str, history: list[dict] | None = None, lang: str | None = None
    ) -> dict:
        # Intent labels are language-agnostic; ``lang`` is accepted for API
        # symmetry with the other providers and currently unused.
        return self._classify(text, history)

    @staticmethod
    def _pick(lang: str, en: str, hinglish: str, hi: str) -> str:
        """Response template for the reply language (never translated across)."""
        if lang == "hi":
            return hi
        if lang == "hinglish":
            return hinglish
        return en

    async def agenerate(
        self,
        text: str,
        context: str = "",
        history: list[dict] | None = None,
        lang: str | None = None,
    ) -> str:
        if context:
            return context
        lang = lang or detect_language(text)
        classification = self._classify(text, history)
        intent = classification["intent"]
        pick = lambda en, hinglish, hi: self._pick(lang, en, hinglish, hi)
        if intent == "conversation_recall":
            # Real data only: topics come from the request's history, never
            # invented. Cap at the 3 most recent user turns. Follow-up turns
            # ("Why would I use it?") carry no topic of their own, so they
            # are skipped rather than listed as "I use it".
            topics: list[str] = []
            for turn in history or []:
                if not (isinstance(turn, dict) and turn.get("role") == "user"):
                    continue
                content = str(turn.get("content") or "")
                if (
                    _FOLLOWUP_PRONOUN.search(content)
                    or _FOLLOWUP_BARE.search(content)
                    or _FOLLOWUP_MORE.search(content)
                ):
                    continue
                t = _extract_topic(content)
                if t:
                    topics.append(t)
            topics = topics[-3:]
            topics = [
                t
                for i, t in enumerate(topics)
                if i == 0 or t.lower() != topics[i - 1].lower()
            ]
            if not topics:
                return pick(
                    "We haven't discussed anything yet in this conversation, sir.",
                    "Humne is conversation mein abhi kuch discuss nahi kiya hai.",
                    "हमने इस बातचीत में अभी कुछ चर्चा नहीं की है।",
                )
            joined = ", then ".join(topics)
            return pick(
                f"Earlier, sir, we discussed: {joined}.",
                f"Pehle humne in baaton par discuss kiya: {joined}.",
                f"पहले हमने इन विषयों पर चर्चा की: {joined}।",
            )
        if intent == "chat_followup":
            # The topic is guaranteed non-empty by _classify; the reply stays
            # honest and conversational with one concrete follow-up offer.
            topic = classification.get("topic") or _extract_topic(
                _last_user_turn(history)
            )
            return pick(
                f"On {topic}, sir — happy to go deeper on that. Shall I search "
                f"your documents for what they say about {topic}, or look it "
                "up on the web?",
                f"{topic} ke baare mein — khushi se aur detail mein bata sakta "
                f"hoon. Kya main aapke documents mein '{topic}' dhoondhoon, ya "
                "web par search karoon?",
                f"{topic} के बारे में — खुशी से और विस्तार से बता सकता हूँ। "
                f"क्या मैं आपके दस्तावेज़ों में '{topic}' खोजूँ, या वेब पर देखूँ?",
            )
        if intent == "greeting":
            return pick(
                f"Hey! {settings.persona_name} here — ready when you are.",
                "Hey! Main Spidey hoon — bolo, kya kaam hai?",
                "नमस्ते! मैं स्पाइडी हूँ। आज मैं आपकी क्या मदद कर सकता हूँ?",
            )
        if intent == "help":
            return pick(
                "Certainly, sir. I can calculate, remember things, manage "
                "tasks and reminders, search the web, search your uploaded "
                "documents, create documents and notes, explain code, analyze "
                "your resume, report system status, and chat. Try: "
                "'calculate 12 * 8', 'remind me to call mom tomorrow', "
                "'system status', 'search the web for quantum computing', or "
                "'explain this code: ...'.",
                "Zaroor! Main calculation kar sakta hoon, cheezein yaad rakh "
                "sakta hoon, tasks aur reminders manage kar sakta hoon, web "
                "search kar sakta hoon, aapke documents mein dhoondh sakta "
                "hoon, notes bana sakta hoon, code samjha sakta hoon, resume "
                "analyze kar sakta hoon, aur system status bata sakta hoon. "
                "Try karo: '12 * 8 kitna hai', 'kal 9 baje mujhe assignment "
                "yaad dila dena', ya 'mere tasks dikhao'.",
                "ज़रूर! मैं गणना कर सकता हूँ, बातें याद रख सकता हूँ, टास्क और "
                "रिमाइंडर संभाल सकता हूँ, वेब पर खोज सकता हूँ, आपके दस्तावेज़ों "
                "में खोज सकता हूँ, नोट्स बना सकता हूँ, कोड समझा सकता हूँ, "
                "रिज़्यूमे जाँच सकता हूँ और सिस्टम स्थिति बता सकता हूँ।",
            )
        if _DEFINITION_ASK.search(text):
            # Honest fallback for knowledge questions the rule-based provider
            # cannot answer itself: name the topic and offer a real search.
            topic = _extract_topic(text)
            if topic:
                return pick(
                    f"I don't have that in my built-in knowledge, sir — shall "
                    f"I search the web for '{topic}'? Just say the word.",
                    f"Yeh mere built-in knowledge mein nahi hai — kya main web "
                    f"par '{topic}' search karoon? Bas bolo.",
                    f"यह मेरी जानकारी में नहीं है — क्या मैं वेब पर '{topic}' "
                    "खोजूँ? बस कहिए।",
                )
        return pick(
            "Understood, sir — though I shall need something more concrete. "
            "A calculation, something to remember, or a task, perhaps?",
            "Samajh gaya — lekin thoda aur detail chahiye. Koi calculation, "
            "yaad rakhne wali baat, ya koi task?",
            "समझ गया — लेकिन थोड़ा और विस्तार चाहिए। कोई गणना, याद रखने "
            "वाली बात, या कोई कार्य?",
        )

    async def agenerate_stream(
        self,
        message: str,
        context: str = "",
        history: list[dict] | None = None,
        lang: str | None = None,
    ):
        """Compose via the existing rule-based logic, then yield sentence
        chunks (split on ./!/?/। boundaries, punctuation kept). No timers."""
        text = await self.agenerate(
            message, context=context, history=history, lang=lang
        )
        for chunk in split_sentences(text):
            yield chunk
