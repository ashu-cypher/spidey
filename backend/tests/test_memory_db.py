"""Phase 2 — memory tool backed by the database (sqlite in /tmp via conftest)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import get_session
from app.models import Memory
from app.tools.base import ToolError
from app.tools.memory_tool import MemoryTool

pytestmark = pytest.mark.asyncio


async def test_memory_save_returns_expected_keys():
    t = MemoryTool()
    r = await t.execute(action="save", content="phase2 crud probe alpha")
    saved = r["saved"]
    assert saved["content"] == "phase2 crud probe alpha"
    assert saved["category"] == "general"
    assert saved["importance"] == 0.5
    assert saved["id"]
    assert saved["created_at"]


async def test_memory_importance_scoring():
    t = MemoryTool()
    r = await t.execute(action="save", content="I prefer Python")
    assert r["saved"]["category"] == "preference"
    assert r["saved"]["importance"] == 0.8


async def test_memory_recall_orders_by_overlap_then_importance():
    t = MemoryTool()
    await t.execute(action="save", content="recall-probe I am learning Rust for systems work")
    await t.execute(action="save", content="recall-probe my favorite color is blue")
    r = await t.execute(action="recall", query="what am I learning in recall-probe?")
    assert r["results"], "expected recall to find the Rust memory"
    assert "Rust" in r["results"][0]["content"]


async def test_memory_list_excludes_nothing_valid_and_shows_shape():
    t = MemoryTool()
    await t.execute(action="save", content="list-shape probe memory")
    r = await t.execute(action="list")
    items = [m for m in r["memories"] if m["content"] == "list-shape probe memory"]
    assert len(items) == 1
    assert set(items[0]) >= {"id", "content", "category", "importance", "created_at"}


async def test_memory_temporary_gets_expiry_and_expired_not_recalled():
    t = MemoryTool()
    r = await t.execute(action="save", content="temporary expiry-probe note for later")
    saved = r["saved"]
    assert saved["category"] == "temporary"
    assert saved["importance"] == 0.3
    assert saved["expires_at"] is not None

    # Backdate it to force expiry.
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    with get_session() as session:
        row = session.get(Memory, saved["id"])
        row.expires_at = past

    recalled = await t.execute(action="recall", query="expiry-probe note")
    assert all("expiry-probe" not in m["content"] for m in recalled["results"])
    listed = await t.execute(action="list")
    assert all("expiry-probe" not in m["content"] for m in listed["memories"])
    listed_all = await t.execute(action="list", include_expired=True)
    assert any("expiry-probe" in m["content"] for m in listed_all["memories"])


async def test_memory_delete():
    t = MemoryTool()
    r = await t.execute(action="save", content="delete-probe doomed memory")
    mid = r["saved"]["id"]
    d = await t.execute(action="delete", id=mid)
    assert d["deleted"] == mid
    listed = await t.execute(action="list")
    assert all(m["id"] != mid for m in listed["memories"])
    with pytest.raises(ToolError):
        await t.execute(action="delete", id=mid)


async def test_memory_save_rejects_empty():
    t = MemoryTool()
    with pytest.raises(ToolError):
        await t.execute(action="save", content="   ")
    with pytest.raises(ToolError):
        await t.execute(action="recall", query="")


async def test_memory_explicit_category_overrides_auto():
    t = MemoryTool()
    r = await t.execute(
        action="save", content="I love hiking", category="hobby", importance=0.9
    )
    assert r["saved"]["category"] == "hobby"
    assert r["saved"]["importance"] == 0.9
