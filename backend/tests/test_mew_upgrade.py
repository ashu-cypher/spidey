"""Tests for the MEW upgrade: generate, knowledge graph, project, learning,
computer stub, and the new router intents."""
import pytest

from app.tools import TOOL_REGISTRY
from app.tools.base import ToolError


# -- generate ------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_pdf(tmp_path, monkeypatch):
    import app.tools.generate as gen

    monkeypatch.setattr(gen, "_GENERATED_DIR", tmp_path)
    tool = TOOL_REGISTRY["generate"]
    out = await tool.execute(
        action="pdf", title="Test", content="# Hello\n\nSome **bold** text."
    )
    f = out["file"]
    assert f["format"] == "pdf"
    assert f["size_bytes"] > 0
    assert f["download_url"].startswith("/api/generated/")
    path = tool.resolve_path(f["id"])
    assert path is not None and path.exists()
    # Real PDF magic bytes.
    assert path.read_bytes()[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_generate_docx(tmp_path, monkeypatch):
    import app.tools.generate as gen

    monkeypatch.setattr(gen, "_GENERATED_DIR", tmp_path)
    tool = TOOL_REGISTRY["generate"]
    out = await tool.execute(
        action="docx", title="Test", content="# Hi\n\n- one\n- two"
    )
    f = out["file"]
    assert f["format"] == "docx"
    assert tool.resolve_path(f["id"]).exists()


@pytest.mark.asyncio
async def test_generate_csv(tmp_path, monkeypatch):
    import app.tools.generate as gen

    monkeypatch.setattr(gen, "_GENERATED_DIR", tmp_path)
    tool = TOOL_REGISTRY["generate"]
    out = await tool.execute(
        action="csv", title="T", rows=[["a", "b"], ["1", "2"]]
    )
    path = tool.resolve_path(out["file"]["id"])
    assert "a,b" in path.read_text()


@pytest.mark.asyncio
async def test_generate_empty_pdf_fails(tmp_path, monkeypatch):
    import app.tools.generate as gen

    monkeypatch.setattr(gen, "_GENERATED_DIR", tmp_path)
    with pytest.raises(ToolError):
        await TOOL_REGISTRY["generate"].execute(action="pdf", title="T", content="  ")


@pytest.mark.asyncio
async def test_generate_bad_action():
    with pytest.raises(ToolError) as e:
        await TOOL_REGISTRY["generate"].execute(action="pptx")
    assert "outline" in str(e.value.user_message)


# -- knowledge graph ------------------------------------------------------
@pytest.mark.asyncio
async def test_knowledge_learn_and_query():
    tool = TOOL_REGISTRY["knowledge"]
    out = await tool.execute(
        action="learn", statement="My main project is TestProj, it uses Ollama and RAG"
    )
    learned = out["learned"]
    assert any("TestProj" in s for s in learned)
    q = await tool.execute(action="query", query="my projects")
    assert "TestProj" in q["answer"]
    d = await tool.execute(action="describe", query="TestProj")
    assert "ollama" in d["answer"].lower() or "uses" in d["answer"].lower()
    # Cleanup so other tests don't see it.
    await tool.execute(action="forget", query="TestProj")


@pytest.mark.asyncio
async def test_knowledge_learn_garbage():
    with pytest.raises(ToolError):
        await TOOL_REGISTRY["knowledge"].execute(
            action="learn", statement="the weather is nice today"
        )


# -- project --------------------------------------------------------------
@pytest.mark.asyncio
async def test_project_analyze_self():
    tool = TOOL_REGISTRY["project"]
    out = await tool.execute(action="analyze", path="app/tools")
    a = out["analysis"]
    assert a["source_files"] > 0
    assert ".py" in a["by_language"]


@pytest.mark.asyncio
async def test_project_bad_path():
    with pytest.raises(ToolError) as e:
        await TOOL_REGISTRY["project"].execute(
            action="analyze", path="/nonexistent/xyz123"
        )
    assert "doesn't exist" in e.value.user_message


@pytest.mark.asyncio
async def test_project_search():
    tool = TOOL_REGISTRY["project"]
    out = await tool.execute(
        action="search", path="app/tools", pattern="class GenerateTool"
    )
    assert "generate.py" in " ".join(out["search"]["files_matched"])


# -- learning ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_learning_note_quiz():
    tool = TOOL_REGISTRY["learning"]
    saved = await tool.execute(
        action="note", topic="TestTopicXYZ", notes="RAG combines retrieval with generation."
    )
    assert saved["saved"]["topic"] == "TestTopicXYZ"
    quiz = await tool.execute(action="quiz", topic="TestTopicXYZ", num_questions=3)
    assert len(quiz["quiz"]["questions"]) == 3
    assert "disclaimer" in quiz["quiz"]
    topics = await tool.execute(action="topics")
    assert any(t["topic"] == "TestTopicXYZ" for t in topics["topics"])


# -- computer stub ------------------------------------------------------------
@pytest.mark.asyncio
async def test_computer_honest_stub():
    tool = TOOL_REGISTRY["computer"]
    out = await tool.execute(action="screenshot")
    assert out["implemented"] is False
    assert "isn't implemented" in out["message"]
    assert tool.permission == "confirm"


# -- router ---------------------------------------------------------------------
def _classify(text):
    from app.providers.rule_based import RuleBasedProvider

    return RuleBasedProvider()._classify(text)


def test_route_generate_file():
    c = _classify("make a PDF of the quarterly summary")
    assert c["intent"] == "generate_file"
    assert c["tools"] == ["generate"]


def test_route_generate_prompt_not_swallowed():
    c = _classify("generate a prompt for building a todo app")
    assert c["intent"] == "generate_prompt"


def test_route_research_and_generate():
    c = _classify("research RAG in education and make me a PDF")
    assert c["intent"] == "research_and_generate"
    assert c["tools"] == ["research", "generate"]


def test_route_project():
    c = _classify("explain my project")
    assert c["intent"] == "project_analyze"


def test_route_learning_quiz():
    c = _classify("quiz me on RAG")
    assert c["intent"] == "learning"


def test_route_knowledge_learn():
    c = _classify("My main project is MEW")
    assert c["intent"] == "knowledge_learn"


def test_route_knowledge_query():
    c = _classify("what do you remember about my projects?")
    assert c["intent"] == "knowledge_query"


def test_route_computer_never():
    # No phrasing should route to computer control.
    for text in ("click the button", "take a screenshot", "type hello"):
        c = _classify(text)
        assert "computer" not in c.get("tools", []), text
