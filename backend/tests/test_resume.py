"""Phase 4 — resume intelligence tests (sqlite, via tests/conftest.py)."""
import pytest
from sqlalchemy import delete

from app.agents.spidey_agent import NO_CV_REPLY, SpideyAgent
from app.database import get_session
from app.models import ResumeVersion
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.tools.base import ToolError
from app.tools.resume import extract_skills
from app.workflows.engine import WorkflowEngine

SAMPLE_CV = """Alex Carter
alex.carter@example.com | +1-555-010-2030 | linkedin.com/in/alexcarter-dev | Seattle, WA

Summary
Software engineer with 4 years of experience building web applications.

Experience
Senior Software Engineer, Northwind Labs -- 2021-Present
- Responsible for managing the backend API team
- Helped with customer escalations and various support tasks
- Built a reporting dashboard used by 200+ customers, cutting report time by 40%
- Built a reporting dashboard used by over 200 customers and reduced reporting time by 40 percent

Software Engineer, Contoso -- 2019-2021
- Worked on the payments service
- Assisted in migrating the monolith to microservices etc.

Education
B.S. Computer Science, University of Washington -- 2019

Skills
Python, JavaScript, React, PostgreSQL, Docker, AWS

Projects
Invoice Tracker
- Developed an invoice tracking app with React and FastAPI
- Deployed with Docker on AWS
"""

SAMPLE_JD = """Senior Backend Engineer

We are looking for a Senior Backend Engineer with 5+ years of experience in
Python and FastAPI. You will build microservices on AWS, work with
PostgreSQL, and deploy with Docker and Kubernetes. Experience with React is
a plus. Strong communication skills required.
"""


def _clear_versions():
    with get_session() as session:
        session.execute(delete(ResumeVersion))


async def _make_version(content: str = SAMPLE_CV, label: str = "test cv") -> dict:
    tool = TOOL_REGISTRY["resume"]
    created = await tool.execute(
        action="create_version",
        content=content,
        label=label,
        source_filename="sample.txt",
        created_from="upload",
    )
    return created["version"]


def _issues_of(analysis: dict, issue_type: str) -> list[dict]:
    return [i for i in analysis["issues"] if i["type"] == issue_type]


# ---------------------------------------------------------------------------
# Parsing / section detection
# ---------------------------------------------------------------------------


async def test_section_detection(tmp_path):
    tool = TOOL_REGISTRY["resume"]
    cv_file = tmp_path / "sample_cv.txt"
    cv_file.write_text(SAMPLE_CV)
    parsed = await tool.execute(action="parse", file_path=str(cv_file))

    sections = parsed["sections"]
    for expected in ("summary", "experience", "education", "skills", "projects"):
        assert expected in sections, f"missing section: {expected}"
    assert "certifications" not in sections or not sections["certifications"]
    assert parsed["contact"]["email"] == "alex.carter@example.com"
    assert parsed["raw_text"]  # truncated sensibly, not empty


async def test_parse_rejects_missing_file():
    tool = TOOL_REGISTRY["resume"]
    with pytest.raises(ToolError):
        await tool.execute(action="parse", file_path="/tmp/does-not-exist-cv.txt")


# ---------------------------------------------------------------------------
# Analysis issue types
# ---------------------------------------------------------------------------


async def test_weak_verb_detection():
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    out = await tool.execute(action="analyze", version_id=version["id"])
    weak = _issues_of(out["analysis"], "weak_verb")
    assert len(weak) >= 3
    assert any("responsible for" in i["detail"] for i in weak)


async def test_vague_statement_detection():
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    out = await tool.execute(action="analyze", version_id=version["id"])
    vague = _issues_of(out["analysis"], "vague_statement")
    assert any("various" in i["detail"] for i in vague)


async def test_missing_measurable_detection():
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    out = await tool.execute(action="analyze", version_id=version["id"])
    missing = _issues_of(out["analysis"], "missing_measurable")
    excerpts = " ".join(i["excerpt"] for i in missing)
    assert "backend API team" in excerpts  # no numbers -> flagged
    assert "payments service" in excerpts
    assert "200+ customers" not in excerpts  # has numbers -> not flagged


async def test_repetition_detection():
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    out = await tool.execute(action="analyze", version_id=version["id"])
    rep = _issues_of(out["analysis"], "repetition")
    assert len(rep) >= 1
    assert "reporting dashboard" in rep[0]["excerpt"]


async def test_analysis_scores_and_ats():
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    analysis = (await tool.execute(action="analyze", version_id=version["id"]))[
        "analysis"
    ]
    assert 0 <= analysis["quality_score"] <= 100
    assert analysis["quality_score"] < 100  # the sample CV has real issues
    ats = analysis["ats"]
    assert ats["sections_ok"] is True
    assert ats["contact_ok"] is True
    assert 0.0 <= ats["keyword_coverage"] <= 1.0
    assert 0 <= ats["score"] <= 100
    assert "no summary section" not in analysis["missing_info"]  # summary exists


async def test_analyze_unknown_version():
    tool = TOOL_REGISTRY["resume"]
    with pytest.raises(ToolError):
        await tool.execute(action="analyze", version_id="no-such-version")


# ---------------------------------------------------------------------------
# Improve — the no-invention invariant
# ---------------------------------------------------------------------------


async def test_no_invention_invariant():
    """Every skill token in a suggestion must already exist in the CV text."""
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    out = await tool.execute(action="improve", version_id=version["id"])

    suggestions = out["suggestions"]
    assert suggestions, "expected at least one rewrite suggestion"
    cv_skills = extract_skills(SAMPLE_CV)
    for s in suggestions:
        original_skills = extract_skills(s["original"])
        improved_skills = extract_skills(s["improved"])
        # Nothing new relative to the original bullet...
        assert improved_skills <= original_skills, (
            f"suggestion invents skills: {improved_skills - original_skills}"
        )
        # ...and therefore nothing new relative to the CV.
        assert improved_skills <= cv_skills
        assert s["original"] in SAMPLE_CV
    assert out["new_version_id"] != version["id"]


async def test_improve_rewrites_weak_verbs():
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    out = await tool.execute(action="improve", version_id=version["id"])
    improved_texts = [s["improved"] for s in out["suggestions"]]
    assert any("Managed the backend API team" in t for t in improved_texts)
    assert not any("Responsible for" in t for t in improved_texts)


# ---------------------------------------------------------------------------
# Versioning: append-only
# ---------------------------------------------------------------------------


async def test_versioning_create_list_restore():
    _clear_versions()
    tool = TOOL_REGISTRY["resume"]

    v1 = await _make_version(label="v1")
    v2 = (await tool.execute(
        action="create_version", content=SAMPLE_CV + "\nExtra line.",
        label="v2", created_from="improvement",
    ))["version"]
    assert (v1["version_number"], v2["version_number"]) == (1, 2)

    listed = (await tool.execute(action="list"))["versions"]
    assert len(listed) == 2
    assert [v["version_number"] for v in listed] == [2, 1]  # newest first

    # Restore creates a NEW row copying v1's content; v1 is untouched.
    restored = await tool.execute(
        action="create_version", content=v1["content"],
        label="restored", created_from=v1["id"],
    )
    v3 = restored["version"]
    assert v3["version_number"] == 3
    assert v3["content"] == v1["content"]
    assert v3["id"] != v1["id"]

    after = (await tool.execute(action="list"))["versions"]
    assert len(after) == 3
    v1_again = next(v for v in after if v["id"] == v1["id"])
    assert v1_again["content"] == v1["content"]
    assert v1_again["version_number"] == 1


async def test_latest_returns_newest():
    _clear_versions()
    tool = TOOL_REGISTRY["resume"]
    assert (await tool.execute(action="latest"))["version"] is None
    v1 = await _make_version()
    assert (await tool.execute(action="latest"))["version"]["id"] == v1["id"]
    v2 = (await tool.execute(
        action="create_version", content=SAMPLE_CV, label="v2"))["version"]
    assert (await tool.execute(action="latest"))["version"]["id"] == v2["id"]


# ---------------------------------------------------------------------------
# Job matching
# ---------------------------------------------------------------------------


async def test_job_match_split():
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    out = await tool.execute(
        action="job_match", version_id=version["id"], job_description=SAMPLE_JD
    )
    match = out["job_match"]
    assert "python" in match["matched_skills"]
    assert "react" in match["matched_skills"]
    assert "kubernetes" in match["missing_skills"]
    assert "python" not in match["missing_skills"]
    assert 0 < match["match_score"] < 100
    assert match["improvements"], "expected gap-closing improvements"
    # Honest framing: missing skills are never claimed as possessed.
    assert any("kubernetes" in imp for imp in match["improvements"])


async def test_job_match_needs_description():
    tool = TOOL_REGISTRY["resume"]
    version = await _make_version()
    with pytest.raises(ToolError):
        await tool.execute(action="job_match", version_id=version["id"],
                           job_description="   ")


# ---------------------------------------------------------------------------
# Agent wiring
# ---------------------------------------------------------------------------


async def test_resume_intent_classification():
    provider = RuleBasedProvider()
    c = await provider.aclassify_intent("Spidey, analyze my resume.")
    assert c["intent"] == "resume_analyze" and "resume" in c["tools"]
    c = await provider.aclassify_intent("improve my CV")
    assert c["intent"] == "resume_improve" and "resume" in c["tools"]
    c = await provider.aclassify_intent("check my CV")
    assert c["intent"] == "resume_analyze"


async def test_analyze_with_no_cv_gives_clean_message():
    _clear_versions()
    provider = RuleBasedProvider()
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("Spidey, analyze my resume.")
    response = await agent.run("Spidey, analyze my resume.", run, engine)
    assert NO_CV_REPLY in response
    assert engine.get_run(run.workflow_id).status == "completed"
    step_names = [s.name for s in engine.get_run(run.workflow_id).steps]
    assert step_names[0] == "Understand request"
    assert "Read resume" in step_names


async def test_resume_pipeline_nine_visible_steps():
    _clear_versions()
    await _make_version()
    provider = RuleBasedProvider()
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    engine = WorkflowEngine()
    run = engine.create_run("Spidey, analyze my resume.")
    response = await agent.run("Spidey, analyze my resume.", run, engine)

    finished = engine.get_run(run.workflow_id)
    assert finished.status == "completed"
    step_names = [s.name for s in finished.steps]
    assert step_names == [
        "Understand request",
        "Read resume",
        "Analyze sections",
        "Identify weaknesses",
        "Retrieve memory",
        "Generate suggestions",
        "Verify results",
        "Compose response",
        "Save memory",
    ]
    assert all(s.status == "COMPLETED" for s in finished.steps)
    assert "Quality score" in response


async def test_polish_rejects_invented_numbers():
    """The LLM polish must not fabricate metrics: invented numbers in the
    polished bullet (not present in the original) fall back to the draft."""
    from app.tools.resume import _polish_with_provider
    import app.providers as providers_mod

    class FakeProvider:
        name = "ollama"

        async def agenerate(self, message, context="", history=None, lang=None):
            return "Built dashboard achieving 98% user satisfaction."

    orig_get = providers_mod.get_provider
    providers_mod.get_provider = lambda: FakeProvider()
    try:
        out = await _polish_with_provider(
            "Built a security dashboard for monitoring.",
            "Built a security dashboard for monitoring.",
        )
    finally:
        providers_mod.get_provider = orig_get
    assert out is None  # invented "98%" rejected


async def test_polish_keeps_original_numbers():
    """Numbers already in the original bullet are fine to keep."""
    from app.tools.resume import _polish_with_provider
    import app.providers as providers_mod

    class FakeProvider:
        name = "ollama"

        async def agenerate(self, message, context="", history=None, lang=None):
            return "Led migration serving 200 users."

    orig_get = providers_mod.get_provider
    providers_mod.get_provider = lambda: FakeProvider()
    try:
        out = await _polish_with_provider(
            "Worked on migration serving 200 users.",
            "Led migration serving 200 users.",
        )
    finally:
        providers_mod.get_provider = orig_get
    assert out == "Led migration serving 200 users."


def test_resume_ordinal_matches_my_and_the():
    """Spec TEST 6 phrasing: 'Rewrite my second project.' (not just 'the')
    must route to resume_improve with the right project entry."""
    from app.providers.rule_based import RuleBasedProvider
    p = RuleBasedProvider()
    for msg in ("Rewrite my second project.", "rewrite the second project"):
        c = p._classify(msg, None)
        assert c["intent"] == "resume_improve", msg
        assert c["resume_section"] == "projects", msg
        assert c["resume_entry"] == 1, msg  # second project -> index 1
