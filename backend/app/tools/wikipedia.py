"""Wikipedia tool — dedicated encyclopedic lookup (spec 15).

Separate from web search: for "Who was Alan Turing?", "Explain quantum
computing using Wikipedia", "Tell me about the history of RAG".

Uses the Wikipedia API (no key needed):
1. Search for the topic -> get the best matching article
2. Fetch the article extract (intro + key sections)

Honest: if no article is found, says so. Never invents content.
For current events, the agent should prefer web search instead.
"""
from __future__ import annotations

from app.tools.base import BaseTool, ToolError

_WIKI_API = "https://en.wikipedia.org/w/api.php"
_TIMEOUT = 15.0


class WikipediaTool(BaseTool):
    name = "wikipedia"
    description = (
        "Looks up encyclopedic information on Wikipedia. Best for "
        "people, places, concepts, and history — not for current events."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
        },
        "required": ["topic"],
    }
    output_schema = {"article": "string"}
    permission = "read"
    # Two sequential API calls (search + extract); allow room.
    timeout: float = 60.0

    async def _run(self, **kwargs) -> dict:
        # Reuse the proxy-aware client from the search tool so this works
        # in sandboxed environments and on user machines alike.
        from app.tools.search import _make_client

        topic = (kwargs.get("topic") or "").strip()
        if not topic:
            raise ToolError("What should I look up on Wikipedia?")
        # Clean instruction words.
        import re

        topic = re.sub(
            r"^(?:please\s+)?(?:tell\s+me\s+about|explain|who\s+(?:was|is)|"
            r"what\s+(?:was|is))\s+",
            "",
            topic,
            flags=re.IGNORECASE,
        ).strip().rstrip(".?!")
        if not topic:
            raise ToolError("What should I look up on Wikipedia?")

        try:
            async with _make_client(
                timeout=_TIMEOUT,
                headers={"User-Agent": "MEW/1.0 (personal AI agent)"},
            ) as client:
                # Step 1: find the best matching article.
                search_resp = await client.get(
                    _WIKI_API,
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": topic,
                        "srlimit": 1,
                        "format": "json",
                    },
                )
                search_resp.raise_for_status()
                hits = (search_resp.json().get("query") or {}).get("search") or []
                if not hits:
                    raise ToolError(
                        f"I couldn't find a Wikipedia article about \"{topic}\"."
                    )
                title = hits[0]["title"]
                # Step 2: fetch the article extract.
                extract_resp = await client.get(
                    _WIKI_API,
                    params={
                        "action": "query",
                        "prop": "extracts",
                        "exintro": "1",
                        "explaintext": "1",
                        "titles": title,
                        "format": "json",
                    },
                )
                extract_resp.raise_for_status()
                pages = (extract_resp.json().get("query") or {}).get("pages") or {}
                extract = ""
                for page in pages.values():
                    extract = (page.get("extract") or "").strip()
                    break
        except ToolError:
            raise
        except Exception:
            raise ToolError("Wikipedia is unavailable right now.")
        if not extract:
            raise ToolError(
                f"I found the article \"{title}\" but couldn't read it."
            )
        url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        # Cap length: intro is usually enough; the agent can ask for more.
        article = extract[:3000]
        return {
            "topic": topic,
            "title": title,
            "url": url,
            "article": article,
        }
