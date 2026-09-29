import pytest

from app.tools.base import ToolError
from app.tools.calculator import CalculatorTool
from app.tools.memory_tool import MemoryTool
from app.tools.tasks import TaskTool


async def test_calculator_basic():
    t = CalculatorTool()
    r = await t.execute(text="calculate 12 * 8")
    assert r["result"] == 96


async def test_calculator_parens():
    t = CalculatorTool()
    r = await t.execute(text="what is (2 + 3) * 4?")
    assert r["result"] == 20


async def test_calculator_rejects_code():
    t = CalculatorTool()
    with pytest.raises(ToolError):
        await t.execute(text="__import__('os').system('x')")


async def test_memory_save_recall():
    t = MemoryTool()
    s = await t.execute(action="save", content="I am learning Python")
    assert s["saved"]["importance"] >= 0.6
    r = await t.execute(action="recall", query="what am I learning?")
    assert any("Python" in m["content"] for m in r["results"])


async def test_tasks_crud():
    t = TaskTool()
    c = await t.execute(action="create", title="Buy milk")
    assert c["task"]["done"] is False
    l = await t.execute(action="list")
    assert len(l["tasks"]) == 1
    d = await t.execute(action="complete", id=c["task"]["id"])
    assert d["task"]["done"] is True
