"""Phase 5 — web search tool (DuckDuckGo Instant Answer API, no key needed).

Honest scope: the DDG instant-answer endpoint returns an abstract plus
related topics — great for quick facts, not a full web index. Any failure
degrades to a user-safe ToolError, never a traceback.

MISSION MEW additions:
* ``mode``: "web" (existing behaviour) or "news". DDG's instant-answer API
  has no dedicated news endpoint, so "news" reuses the same call and labels
  the results accordingly — graceful, and honest about it.
* ``action="summarize"`` with ``url``: fetches the page (10s timeout,
  200KB cap), strips HTML to text with the stdlib html.parser (no new deps),
  and asks the configured AI provider for a concise summary. Fetch or
  summarization failures degrade to a user-safe message.
"""
from __future__ import annotations

from html.parser import HTMLParser

import httpx

from app.tools.base import BaseTool, ToolError

_DDG_URL = "https://api.duckduckgo.com/"
_TIMEOUT = 15.0
_MAX_RELATED = 5
_FETCH_TIMEOUT = 10.0
_FETCH_CAP_BYTES = 200 * 1024
_SUMMARY_INPUT_CAP = 6000


class _TextExtractor(HTMLParser):
    """Tiny HTML -> text stripper (script/style dropped)."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        collapsed = " ".join("".join(self._parts).split())
        return collapsed


class SearchTool(BaseTool):
    name = "search"
    description = "Searches the web for quick facts and summaries."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "summarize"]},
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "mode": {"type": "string", "enum": ["web", "news"]},
            "url": {"type": "string"},
        },
        "required": [],
    }
    output_schema = {"results": "list"}
    permission = "read"

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "search").strip().lower()
        if action == "summarize":
            return await self._summarize(kwargs.get("url") or "")
        mode = (kwargs.get("mode") or "web").strip().lower()
        if mode not in ("web", "news"):
            mode = "web"
        query = (kwargs.get("query") or "").strip()
        if not query:
            raise ToolError("Search needs a query.")
        limit = max(1, min(int(kwargs.get("limit") or _MAX_RELATED), 10))
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _DDG_URL,
                    params={
                        "q": query,
                        "format": "json",
                        "no_html": "1",
                        "skip_disambig": "1",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            raise ToolError("Web search is unavailable right now.")
        results = self._parse(data, query, limit)
        if mode == "news":
            # No dedicated news endpoint on DDG instant-answer; label honestly.
            for r in results:
                r["source"] = f"{r.get('source', 'DuckDuckGo')} (news)"
        return {"query": query, "mode": mode, "results": results}

    @staticmethod
    def _parse(data: dict, query: str, limit: int) -> list[dict]:
        results: list[dict] = []
        abstract = (data.get("AbstractText") or "").strip()
        if abstract:
            results.append(
                {
                    "title": data.get("Heading") or query,
                    "url": data.get("AbstractURL") or "",
                    "snippet": abstract,
                    "source": data.get("AbstractSource") or "DuckDuckGo",
                }
            )
        for topic in data.get("RelatedTopics") or []:
            if len(results) >= limit + (1 if abstract else 0):
                break
            if not isinstance(topic, dict):
                continue
            # Grouped topics nest their entries under "Topics".
            entries = topic.get("Topics") or [topic]
            for entry in entries:
                if len(results) >= limit + (1 if abstract else 0):
                    break
                text = (entry.get("Text") or "").strip()
                if not text:
                    continue
                results.append(
                    {
                        "title": text[:120],
                        "url": entry.get("FirstURL") or "",
                        "snippet": text,
                        "source": "DuckDuckGo",
                    }
                )
        return results
    async def _summarize(self, url: str) -> dict:
        """Fetch *url* and summarize it with the configured AI provider."""
        from urllib.parse import urlparse

        url = (url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ToolError("I need a valid http(s) URL to summarize, sir.")
        try:
            text = await self._fetch_text(url)
        except ToolError:
            raise
        except Exception:
            raise ToolError("I couldn't fetch that page, sir.")
        if not text:
            raise ToolError("That page had no readable text to summarize, sir.")
        # Lazy import: app.providers must not be imported at tools import time
        # (chat route imports both; keep the dependency one-directional).
        from app.providers import get_provider

        provider = get_provider()
        excerpt = text[:_SUMMARY_INPUT_CAP]
        try:
            summary = await provider.agenerate(
                "Summarize the following web page concisely in a few sentences.",
                context=f"Page URL: {url}\n\n{excerpt}",
            )
        except Exception:
            raise ToolError("I fetched the page but couldn't summarize it, sir.")
        return {"action": "summarize", "url": url, "summary": summary}

    @staticmethod
    async def _fetch_text(url: str) -> str:
        """GET *url* with a 10s timeout and a 200KB body cap; return text."""
        chunks: list[bytes] = []
        total = 0
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": "SPIDEY/0.1 (summarizer)"},
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(16384):
                    total += len(chunk)
                    if total > _FETCH_CAP_BYTES:
                        break
                    chunks.append(chunk)
        raw = b"".join(chunks)
        try:
            html = raw.decode("utf-8", errors="ignore")
        except Exception:
            raise ToolError("I couldn't read that page's content, sir.")
        extractor = _TextExtractor()
        try:
            extractor.feed(html)
        except Exception:
            raise ToolError("I couldn't parse that page, sir.")
        return extractor.text()
