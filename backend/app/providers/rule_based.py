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
    r"\bremind me to\b|\bremind me (in|at)\b"
    r"|\bremind me (tomorrow|today|tonight|next|on|every|each|morning|evening|afternoon|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\bset\s+(?:a\s+)?reminders?\b"
    r"|\byaad\s+dila(na|o|dena)?\b"
    r"|\breminders?\s+de\s+dena\b",
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
    r"|\bgoogle\b"
    # Person/entity questions need fresh facts, not model knowledge —
    # especially with a small local model that hallucinates biographies.
    r"|\bwho\s+(is|was|are|were)\b",
    re.IGNORECASE,
)
# Deep research: "research X", "deep dive into X" — multi-angle report.
_DEEP_RESEARCH = re.compile(
    r"\bdeep\s+dive\s+into\b"
    r"|\bresearch\b"
    r"|\binvestigate\b",
    re.IGNORECASE,
)
# Briefing: "brief me", "morning briefing", "daily briefing".
_BRIEFING = re.compile(
    r"\bbrief\s+me\b"
    r"|\bmorning\s+briefing\b"
    r"|\bdaily\s+briefing\b"
    r"|\bwhat's\s+on\s+(my\s+)?(schedule|agenda)\b",
    re.IGNORECASE,
)
# Command Center (MEW 2.0): "what should I work on", "what is most urgent".
_COMMAND_CENTER = re.compile(
    r"\bwhat\s+should\s+i\s+work\s+on\b"
    r"|\bwhat\s+is\s+most\s+urgent\b"
    r"|\bwhat\s+did\s+i\s+leave\s+unfinished\b"
    r"|\bwhat\s+should\s+i\s+do\s+next\b"
    r"|\bwhat\s+am\s+i\s+(currently\s+)?working\s+on\b"
    r"|\bmy\s+priorities\b",
    re.IGNORECASE,
)
# Goals (MEW 2.0): "I want to become better at X", "how am I progressing".
# Checked BEFORE _KNOWLEDGE_LEARN ("my goal is X" would match it).
_GOALS = re.compile(
    r"\bi\s+want\s+to\s+become\s+better\s+at\b"
    r"|\bmy\s+goal\s+is\b"
    r"|\badd\s+a\s+goal\b"
    r"|\bhow\s+am\s+i\s+progressing\b"
    r"|\bwhat\s+should\s+i\s+learn\s+next\b"
    r"|\bshow\s+my\s+goals\b",
    re.IGNORECASE,
)
# Research vault recall (MEW 2.0): "what did I research about X".
# Checked BEFORE _DEEP_RESEARCH ("research" alone would match it).
_RESEARCH_RECALL = re.compile(
    r"\bwhat\s+did\s+i\s+(find|research|learn)\s+about\b"
    r"|\bshow\s+my\s+research\s+on\b",
    re.IGNORECASE,
)
# Action history + undo (MEW 2.0): "what did you change", "undo that".
_ACTION_HISTORY = re.compile(
    r"\bwhat\s+did\s+you\s+change\b"
    r"|\bshow\s+(me\s+)?(the\s+)?action\s+history\b"
    r"|\bwhat\s+did\s+you\s+do\b",
    re.IGNORECASE,
)
_ACTION_UNDO = re.compile(
    r"\bundo\s+(that|the\s+last|it)\b"
    r"|\bundo\b",
    re.IGNORECASE,
)
# News: "top 5 news", "latest news", "headlines", "news today" —
# these don't always contain the word "search".
_NEWS = re.compile(
    r"\btop\s+\d+\s+news\b"
    r"|\blatest\s+news\b"
    r"|\bnews\s+today\b"
    r"|\btoday's\s+news\b"
    r"|\bheadlines\b"
    r"|\btell\s+me\s+(the\s+)?(top\s+|latest\s+)?news\b",
    re.IGNORECASE,
)
# News digest intent: category-flavored "give me today's AI news" /
# "send today's news" / "what's the latest in AI" requests want a
# multi-story digest from the dedicated news tool, not a generic search.
# Checked BEFORE _NEWS (which still handles "top 5 news", "latest news",
# "headlines" via the search tool).
_NEWS_DIGEST = re.compile(
    r"\bgive\s+me\s+(today'?s|the\s+latest)\s+([a-z]+\s+)?news\b"
    r"|\bsend\s+(me\s+)?(today'?s|the\s+latest)\s+news\b"
    r"|\bwhat'?s\s+the\s+latest\s+in\s+(ai|tech|technology|india|world|crypto|sports)\b",
    re.IGNORECASE,
)
# Wikipedia: explicit "using Wikipedia"/"on Wikipedia"/"wiki" requests.
# (Plain "who was X" stays web_search for fresher results.)
_WIKIPEDIA = re.compile(
    r"\bwikipedia\b"
    r"|\bwiki\b",
    re.IGNORECASE,
)
# Output generation (spec 16): "make a PDF", "create a docx", "generate a
# report", "make me a CSV". Must not swallow generate_prompt ("generate a
# prompt for X") — that check runs first in _classify.
_GENERATE_FILE = re.compile(
    r"\b(make|create|generate|build|export)\b.{0,30}\b(pdf|docx|csv)\b"
    r"|\b(make|create|generate)\b.{0,30}\b(report|presentation\s+outline)\b"
    r"|\bconvert\b.{0,20}\bto\s+(pdf|docx)\b",
    re.IGNORECASE,
)
# Compound plan (spec 3): "research X and make me a PDF/presentation".
# Checked before _DEEP_RESEARCH so the planner runs the full pipeline.
_RESEARCH_AND_GENERATE = re.compile(
    r"\bresearch\b.{0,60}\b(and|then)\b.{0,40}\b(make|create|generate|give\s+me)\b"
    r".{0,30}\b(pdf|presentation|report|docx|document)\b",
    re.IGNORECASE,
)
# Project intelligence (spec 8): "explain my project", "analyze this project".
_PROJECT_ANALYZE = re.compile(
    r"\b(explain|analyze|analyse|inspect|review)\b.{0,40}\b(my\s+|this\s+)?project\b"
    r"|\bwhere\s+is\b.{0,40}\bimplemented\b"
    r"|\bfind\s+(todos?|bugs?|unused\s+code)\b.{0,20}\b(in\s+my\s+project|in\s+this\s+project)?",
    re.IGNORECASE,
)
# Learning (spec 15): "teach me X", "quiz me", "I'm learning X".
_LEARN = re.compile(
    r"\bteach\s+me\b"
    r"|\bquiz\s+me\b"
    r"|\btest\s+me\s+on\b"
    r"|\bi('|’)?m\s+learning\b"
    r"|\blearning\s+progress\b",
    re.IGNORECASE,
)
# User corrections — "stop repeating", "that's wrong", "no, I meant X".
# These must NOT regenerate; they need acknowledgment + clarification.
# Checked early so a frustrated correction never becomes another bad answer.
_CORRECTION = re.compile(
    r"\bstop\s+(repeating|saying\s+that)\b"
    r"|\bthat's\s+wrong\b|\bthats\s+wrong\b"
    r"|\bno\s*,?\s*i\s+meant\b"
    r"|\byou'?re\s+repeating\b"
    r"|\bwrong\s+answer\b",
    re.IGNORECASE,
)
# Syllabus intelligence + academic planner. Checked BEFORE the generic
# task intents below ("mark DBMS Unit 2 complete" would otherwise match
# _TASK_COMPLETE's bare "mark ... complete" alternative).
_SYLLABUS_INGEST = re.compile(
    r"\b(ingest|import|process|add)\b.{0,40}\bsyllabus\b"
    r"|\bthis\s+is\s+my\s+syllabus\b"
    r"|\bhere'?s\s+my\s+syllabus\b"
    r"|\b(ingest|import)\b.{0,40}\b(exam\s+)?timetable\b",
    re.IGNORECASE,
)
_SYLLABUS_FETCH = re.compile(
    r"\b(fetch|download|get)\b.{0,40}\b(official\s+)?syllabus\b"
    r"|\bmy\s+syllabus\b.{0,20}\b(fetch|download|auto)\b",
    re.IGNORECASE,
)
_SYLLABUS_QUERY = re.compile(
    r"\bwhat\s+subjects\b"
    r"|\bsubjects\s+do\s+i\s+have\b"
    r"|\b(list|show)\b.{0,15}\b(my\s+)?subjects\b"
    r"|\bmy\s+subjects\b"
    r"|\bwhich\s+subjects\s+have\b"
    r"|\bterm\s*work\b"
    r"|\bpractical\s+(exam|subject)s?\b"
    r"|\boral\s+(exam|subject)s?\b"
    r"|\b(chapters?|units?|topics?)\s+(are\s+)?in\b"
    r"|\bunits?\s+for\b"
    r"|\bchapters?\s+of\b",
    re.IGNORECASE,
)
_SYLLABUS_EXAM = re.compile(
    r"\bwhen\s+is\s+(my\s+)?.{0,40}\bexam\b"
    r"|\bwhich\s+exam\s+is\s+next\b"
    r"|\bexam\s+(dates?|timetable|schedule|countdown)\b",
    re.IGNORECASE,
)
_STUDY_PLAN = re.compile(
    r"\bplan\s+my\s+(week|semester|day)\b"
    r"|\bwhat\s+should\s+i\s+study\b"
    r"|\bstudy\s+plan\b",
    re.IGNORECASE,
)
_STUDY_SESSION = re.compile(
    r"\bstart\s+(a\s+)?.{0,40}\bstudy\s+session\b",
    re.IGNORECASE,
)
_STUDY_UPDATE = re.compile(
    r"\bmark\b.{0,50}\bunit\b.{0,25}\bcomplete\b"
    r"|\bcomplete\b.{0,30}\bunit\b.{0,10}\d"
    r"|\bi\s+didn'?t\s+study\s+today\b"
    r"|\bmove\s+today'?s\b",
    re.IGNORECASE,
)
_SYLLABUS_PROGRESS = re.compile(
    r"\bhow\s+much\s+syllabus\b"
    r"|\bsyllabus\s+(is\s+)?(left|remaining|done|completed|complete)\b"
    r"|\bsyllabus\s+progress\b",
    re.IGNORECASE,
)
# Knowledge graph (spec 5): statements that assert facts + "what do you
# remember about my X".
_KNOWLEDGE_LEARN = re.compile(
    r"\bmy\s+(main\s+|current\s+)?(project|skill|skills|course|goal|interest|assignment)s?"
    r"\s+(is|are)\b"
    r"|\b[A-Z][\w\-+.]*\s+uses?\s+[\w\s]+(,|\band\b)",
    re.IGNORECASE,
)
_KNOWLEDGE_QUERY = re.compile(
    r"\bwhat\s+do\s+you\s+(know|remember)\s+about\s+my\b"
    r"|\blist\s+my\s+(projects|skills)\b",
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
_HELP = re.compile(
    r"\bhelp\b|what can you do|\bmadad\b"
    r"|kya kar sakte ho|tum kya kar sakte|aap kya kar sakte"
    r"|क्या कर सकते",
    re.IGNORECASE,
)
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
    r".{0,40}\b(my\s+)?(resume|cv)\b"
    r"|\b(improve|rewrite)\b.{0,30}\b(my\s+)?(summary|objective|experience|projects|skills)\b",
    re.IGNORECASE,
)
_RESUME_ANALYZE = re.compile(
    r"\b(analy[sz]e|analysis|check|review|audit|critique|feedback\s+on|look\s+at)\b"
    r".{0,40}\b(my\s+)?(resume|cv)\b"
    r"|\b(my\s+)?(resume|cv)\s+(analysis|review|feedback)\b"
    r"|\bwrong\s+with\b.{0,20}\b(my\s+)?(resume|cv)\b"
    r"|\bwhat\s+are\s+my\s+(weaknesses|strengths)\b",
    re.IGNORECASE,
)

# MEW Phase 2 — user profile ("Who am I?" answers from the stored profile;
# unknown fields are honestly reported as unknown, never invented).
_WHO_AM_I = re.compile(
    r"\bwho\s+am\s+i\b"
    r"|\bwhat('s| is)\s+my\s+name\b"
    r"|\btell\s+me\s+about\s+myself\b",
    re.IGNORECASE,
)
# MEW Phase 2 — memory forget. Bulk wipe ("forget everything") is a separate
# confirmation-gated intent; "don't forget ..." is a reminder phrasing, not
# a deletion, and is guarded out below.
_MEMORY_FORGET_ALL = re.compile(
    r"\bforget\s+(everything|all(\s+of\s+it)?)\b"
    r"|\bdelete\s+all\s+memories\b"
    r"|\bwipe\b.{0,20}\bmemory\b",
    re.IGNORECASE,
)
_DONT_FORGET = re.compile(r"\bdon'?t\s+forget\b", re.IGNORECASE)
_MEMORY_FORGET = re.compile(r"\bforget\s+(?:that\s+)?\S", re.IGNORECASE)
# "What do you remember about me?" lists the whole store (not a recall
# query — the generic phrasing would score nothing).
_MEMORY_LIST_ALL = re.compile(
    r"\bwhat\s+do\s+you\s+remember\s+about\s+me\b"
    r"|\b(list|show)\b.{0,20}\bmemories\b"
    r"|\bwhat\b.{0,15}\bmemories\b.{0,10}\b(do\s+you\s+)?have\b",
    re.IGNORECASE,
)
# MEW Phase 2 — conversational file management over conversation_attachments.
# "Forget this document" must beat the memory-forget pattern, so the delete
# check runs before it in _classify.
_ATTACHMENT_DELETE = re.compile(
    r"\bforget\s+this\s+(document|file|attachment)\b"
    r"|\bdelete\b.{0,30}\b(documents?|files?|attachments?)\b"
    r"|\bdelete\s+(?:the\s+)?([^\s]+\.\w{2,4})\b",
    re.IGNORECASE,
)
_ATTACHMENT_LIST = re.compile(
    r"\bwhat\s+files\s+have\s+i\s+uploaded\b"
    r"|\b(list|show)\b.{0,25}\b(uploaded|attached)\b.{0,10}\b(files?|documents?)\b"
    r"|\bmy\s+(uploaded|attached)\s+(files?|documents?)\b"
    r"|\bfiles\s+i('ve|\s+have)\s+attached\b",
    re.IGNORECASE,
)
_ATTACHMENT_SEARCH = re.compile(
    r"\bfind\b.{0,40}\b(papers?|documents?|files?|notes?)\b.{0,40}\bwhere\b"
    r"|\bwhere\s+did\s+i\s+mention\b"
    r"|\bwhich\s+(document|file)\s+mentions\b",
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
    r"\b(analy[sz]e|explain|summari[sz]e)\s+(this|that|it)\b(?!\s+code\b)"
    r"|\b(analy[sz]e|explain|summari[sz]e)\s+(the\s+|my\s+)?(document|file|pdf|paper|report|article)\b"
    r"|\bwhat\s+is\s+the\s+most\s+important\s+part\b"
    r"|\bwhat(?:'s|\s+is)\s+(this|that|it)\s+about\b",
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

# MEW capability upgrade — section-targeted resume follow-ups, no
# "resume"/"cv" word needed. "What's wrong with my projects section?" is
# analysis; "rewrite the second project" is a targeted improve.
_RESUME_SECTION_Q = re.compile(
    r"\b(what'?s|what\s+is)\s+(wrong|missing)\s+with\b.{0,40}"
    r"\b(projects?|experience|education|skills?|summary|objective)\b"
    r"|\b(review|critique|assess)\b.{0,25}\bmy\b.{0,25}"
    r"\b(projects?|experience|education|skills?|summary|objective)\b"
    r"(?!\s+(code|document|file)\b)",
    re.IGNORECASE,
)
_RESUME_ORDINAL_RE = re.compile(
    r"\b(improve|rewrite|fix|polish|strengthen)\b.{0,25}"
    r"\b(the|my)\s+(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+"
    r"(project|section|bullet)\b",
    re.IGNORECASE,
)
_SECTION_NOUN_RE = re.compile(
    r"\b(projects?|experience|education|skills?|summary|objective)\b",
    re.IGNORECASE,
)
_ORDINALS = {
    "first": 0, "1st": 0,
    "second": 1, "2nd": 1,
    "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3,
    "fifth": 4, "5th": 4,
}
_SECTION_CANONICAL = {
    "project": "projects", "projects": "projects",
    "experience": "experience",
    "education": "education",
    "skill": "skills", "skills": "skills",
    "summary": "summary",
    "objective": "summary",
}


def _resume_section_of(text: str) -> str | None:
    """Canonical section name mentioned in ``text`` (or None)."""
    m = _SECTION_NOUN_RE.search(text or "")
    if not m:
        return None
    return _SECTION_CANONICAL.get(m.group(1).lower())


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
        # User corrections come FIRST — a frustrated "stop repeating" must
        # never regenerate another bad answer.
        if _CORRECTION.search(text):
            return {
                "intent": "correction",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
                "response_mode": "answer",
            }
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
        # MEW capability upgrade — "rewrite the second project": targeted
        # improve of exactly one bullet in one section.
        m_ord = _RESUME_ORDINAL_RE.search(text)
        if m_ord:
            noun = m_ord.group(4).lower()
            ordinal = _ORDINALS.get(m_ord.group(3).lower())
            classification = {
                "intent": "resume_improve",
                "requires_memory": True,
                "requires_tools": True,
                "tools": ["resume"],
                "response_mode": "grounded",
                "resume_section": (
                    "projects" if noun == "project" else _resume_section_of(text)
                ),
            }
            if noun == "project":
                # The Nth PROJECT (entry), not the Nth bullet.
                classification["resume_entry"] = ordinal
            else:
                classification["resume_index"] = ordinal
            return classification
        # MEW capability upgrade — "what's wrong with my projects section?":
        # analysis focused on one section.
        if _RESUME_SECTION_Q.search(text):
            return {
                "intent": "resume_analyze",
                "requires_memory": True,
                "requires_tools": True,
                "tools": ["resume"],
                "response_mode": "grounded",
                "resume_section": _resume_section_of(text),
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
        # MEW Phase 2 — profile, memory forget/list, conversational file
        # management. "Forget this document" beats memory-forget (document
        # deletion, not a memory deletion); bulk memory wipe is its own
        # confirmation-gated intent.
        if _ATTACHMENT_DELETE.search(text):
            return {
                "intent": "attachment_delete",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["attachments"],
                "response_mode": "confirm",
            }
        if _WHO_AM_I.search(text):
            return {
                "intent": "who_am_i",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
                "response_mode": "answer",
            }
        if _MEMORY_FORGET_ALL.search(text):
            return {
                "intent": "memory_forget_all",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["memory"],
                "response_mode": "confirm",
            }
        if _MEMORY_FORGET.search(text) and not _DONT_FORGET.search(text):
            return {
                "intent": "memory_forget",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["memory"],
                "response_mode": "confirm",
            }
        if _MEMORY_LIST_ALL.search(text):
            return {
                "intent": "memory_list",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["memory"],
                "response_mode": "list",
            }
        if _ATTACHMENT_LIST.search(text):
            return {
                "intent": "attachment_list",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["attachments"],
                "response_mode": "list",
            }
        if _ATTACHMENT_SEARCH.search(text):
            return {
                "intent": "attachment_search",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["attachments"],
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
        # MEW upgrade — output generation (spec 16), knowledge graph (spec 5),
        # project intelligence (spec 8), learning (spec 15). Checked after
        # generate_prompt so "generate a prompt for X" keeps its route.
        if _RESEARCH_AND_GENERATE.search(text):
            return {
                "intent": "research_and_generate",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["research", "generate"],
                "response_mode": "answer",
            }
        if _GENERATE_FILE.search(text):
            return {
                "intent": "generate_file",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["generate"],
                "response_mode": "answer",
            }
        if _GOALS.search(text):
            return {
                "intent": "goals",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["goals"],
                "response_mode": "grounded",
            }
        if _KNOWLEDGE_QUERY.search(text):
            return {
                "intent": "knowledge_query",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["knowledge"],
                "response_mode": "answer",
            }
        if _KNOWLEDGE_LEARN.search(text) and not _REMEMBER.search(text):
            # "remember that my current project is X" stays the memory
            # intent; bare assertions ("My main project is X") feed the
            # knowledge graph.
            return {
                "intent": "knowledge_learn",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["knowledge"],
                "response_mode": "answer",
            }
        if _PROJECT_ANALYZE.search(text):
            return {
                "intent": "project_analyze",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["project"],
                "response_mode": "answer",
            }
        if _LEARN.search(text):
            return {
                "intent": "learning",
                "requires_memory": True,
                "requires_tools": True,
                "tools": ["learning"],
                "response_mode": "answer",
            }
        # Syllabus intelligence + academic planner. These sit above the
        # generic task intents so "mark DBMS Unit 2 complete" never becomes
        # a task completion and "plan my week" never becomes a task.
        if _SYLLABUS_FETCH.search(text):
            return {
                "intent": "syllabus_fetch",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["syllabus"],
                "response_mode": "answer",
            }
        if _SYLLABUS_INGEST.search(text):
            return {
                "intent": "syllabus_ingest",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["syllabus"],
                "response_mode": "answer",
            }
        if _STUDY_SESSION.search(text):
            return {
                "intent": "study_session",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["syllabus", "learning", "planner"],
                "response_mode": "answer",
            }
        if _STUDY_UPDATE.search(text):
            return {
                "intent": "study_update",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["planner"],
                "response_mode": "answer",
            }
        if _SYLLABUS_PROGRESS.search(text):
            return {
                "intent": "syllabus_progress",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["planner"],
                "response_mode": "answer",
            }
        if _STUDY_PLAN.search(text):
            return {
                "intent": "study_plan",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["planner"],
                "response_mode": "answer",
            }
        if _SYLLABUS_EXAM.search(text):
            return {
                "intent": "syllabus_exam",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["syllabus"],
                "response_mode": "answer",
            }
        if _SYLLABUS_QUERY.search(text):
            return {
                "intent": "syllabus_query",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["syllabus"],
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
        if _RESEARCH_RECALL.search(text):
            return {
                "intent": "research_recall",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["research"],
                "response_mode": "grounded",
            }
        if _DEEP_RESEARCH.search(text):
            return {
                "intent": "deep_research",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["research"],
                "response_mode": "answer",
            }
        if _COMMAND_CENTER.search(text):
            return {
                "intent": "command_center",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["command_center"],
                "response_mode": "grounded",
            }
        if _ACTION_UNDO.search(text):
            return {
                "intent": "action_undo",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["history"],
                "response_mode": "grounded",
            }
        if _ACTION_HISTORY.search(text):
            return {
                "intent": "action_history",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["history"],
                "response_mode": "grounded",
            }
        if _BRIEFING.search(text):
            return {
                "intent": "briefing",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["briefing"],
                "response_mode": "answer",
            }
        if _WIKIPEDIA.search(text):
            return {
                "intent": "wikipedia",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["wikipedia"],
                "response_mode": "answer",
            }
        if _NEWS_DIGEST.search(text):
            return {
                "intent": "news",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["news"],
                "response_mode": "answer",
            }
        if _NEWS.search(text):
            return {
                "intent": "web_search",
                "requires_memory": False,
                "requires_tools": True,
                "tools": ["search"],
                "response_mode": "answer",
                "news_mode": True,
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
        # Help/capability questions ("what can you do", "tum kya kar sakte
        # ho") are checked before greeting so "Hey MEW, what can you do?"
        # gets a capabilities answer instead of a plain hello.
        if _HELP.search(text):
            return {
                "intent": "help",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
                "response_mode": "help",
            }
        if _GREETING.search(text):
            return {
                "intent": "greeting",
                "requires_memory": False,
                "requires_tools": False,
                "tools": [],
                "response_mode": "greeting",
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
                    "We haven't discussed anything yet in this conversation.",
                    "Humne is conversation mein abhi kuch discuss nahi kiya hai.",
                    "हमने इस बातचीत में अभी कुछ चर्चा नहीं की है।",
                )
            joined = ", then ".join(topics)
            return pick(
                f"Earlier we discussed: {joined}.",
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
                f"On {topic} — happy to go deeper. Shall I search "
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
                "Hey! Main MEW hoon — bolo, kya kaam hai?",
                "नमस्ते! मैं MEW हूँ। आज मैं आपकी क्या मदद कर सकता हूँ?",
            )
        if intent == "help":
            return pick(
                "You bet — I can calculate, remember things, manage "
                "tasks and reminders, search the web, search your uploaded "
                "documents, create documents and notes, explain code, analyze "
                "your resume, report system status, understand your syllabus, "
                "and plan your studies. Try: "
                "'calculate 12 * 8', 'remind me to call mom tomorrow', "
                "'system status', 'what subjects do I have', 'plan my week', or "
                "'explain this code: ...'.",
                "Zaroor! Main calculation kar sakta hoon, cheezein yaad rakh "
                "sakta hoon, tasks aur reminders manage kar sakta hoon, web "
                "search kar sakta hoon, aapke documents mein dhoondh sakta "
                "hoon, notes bana sakta hoon, code samjha sakta hoon, resume "
                "analyze kar sakta hoon, aur system status bata sakta hoon. "
                "Aapka syllabus bhi samajh sakta hoon aur study plan bana "
                "sakta hoon. "
                "Try karo: '12 * 8 kitna hai', 'kal 9 baje mujhe assignment "
                "yaad dila dena', ya 'mere tasks dikhao'.",
                "ज़रूर! मैं गणना कर सकता हूँ, बातें याद रख सकता हूँ, टास्क और "
                "रिमाइंडर संभाल सकता हूँ, वेब पर खोज सकता हूँ, आपके दस्तावेज़ों "
                "में खोज सकता हूँ, नोट्स बना सकता हूँ, कोड समझा सकता हूँ, "
                "रिज़्यूमे जाँच सकता हूँ और सिस्टम स्थिति बता सकता हूँ। "
                "आपका पाठ्यक्रम भी समझ सकता हूँ और अध्ययन योजना बना सकता हूँ।",
            )
        if _DEFINITION_ASK.search(text):
            # Honest fallback for knowledge questions the rule-based provider
            # cannot answer itself: name the topic and offer a real search.
            topic = _extract_topic(text)
            if topic:
                return pick(
                    f"I don't have that in my built-in knowledge — shall "
                    f"I search the web for '{topic}'? Just say the word.",
                    f"Yeh mere built-in knowledge mein nahi hai — kya main web "
                    f"par '{topic}' search karoon? Bas bolo.",
                    f"यह मेरी जानकारी में नहीं है — क्या मैं वेब पर '{topic}' "
                    "खोजूँ? बस कहिए।",
                )
        # Smart fallback: try to be useful instead of the generic
        # "something more concrete". Detect question-like input and offer
        # a web search; detect frustration and be empathetic.
        if re.search(r"\b(argh+|ugh+|damn|stupid|useless)\b", text, re.IGNORECASE):
            return pick(
                "Hey, I hear you — I'm running in limited mode right now "
                "(no AI model connected). I can still search the web, manage "
                "tasks and reminders, and do calculations. What do you need?",
                "Samajh gaya, thoda frustrating hai — main abhi limited mode "
                "mein hoon. Phir bhi web search, tasks, reminders aur "
                "calculation kar sakta hoon. Bolo, kya chahiye?",
                "समझ गया — मैं अभी सीमित मोड में हूँ। फिर भी वेब खोज, कार्य, "
                "रिमाइंडर और गणना कर सकता हूँ। बताइए, क्या चाहिए?",
            )
        # Question-like but unclassified: offer web search honestly.
        if text.strip().endswith("?") or re.search(
            r"\b(what|who|when|where|why|how|which|tell\s+me|show\s+me)\b",
            text,
            re.IGNORECASE,
        ):
            topic = _extract_topic(text) or text.strip()[:60]
            return pick(
                f"That's a good question. I can search the web for '{topic}' — "
                f"just say 'search the web for {topic}'.",
                f"Achha sawal hai. Main web par '{topic}' search kar sakta "
                f"hoon — bas bolo 'search the web for {topic}'.",
                f"अच्छा सवाल है। मैं वेब पर '{topic}' खोज सकता हूँ — बस कहिए "
                f"'search the web for {topic}'।",
            )
        return pick(
            "Got it — though I'll need something more concrete. "
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
