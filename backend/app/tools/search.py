"""Phase 5 — web search tool (DuckDuckGo Instant Answer API, no key needed).

Honest scope: the DDG instant-answer endpoint returns an abstract plus
related topics — great for quick facts, not a full web index. Any failure
degrades to a user-safe ToolError, never a traceback.
"""
from __future__ import annotations

import httpx

from app.tools.base import BaseTool, ToolError

_DDG_URL = "https://api.duckduckgo.com/"
_TIMEOUT = 15.0
_MAX_RELATED = 5


class SearchTool(BaseTool):
    name = "search"
    description = "Searches the web for quick facts and summaries."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    }
    output_schema = {"results": "list"}
    permission = "read"

    async def _run(self, **kwargs) -> dict:
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
        return {"query": query, "results": self._parse(data, query, limit)}

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
