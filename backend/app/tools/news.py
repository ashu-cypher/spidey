"""News digest tool — structured daily news from REAL web search.

``daily_news(categories)`` runs the existing 4-source SearchTool (Bing HTML,
Bing RSS, DuckDuckGo, Wikipedia) in news mode once per category, dedupes by
URL and title similarity, drops stories whose known publish date is older
than 48h, and returns a structured digest. Tech/AI categories are
prioritized for this user.

Honesty rules (standing):
- Search returned nothing -> honest "no news found", never fabricated.
- Stories with a known publish date older than 48h are dropped; stories
  without a date are kept but every story carries ``fetched_at`` (UTC) so an
  old story is never presented as today's.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.tools.base import BaseTool, ToolError

# Tech/AI first — this user's standing interest priority.
_DEFAULT_CATEGORIES = ["AI", "technology", "India", "world"]

_MAX_AGE = timedelta(hours=48)
_SUMMARY_CHARS = 220

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_url(url: str) -> str:
    """Canonical form for dedupe: lowercase host, strip tracking params."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = parts.path.rstrip("/") or "/"
        kept = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        ]
        return urlunsplit(
            (parts.scheme.lower(), host, path, urlencode(kept), "")
        )
    except Exception:
        return url.lower()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{4,}", (text or "").lower()))


def _titles_similar(a: str, b: str) -> bool:
    """Token-overlap similarity; catches syndicated rewrites of one story."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return overlap >= 0.6


def dedupe_results(results: list[dict]) -> list[dict]:
    """Drop duplicate stories: same normalized URL or similar title.

    First occurrence wins (keeps category priority ordering).
    """
    kept: list[dict] = []
    seen_urls: set[str] = set()
    for r in results:
        url = _normalize_url(r.get("url", ""))
        if url and url in seen_urls:
            continue
        if any(_titles_similar(r.get("title", ""), k.get("title", "")) for k in kept):
            continue
        if url:
            seen_urls.add(url)
        kept.append(r)
    return kept


def _parse_date(value: object) -> datetime | None:
    """Best-effort publish-date parse from common result fields."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    # RFC 2822 (RSS pubDate)
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    # ISO 8601
    try:
        iso = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _story_date(result: dict) -> datetime | None:
    for key in ("published", "pubDate", "date", "published_at"):
        dt = _parse_date(result.get(key))
        if dt:
            return dt
    return None


def _is_fresh(result: dict, now: datetime) -> bool:
    """Drop stories older than 48h when their date is known."""
    dt = _story_date(result)
    if dt is None:
        return True
    return now - dt <= _MAX_AGE


def _summarize_text(snippet: str) -> str:
    text = " ".join((snippet or "").split())
    if len(text) <= _SUMMARY_CHARS:
        return text
    cut = text[:_SUMMARY_CHARS].rsplit(" ", 1)[0]
    return cut + "…"


async def _search_category(category: str, limit: int) -> list[dict]:
    """One news-mode search; ToolError (no results) -> empty, never fatal."""
    from app.tools.search import SearchTool

    try:
        out = await SearchTool().execute(
            query=f"{category} news", mode="news", limit=limit
        )
    except ToolError:
        return []
    except Exception:
        return []
    results = out.get("results", []) if isinstance(out, dict) else []
    for r in results:
        if isinstance(r, dict):
            r["category"] = category
    return [r for r in results if isinstance(r, dict)]


async def daily_news(
    categories: list[str] | None = None, per_category: int = 4
) -> dict:
    """Fetch, dedupe, and structure today's news across categories.

    Returns {"news": [story...], "categories": [...], "fetched_at": iso,
    "message": str}. ``news`` is empty with an honest message when search
    returns nothing — stories are never invented.
    """
    cats = [c for c in (categories or _DEFAULT_CATEGORIES) if c and c.strip()]
    if not cats:
        cats = list(_DEFAULT_CATEGORIES)
    now = _utcnow()
    all_results: list[dict] = []
    for cat in cats:
        all_results.extend(await _search_category(cat, per_category))
    fresh = [r for r in dedupe_results(all_results) if _is_fresh(r, now)]
    stories = []
    for r in fresh:
        dt = _story_date(r)
        stories.append(
            {
                "headline": (r.get("title") or "").strip(),
                "summary": _summarize_text(r.get("snippet") or ""),
                "source": (r.get("source") or "web").strip(),
                "url": (r.get("url") or "").strip(),
                "date": dt.isoformat() if dt else None,
                "fetched_at": now.isoformat(),
                "category": r.get("category", ""),
            }
        )
    stories = [s for s in stories if s["headline"]]
    if not stories:
        return {
            "news": [],
            "categories": cats,
            "fetched_at": now.isoformat(),
            "message": (
                "I couldn't find any fresh news stories right now — "
                "the web search came back empty. Try again in a bit."
            ),
        }
    return {
        "news": stories,
        "categories": cats,
        "fetched_at": now.isoformat(),
        "message": f"Found {len(stories)} stories across {len(cats)} categories.",
    }


def render_digest(news: list[dict], fetched_at: str) -> str:
    """Markdown digest for chat display."""
    try:
        day = datetime.fromisoformat(fetched_at).strftime("%A, %B %d")
    except Exception:
        day = "today"
    lines = [f"# 📰 Today's News — {day}", ""]
    by_cat: dict[str, list[dict]] = {}
    order: list[str] = []
    for s in news:
        cat = s.get("category") or "top stories"
        if cat not in by_cat:
            by_cat[cat] = []
            order.append(cat)
        by_cat[cat].append(s)
    for cat in order:
        lines.append(f"## {cat}")
        lines.append("")
        for s in by_cat[cat]:
            head = s.get("headline", "")
            summary = s.get("summary", "")
            source = s.get("source", "")
            url = s.get("url", "")
            bullet = f"- **{head}**"
            if summary:
                bullet += f" — {summary}"
            meta = " · ".join(p for p in (source, s.get("date")) if p)
            if meta:
                bullet += f" ({meta})"
            lines.append(bullet)
            if url:
                lines.append(f"  <{url}>")
        lines.append("")
    return "\n".join(lines).strip()


class NewsTool(BaseTool):
    name = "news"
    description = (
        "Fetches today's news across categories (AI, technology, India, "
        "world) from live web search, deduped and structured. Honest about "
        "empty results — never invents stories."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["digest", "latest"],
                "description": "digest: multi-category digest; latest: news about a query",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Categories for digest (default AI/tech/India/world)",
            },
            "query": {
                "type": "string",
                "description": "Topic for action=latest",
            },
        },
        "required": [],
    }
    output_schema = {"news": "list", "digest": "string"}
    permission = "read"

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "digest").strip().lower()
        if action == "latest":
            query = (kwargs.get("query") or "").strip()
            if not query:
                raise ToolError("What topic should I get the latest news about?")
            result = await daily_news(categories=[query], per_category=5)
        else:
            categories = kwargs.get("categories")
            if categories is not None and not isinstance(categories, list):
                raise ToolError("categories must be a list of strings.")
            result = await daily_news(categories=categories)
        news = result["news"]
        digest = (
            render_digest(news, result["fetched_at"])
            if news
            else result["message"]
        )
        return {
            "news": news,
            "categories": result["categories"],
            "fetched_at": result["fetched_at"],
            "message": result["message"],
            "digest": digest,
        }
