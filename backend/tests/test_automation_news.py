"""Automation Engine + News Automation tests.

Covers: job CRUD round trip, schedule validation, due-job detection logic,
claim-before-execute (exactly-once), quiet-hours suppression,
no-new-content skip, dedupe logic, 48h freshness filter, and the news
tool's honest empty-result behavior (mocked search). Web search is never
hit: SearchTool.execute is monkeypatched.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.services import scheduler
from app.tools import news as news_module
from app.tools.base import ToolError
from app.tools.news import (
    NewsTool,
    _is_fresh,
    _parse_date,
    _titles_similar,
    daily_news,
    dedupe_results,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _isolated_scheduler_db(tmp_path, monkeypatch):
    db = str(tmp_path / "jobs.db")
    scheduler.set_db_path(db)
    monkeypatch.setenv("SPIDEY_SCHEDULER_DB", db)
    scheduler._init()
    yield
    scheduler.set_db_path(None)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- job CRUD ---------------------------------------------------------------

def test_job_crud_round_trip():
    job = scheduler.create_job(
        kind="daily_briefing",
        name="Morning briefing",
        schedule="daily@07:30",
        payload={"user_timezone": "Asia/Calcutta"},
    )
    assert job["id"]
    assert job["kind"] == "daily_briefing"
    assert job["enabled"] is True
    assert job["payload"]["user_timezone"] == "Asia/Calcutta"

    jobs = scheduler.list_jobs()
    assert any(j["id"] == job["id"] for j in jobs)

    fetched = scheduler.get_job(job["id"])
    assert fetched and fetched["name"] == "Morning briefing"

    assert scheduler.delete_job(job["id"]) is True
    assert scheduler.get_job(job["id"]) is None
    assert scheduler.delete_job(job["id"]) is False


def test_create_job_rejects_bad_kind_and_schedule():
    with pytest.raises(ValueError):
        scheduler.create_job(kind="nap_time", name="x", schedule="daily@07:00")
    with pytest.raises(ValueError):
        scheduler.create_job(kind="reminder", name="x", schedule="sometimes")
    with pytest.raises(ValueError):
        scheduler.create_job(kind="reminder", name="x", schedule="daily@25:00")
    with pytest.raises(ValueError):
        scheduler.create_job(kind="reminder", name="x", schedule="weekly@funday@09:00")
    with pytest.raises(ValueError):
        scheduler.create_job(kind="reminder", name="", schedule="daily@07:00")


def test_all_schedule_forms_parse():
    assert scheduler.parse_schedule("daily@07:30")["type"] == "daily"
    assert scheduler.parse_schedule("weekly@mon@09:00")["weekday"] == 0
    assert scheduler.parse_schedule("weekly@Sunday@09:00")["weekday"] == 6
    assert scheduler.parse_schedule("interval@30")["minutes"] == 30
    spec = scheduler.parse_schedule("once@2026-10-06T09:00:00Z")
    assert spec["type"] == "once" and spec["at"].tzinfo is not None


# --- due-job detection ------------------------------------------------------

def test_daily_due_logic():
    job = scheduler.create_job("news_briefing", "n", "daily@09:00", {})
    # 10:00 UTC: today's 09:00 occurrence passed, never ran -> due
    due, occ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 0))
    assert due and occ is not None
    # Pretend it ran at that occurrence -> not due anymore
    job["last_run"] = occ
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 0))
    assert not due
    # Next day 10:00 -> due again
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 6, 10, 0))
    assert due
    # Before today's occurrence with no prior run -> due (catch-up run)
    job2 = scheduler.create_job("news_briefing", "n2", "daily@23:00", {})
    due, _ = scheduler.job_due(job2, now=_utc(2026, 10, 5, 8, 0))
    assert due


def test_weekly_due_logic():
    # Monday 2026-10-05. weekly@mon@09:00 fires 10:00 Monday -> due.
    job = scheduler.create_job("weekly_review", "w", "weekly@mon@09:00", {})
    due, occ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 0))
    assert due
    job["last_run"] = occ
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 0))
    assert not due
    # Sunday before -> most recent Monday occurrence is past -> due
    job2 = scheduler.create_job("weekly_review", "w2", "weekly@mon@09:00", {})
    due, _ = scheduler.job_due(job2, now=_utc(2026, 10, 4, 12, 0))
    assert due


def test_interval_due_logic():
    job = scheduler.create_job("news_briefing", "i", "interval@60", {})
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 0))
    assert due  # never ran
    job["last_run"] = _utc(2026, 10, 5, 9, 45).timestamp()
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 0))
    assert not due
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 46))
    assert due


def test_once_due_logic():
    job = scheduler.create_job(
        "reminder", "r", "once@2026-10-05T09:00:00Z", {"text": "hi"}
    )
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 0))
    assert due
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 5, 8, 0))
    assert not due
    job["last_run"] = _utc(2026, 10, 5, 9, 5).timestamp()
    due, _ = scheduler.job_due(job, now=_utc(2026, 10, 5, 10, 0))
    assert not due


def test_claim_is_exactly_once():
    job = scheduler.create_job("news_briefing", "c", "interval@60", {})
    assert scheduler.claim_job(job["id"], 1000.0) is True
    # A second claim for the same (or older) occurrence loses the race.
    assert scheduler.claim_job(job["id"], 1000.0) is False
    assert scheduler.claim_job(job["id"], 999.0) is False
    # A newer occurrence can be claimed.
    assert scheduler.claim_job(job["id"], 1001.0) is True


# --- quiet hours ------------------------------------------------------------

async def _empty_news(categories=None, per_category=4):
    return {
        "news": [],
        "categories": categories or [],
        "fetched_at": datetime.now(UTC).isoformat(),
        "message": "I couldn't find any fresh news stories right now.",
    }


def test_quiet_hours_suppression(monkeypatch):
    monkeypatch.setattr(news_module, "daily_news", _empty_news)
    # Job due, but the whole day is quiet in UTC -> postponed, not claimed.
    job = scheduler.create_job(
        "daily_briefing",
        "qb",
        "interval@60",
        {"quiet_hours": ["00:00", "23:59"], "user_timezone": "UTC"},
    )
    result = asyncio.run(scheduler.run_job(job, now=_utc(2026, 10, 5, 12, 0)))
    assert result["ok"] is True
    assert result["skipped"] == "quiet_hours"
    assert result["delivered"] is False
    assert scheduler.get_job(job["id"])["last_run"] is None

    # Quiet hours disabled -> runs.
    job2 = scheduler.create_job(
        "daily_briefing",
        "qb2",
        "interval@60",
        {"quiet_hours": None, "user_timezone": "UTC"},
    )
    result = asyncio.run(scheduler.run_job_now(job2["id"]))
    assert result["ok"] is True
    assert "skipped" not in result


def test_quiet_hours_window_logic():
    assert scheduler.in_quiet_hours(
        {"quiet_hours": ["22:00", "07:00"], "user_timezone": "UTC"},
        now=_utc(2026, 10, 5, 23, 30),
    )
    assert scheduler.in_quiet_hours(
        {"quiet_hours": ["22:00", "07:00"], "user_timezone": "UTC"},
        now=_utc(2026, 10, 6, 6, 59),
    )
    assert not scheduler.in_quiet_hours(
        {"quiet_hours": ["22:00", "07:00"], "user_timezone": "UTC"},
        now=_utc(2026, 10, 6, 7, 1),
    )
    assert not scheduler.in_quiet_hours({"quiet_hours": None})


def test_no_new_content_skip(monkeypatch):
    monkeypatch.setattr(news_module, "daily_news", _empty_news)
    job = scheduler.create_job(
        "daily_briefing", "nc", "interval@1",
        {"quiet_hours": None, "user_timezone": "UTC"},
    )
    first = asyncio.run(scheduler.run_job_now(job["id"]))
    assert first["ok"] is True and "skipped" not in first
    # Same empty stores -> identical content -> a later tick skips sending.
    job = scheduler.get_job(job["id"])
    later = datetime.now(UTC) + timedelta(seconds=120)
    second = asyncio.run(scheduler.run_job(job, now=later))
    assert second["skipped"] == "no_new_content"


def test_manual_run_honest_result_empty_stores(monkeypatch):
    monkeypatch.setattr(news_module, "daily_news", _empty_news)
    job = scheduler.create_job(
        "daily_briefing", "smoke", "interval@60",
        {"quiet_hours": None, "user_timezone": "UTC"},
    )
    result = asyncio.run(scheduler.run_job_now(job["id"]))
    assert result["ok"] is True
    # Nothing configured here: honest in-app fallback, never claimed sent.
    assert result["channel"] == "in_app"
    assert result["delivered"] is False
    assert "nothing is scheduled" in result["content_preview"].lower()


def test_one_shot_reminder_disables_after_run():
    job = scheduler.create_job(
        "reminder", "oneshot", "once@2026-10-05T09:00:00Z",
        {"text": "drink water", "quiet_hours": None, "user_timezone": "UTC"},
    )
    result = asyncio.run(scheduler.run_job_now(job["id"]))
    assert result["ok"] is True
    assert "drink water" in result["content_preview"]
    assert scheduler.get_job(job["id"])["enabled"] is False


# --- dedupe -----------------------------------------------------------------

def test_dedupe_by_url():
    a = {"title": "AI breakthrough in chips", "url": "https://x.com/a?utm_source=rss", "snippet": "s"}
    b = {"title": "AI breakthrough in chips", "url": "https://x.com/a", "snippet": "s"}
    assert len(dedupe_results([a, b])) == 1


def test_dedupe_by_similar_title():
    a = {"title": "OpenAI releases new GPT model today", "url": "https://a.com/1", "snippet": "s"}
    b = {"title": "OpenAI releases new GPT model, reports say", "url": "https://b.com/2", "snippet": "s"}
    c = {"title": "Monsoon arrives early in Kerala", "url": "https://c.com/3", "snippet": "s"}
    out = dedupe_results([a, b, c])
    assert len(out) == 2
    assert _titles_similar(a["title"], b["title"])
    assert not _titles_similar(a["title"], c["title"])


def test_freshness_filter():
    now = _utc(2026, 10, 5, 12, 0)
    old = {"published": (_utc(2026, 10, 2, 10, 0)).isoformat()}  # 74h old
    assert not _is_fresh(old, now)
    recent = {"pubDate": "Mon, 05 Oct 2026 10:00:00 GMT"}
    assert _is_fresh(recent, now)
    nodate = {}
    assert _is_fresh(nodate, now)  # unknown date -> kept, never silently dropped
    assert _parse_date("not a date") is None


# --- news tool (mocked search) ----------------------------------------------

def _mock_search(results):
    async def fake_execute(self, **kwargs):
        if not results:
            raise ToolError("Web search is unavailable right now.")
        return {"results": results}

    return fake_execute


def test_news_tool_honest_when_search_empty(monkeypatch):
    monkeypatch.setattr(
        sys.modules["app.tools.search"].SearchTool,
        "execute",
        _mock_search([]),
    )
    out = asyncio.run(daily_news(categories=["AI"]))
    assert out["news"] == []
    assert "couldn't find" in out["message"]

    tool_out = asyncio.run(NewsTool().execute(action="digest", categories=["AI"]))
    assert tool_out["news"] == []
    assert "couldn't find" in tool_out["digest"]


def test_news_tool_structures_and_prioritizes(monkeypatch):
    ai_story = {
        "title": "AI model beats benchmark",
        "url": "https://ai.example/1",
        "snippet": "A new model set a record on reasoning tests today.",
        "source": "Bing",
        "published": _utc(2026, 10, 5, 9, 0).isoformat(),
    }
    dup = dict(ai_story, url="https://ai.example/1?utm_medium=x")
    stale = {
        "title": "Old tech story",
        "url": "https://old.example/9",
        "snippet": "Ancient history.",
        "source": "Bing",
        "published": _utc(2026, 9, 20, 9, 0).isoformat(),
    }

    async def fake_execute(self, **kwargs):
        q = kwargs.get("query", "")
        if q.startswith("AI"):
            return {"results": [ai_story, dup]}
        if q.startswith("technology"):
            return {"results": [stale]}
        raise ToolError("no results")

    monkeypatch.setattr(
        sys.modules["app.tools.search"].SearchTool, "execute", fake_execute
    )
    out = asyncio.run(daily_news(categories=["AI", "technology"]))
    stories = out["news"]
    assert len(stories) == 1  # dup deduped, stale (>48h) dropped
    s = stories[0]
    assert s["headline"] == "AI model beats benchmark"
    assert s["category"] == "AI"
    assert s["fetched_at"]
    assert set(s) >= {"headline", "summary", "source", "url", "date", "fetched_at", "category"}


async def test_news_router_intent_classification():
    from app.providers.rule_based import RuleBasedProvider

    provider = RuleBasedProvider()
    for text in (
        "give me today's AI news",
        "send today's news",
        "what's the latest in AI",
    ):
        c = await provider.aclassify_intent(text)
        assert c["intent"] == "news", text
        assert c["tools"] == ["news"], text
    # Existing behavior untouched: generic news phrases stay web_search.
    assert (await provider.aclassify_intent("tell me top 5 news"))["intent"] == "web_search"
    assert (await provider.aclassify_intent("latest news"))["intent"] == "web_search"


def test_news_args_extraction():
    from app.agents.spidey_agent import SpideyAgent

    args = SpideyAgent._extract_news_args("give me today's AI news")
    assert args["categories"] == ["AI"]
    args = SpideyAgent._extract_news_args("send today's news")
    assert args["action"] == "digest"
    assert "categories" not in args


# --- API routes ---------------------------------------------------------------

def test_automation_api_round_trip(monkeypatch):
    monkeypatch.setattr(news_module, "daily_news", _empty_news)
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    created = client.post(
        "/api/automation/jobs",
        json={
            "kind": "news_briefing",
            "name": "api test",
            "schedule": "daily@08:00",
            "payload": {"categories": ["AI"]},
        },
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["job"]["id"]

    listed = client.get("/api/automation/jobs")
    assert listed.status_code == 200
    assert any(j["id"] == job_id for j in listed.json()["jobs"])

    bad = client.post(
        "/api/automation/jobs",
        json={"kind": "nope", "name": "x", "schedule": "daily@08:00"},
    )
    assert bad.status_code == 400
    assert "error" in bad.json()["detail"]

    # Manual run: honest result (no channels configured in tests).
    run = client.post(f"/api/automation/jobs/{job_id}/run")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["ok"] is True
    assert body["delivered"] is False

    missing = client.post("/api/automation/jobs/does-not-exist/run")
    assert missing.status_code == 404

    deleted = client.delete(f"/api/automation/jobs/{job_id}")
    assert deleted.status_code == 200
    assert client.delete(f"/api/automation/jobs/{job_id}").status_code == 404
