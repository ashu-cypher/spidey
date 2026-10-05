"""Syllabus Intelligence + Academic Planner.

Deterministic extraction from synthetic academic texts, ingest -> query
round trips, planning honoring available hours, progress updates, and
missed-day rebalancing without duplication. Plus router intent checks for
the new syllabus/planner phrasings.
"""
from datetime import date, timedelta

import pytest

from app.agents.spidey_agent import SpideyAgent
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.tools.planner import PlannerTool
from app.tools.syllabus import (
    SyllabusTool,
    detect_doc_type,
    extract_notice,
    extract_syllabus,
    extract_timetable,
    find_subject,
)
from app.workflows.engine import WorkflowEngine

SYLLABUS_TEXT = """SAVITRIBAI PHULE PUNE UNIVERSITY
B.E. Computer Engineering - Semester 6 (2026-27)

210254: Database Management Systems
Credits: 4
Examination Scheme: In-Sem 30 | End-Sem 70 | Term Work 25 | Practical 50
Unit 1: Introduction to Databases
- DBMS vs file systems
- Data models: ER and relational
Unit 2: SQL and Relational Algebra
- DDL and DML statements
- Joins and subqueries
- Normalization: 1NF to 3NF
Unit 3: Transactions and Concurrency
- ACID properties
- Locking protocols
Unit 4: NoSQL and New Trends
- Document stores
- CAP theorem

210255: Artificial Intelligence
Credits: 4
Examination Scheme: In-Sem 30 | End-Sem 70 | Term Work 25
Unit 1: Introduction to AI
- Intelligent agents
- Search: BFS, DFS, A*
Unit 2: Knowledge Representation
- Propositional logic
- First-order logic
Unit 3: Pattern Recognition Basics
- Supervised vs unsupervised methods
- Linear regression

210256: Computer Networks Lab
Credits: 2
Examination Scheme: Term Work 50 | Practical 50 | Oral 25
Practical list:
- Configure IP addressing and subnetting
- Socket programming: TCP client-server
"""


def _timetable_text() -> str:
    d1 = (date.today() + timedelta(days=30)).strftime("%d/%m/%Y")
    d2 = (date.today() + timedelta(days=37)).strftime("%d/%m/%Y")
    d3 = (date.today() + timedelta(days=44)).strftime("%d/%m/%Y")
    return (
        "SPPU Examination Timetable - Semester 6\n"
        "Date | Subject | Type | Time\n"
        f"{d1} | Database Management Systems (210254) | Theory | 10:00 AM\n"
        f"{d2} | Artificial Intelligence (210255) | Theory | 10:00 AM\n"
        f"{d3} | Computer Networks Lab (210256) | Practical | 9:00 AM\n"
    )


NOTICE_TEXT = """Department Notice - Assignment Submission
This is to inform all students that the DBMS assignment must be submitted.
Last date of submission: 20/12/2026
AI mini-project submission deadline: 22/12/2026
"""


# ---------------------------------------------------------------------------
# Deterministic extraction
# ---------------------------------------------------------------------------

def test_detect_doc_types():
    assert detect_doc_type(SYLLABUS_TEXT) == "syllabus"
    assert detect_doc_type(_timetable_text()) == "timetable"
    assert detect_doc_type(NOTICE_TEXT) == "notice"


def test_extract_syllabus_subjects_and_meta():
    doc = extract_syllabus(SYLLABUS_TEXT, "syllabus.pdf")
    assert doc["derived_by"] == "deterministic"
    assert doc["university"] == "SAVITRIBAI PHULE PUNE UNIVERSITY"
    assert "Computer Engineering" in (doc["course"] or "")
    assert doc["semester"] == "6"
    assert doc["academic_year"] == "2026-27"
    names = [s["name"] for s in doc["subjects"]]
    assert names == [
        "Database Management Systems",
        "Artificial Intelligence",
        "Computer Networks Lab",
    ]
    codes = [s["code"] for s in doc["subjects"]]
    assert codes == ["210254", "210255", "210256"]


def test_extract_syllabus_units_and_topics():
    doc = extract_syllabus(SYLLABUS_TEXT, "syllabus.pdf")
    dbms = doc["subjects"][0]
    assert len(dbms["units"]) == 4
    assert dbms["units"][0]["title"] == "Introduction to Databases"
    assert "ACID properties" in dbms["units"][2]["topics"]
    ai = doc["subjects"][1]
    assert len(ai["units"]) == 3
    assert ai["credits"] == 4.0


def test_extract_term_work_practical_oral_detection():
    doc = extract_syllabus(SYLLABUS_TEXT, "syllabus.pdf")
    dbms, ai, lab = doc["subjects"]
    assert dbms["has_term_work"] is True
    assert dbms["has_practical"] is True
    assert dbms["marks_distribution"]["term_work"] == 25
    assert dbms["marks_distribution"]["practical"] == 50
    assert ai["has_term_work"] is True
    assert ai["has_practical"] is False
    assert lab["has_practical"] is True
    assert lab["has_oral"] is True
    assert "practical" in lab["exam_types"]
    assert "theory" in dbms["exam_types"]


def test_extract_timetable_dates():
    doc = extract_timetable(_timetable_text(), "timetable.pdf")
    assert len(doc["exam_dates"]) == 3
    first = doc["exam_dates"][0]
    assert first["subject"] == "Database Management Systems"
    assert first["type"] == "theory"
    assert first["date"] == (date.today() + timedelta(days=30)).isoformat()
    assert doc["exam_dates"][2]["type"] == "practical"


def test_extract_notice_deadlines():
    doc = extract_notice(NOTICE_TEXT, "notice.pdf")
    assert len(doc["deadlines"]) == 2
    assert doc["deadlines"][0]["date"] == "2026-12-20"


def test_extraction_never_invents():
    doc = extract_syllabus("hello world, this is not a syllabus at all", "x.pdf")
    assert doc["subjects"] == []
    assert doc["university"] is None
    assert doc["course"] is None


# ---------------------------------------------------------------------------
# Ingest -> query round trip (DB-backed)
# ---------------------------------------------------------------------------

@pytest.fixture
def ingested():
    return None


_INGESTED = False


async def _ingest_all():
    """Ingest the fixture documents once per module: the memory store is
    shared across the whole test session, so repeated ingests would pile
    up duplicate records (and crowd other tests' recall queries)."""
    global _INGESTED
    if _INGESTED:
        return
    tool = SyllabusTool()
    await tool.execute(
        action="ingest", document_text=SYLLABUS_TEXT,
        source_name="test-syllabus.pdf",
    )
    await tool.execute(
        action="ingest", document_text=_timetable_text(),
        source_name="test-timetable.pdf",
    )
    await tool.execute(
        action="ingest", document_text=NOTICE_TEXT,
        source_name="test-notice.pdf",
    )
    _INGESTED = True


async def test_ingest_and_subjects_round_trip():
    await _ingest_all()
    tool = SyllabusTool()
    out = await tool.execute(action="subjects")
    names = [s["name"] for s in out["subjects"]]
    assert "Database Management Systems" in names
    assert "Artificial Intelligence" in names
    assert all(s["source"] == "test-syllabus.pdf" for s in out["subjects"])


async def test_units_for_subject_with_abbreviation():
    await _ingest_all()
    tool = SyllabusTool()
    # "AI" must resolve to Artificial Intelligence via acronym matching.
    out = await tool.execute(action="units", subject="AI")
    assert out["units"]["subject"] == "Artificial Intelligence"
    assert len(out["units"]["units"]) == 3
    assert out["units"]["source"] == "test-syllabus.pdf"


async def test_term_work_and_practical_queries():
    await _ingest_all()
    tool = SyllabusTool()
    tw = await tool.execute(action="term_work")
    tw_names = [s["name"] for s in tw["term_work_subjects"]]
    assert "Database Management Systems" in tw_names
    assert "Artificial Intelligence" in tw_names
    pr = await tool.execute(action="practicals")
    pr_names = [s["name"] for s in pr["practical_subjects"]]
    assert "Database Management Systems" in pr_names
    assert "Computer Networks Lab" in pr_names
    assert "Artificial Intelligence" not in pr_names


async def test_exams_cross_reference_subjects():
    await _ingest_all()
    tool = SyllabusTool()
    out = await tool.execute(action="exams")
    by_name = {e["subject"]: e for e in out["exams"]}
    assert by_name["Database Management Systems"]["code"] == "210254"
    assert by_name["Database Management Systems"]["days_left"] == 30
    # Subject-scoped query.
    scoped = await tool.execute(action="exams", subject="DBMS")
    assert len(scoped["exams"]) == 1
    assert scoped["exams"][0]["subject"] == "Database Management Systems"


async def test_academic_timeline_combines_documents():
    await _ingest_all()
    tool = SyllabusTool()
    out = await tool.execute(action="timeline")
    tl = out["timeline"]
    assert len(tl["subjects"]) == 3
    assert len(tl["exams"]) == 3
    assert len(tl["deadlines"]) == 2
    assert set(tl["sources"]) == {
        "test-syllabus.pdf", "test-timetable.pdf", "test-notice.pdf",
    }


async def test_find_subject_unknown_returns_none():
    subs = [{"name": "Database Management Systems", "code": "210254"}]
    assert find_subject("Quantum Physics", subs) is None
    assert find_subject("dbms", subs)["name"] == "Database Management Systems"


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

async def test_plan_week_respects_available_hours():
    await _ingest_all()
    tool = PlannerTool()
    out = await tool.execute(
        action="plan_week", available_hours_per_day=1.0,
        start_date=date.today().isoformat(),
    )
    plan = out["plan"]
    assert plan["hours_per_day"] == 1.0
    for day in plan["days"]:
        assert day["total_minutes"] <= 60, f"over budget: {day}"
    # All 7 plannable units (4 DBMS + 3 AI; the lab has no units) scheduled.
    total_sessions = sum(len(d["sessions"]) for d in plan["days"])
    assert total_sessions == 7


async def test_plan_week_default_hours_assumption_stated():
    await _ingest_all()
    tool = PlannerTool()
    out = await tool.execute(action="plan_week")
    plan = out["plan"]
    assert plan["hours_assumed"] is True
    assert plan["hours_per_day"] == 2.0
    for day in plan["days"]:
        assert day["total_minutes"] <= 120


async def test_mark_complete_updates_progress():
    await _ingest_all()
    tool = PlannerTool()
    await tool.execute(action="plan_week")
    updated = await tool.execute(
        action="mark_complete", subject="DBMS", unit="2",
    )
    assert updated["updated"]["subject"] == "Database Management Systems"
    assert updated["updated"]["unit"] == "2"
    prog = updated["updated"]["progress"]
    assert prog["completed"] == 1 and prog["total"] == 4
    assert prog["percent"] == 25
    report = await tool.execute(action="progress")
    dbms = next(
        r for r in report["progress"]["subjects"]
        if r["subject"] == "Database Management Systems"
    )
    assert dbms["completed"] == 1 and dbms["percent"] == 25
    assert report["progress"]["overall"]["total"] == 7


async def test_mark_complete_unknown_unit_is_honest():
    await _ingest_all()
    tool = PlannerTool()
    with pytest.raises(Exception) as exc:
        await tool.execute(action="mark_complete", subject="DBMS", unit="9")
    assert "isn't in" in str(exc.value)


async def test_missed_today_moves_without_duplication():
    await _ingest_all()
    tool = PlannerTool()
    before = await tool.execute(
        action="plan_week", start_date=date.today().isoformat(),
    )
    moved = await tool.execute(action="missed_today")
    assert moved["moved"]["moved"], "expected today's sessions to move"
    today = date.today().isoformat()
    for m in moved["moved"]["moved"]:
        assert m["from"] == today
        assert m["to"] > today
    # No duplicate session ids in the stored plan, and nothing left
    # pending today.
    from app.tools import planner as planner_mod
    plan = await planner_mod._load_plan()
    ids = [s["id"] for s in plan["sessions"]]
    assert len(ids) == len(set(ids)), "duplicate session ids!"
    assert not [
        s for s in plan["sessions"]
        if s["date"] == today and s["status"] == "pending"
    ]


async def test_priority_explains_reasoning():
    await _ingest_all()
    tool = PlannerTool()
    out = await tool.execute(action="priority")
    prio = out["priority"]
    # DBMS exam is closest (30 days) -> highest priority.
    assert prio["subject"] == "Database Management Systems"
    assert "closest" in prio["reasoning"]
    assert "Unit" in prio["reasoning"]
    assert prio["source"] == "test-syllabus.pdf"


async def test_exam_countdown():
    await _ingest_all()
    tool = PlannerTool()
    out = await tool.execute(action="countdown")
    exams = out["countdown"]["exams"]
    assert [e["days_left"] for e in exams] == [30, 37, 44]
    assert exams[0]["subject"] == "Database Management Systems"


async def test_progress_honest_when_empty(monkeypatch):
    # Simulate no syllabus data: point load_documents at an empty store by
    # ingesting nothing new — instead call the pure path via a fresh tool
    # against an empty-memory double.
    tool = PlannerTool()
    out = await tool.execute(action="progress")
    # The shared test DB already has syllabus data from earlier tests, so
    # this asserts the shape, not emptiness; emptiness is covered by the
    # syllabus tool raising ToolError with no data (see below).
    assert "subjects" in out["progress"]


async def test_syllabus_tool_honest_without_data():
    from app.tools import memory_tool as mt

    # Temporarily break recall to simulate an empty store.
    real = mt.MemoryTool._recall

    def empty_recall(self, kwargs):
        return {"results": []}

    mt.MemoryTool._recall = empty_recall
    try:
        tool = SyllabusTool()
        with pytest.raises(Exception) as exc:
            await tool.execute(action="subjects")
        assert "No syllabus data yet" in str(exc.value)
        planner = PlannerTool()
        out = await planner.execute(action="progress")
        assert "No syllabus data yet" in out["progress"]["message"]
    finally:
        mt.MemoryTool._recall = real


# ---------------------------------------------------------------------------
# Router intents
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,intent,tools", [
    ("what subjects do I have", "syllabus_query", ["syllabus"]),
    ("which subjects have term work", "syllabus_query", ["syllabus"]),
    ("which subjects have practical exams", "syllabus_query", ["syllabus"]),
    ("what chapters are in AI Unit 3", "syllabus_query", ["syllabus"]),
    ("when is my DBMS exam", "syllabus_exam", ["syllabus"]),
    ("which exam is next", "syllabus_exam", ["syllabus"]),
    ("how much syllabus is left", "syllabus_progress", ["planner"]),
    ("what should I study today", "study_plan", ["planner"]),
    ("plan my week", "study_plan", ["planner"]),
    ("plan my semester", "study_plan", ["planner"]),
    ("mark DBMS Unit 2 complete", "study_update", ["planner"]),
    ("I didn't study today", "study_update", ["planner"]),
    ("move today's DBMS study to tomorrow", "study_update", ["planner"]),
    ("start a DBMS study session", "study_session",
     ["syllabus", "learning", "planner"]),
])
async def test_router_intents(message, intent, tools):
    provider = RuleBasedProvider()
    c = await provider.aclassify_intent(message)
    assert c["intent"] == intent, f"{message!r} -> {c['intent']!r}"
    assert c["tools"] == tools


async def test_mark_complete_does_not_route_to_tasks():
    provider = RuleBasedProvider()
    c = await provider.aclassify_intent("mark DBMS Unit 2 complete")
    assert c["intent"] != "task_complete"


# ---------------------------------------------------------------------------
# End-to-end through the agent
# ---------------------------------------------------------------------------

async def _agent():
    return SpideyAgent(RuleBasedProvider(), TOOL_REGISTRY)


async def test_agent_subjects_e2e():
    await _ingest_all()
    agent = await _agent()
    engine = WorkflowEngine()
    run = engine.create_run("what subjects do I have")
    resp = await agent.run("what subjects do I have", run, engine)
    assert "Database Management Systems" in resp
    assert "test-syllabus.pdf" in resp
    assert engine.get_run(run.workflow_id).status == "completed"


async def test_agent_plan_week_e2e():
    await _ingest_all()
    agent = await _agent()
    engine = WorkflowEngine()
    run = engine.create_run("plan my week")
    resp = await agent.run("plan my week", run, engine)
    assert "Study plan" in resp
    assert "assumed 2h/day" in resp


async def test_agent_mark_complete_e2e():
    await _ingest_all()
    agent = await _agent()
    engine = WorkflowEngine()
    run = engine.create_run("mark DBMS Unit 2 complete")
    resp = await agent.run("mark DBMS Unit 2 complete", run, engine)
    assert "Unit 2" in resp and "complete" in resp
    assert "25%" in resp


async def test_agent_study_session_e2e():
    await _ingest_all()
    agent = await _agent()
    engine = WorkflowEngine()
    run = engine.create_run("start a DBMS study session")
    resp = await agent.run("start a DBMS study session", run, engine)
    assert "Study session started" in resp
    assert "Database Management Systems" in resp
