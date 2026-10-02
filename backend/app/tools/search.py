"""Web search tool — real multi-source search, no API key needed.

Sources are tried in order until one returns results:
1. **Bing HTML** — real web results (title/link/snippet) parsed from the
   search page; redirect URLs are decoded. Primary source.
2. **Bing RSS** — structured fallback; simple but can return news sources
   for news-y queries.
3. **DuckDuckGo instant-answer** — thin (abstract + related topics) but
   reliable; kept as a fallback.
4. **Wikipedia API** — factual fallback for encyclopedic queries.

Network note: outbound HTTP goes through an explicit proxy-aware client
(``_make_client``). In sandboxed environments the egress proxy needs an
explicit proxy URL and CA bundle; on a normal machine (the user's own PC)
both are absent and the client connects directly with default TLS
verification. Any failure degrades to a user-safe ToolError, never a
traceback. Results always carry ``title``/``url``/``snippet``/``source`` so
answers can cite real sources; a source is never claimed when the search
did not run.

``mode``: "web" or "news" (news just labels results; Bing RSS serves both).
``action="summarize"`` with ``url``: fetches the page (10s timeout,
200KB cap), strips HTML to text with the stdlib html.parser (no new deps),
and asks the configured AI provider for a concise summary.
"""
from __future__ import annotations

import os
import base64
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse, parse_qs

import httpx

from app.tools.base import BaseTool, ToolError

_BING_RSS_URL = "https://www.bing.com/search"
_DDG_URL = "https://api.duckduckgo.com/"
_WIKI_URL = "https://en.wikipedia.org/w/api.php"
_TIMEOUT = 15.0
_MAX_RESULTS = 5
_FETCH_TIMEOUT = 10.0
_FETCH_CAP_BYTES = 200 * 1024
_SUMMARY_INPUT_CAP = 6000
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def _make_client(**kwargs) -> httpx.AsyncClient:
    """Build an httpx client that works both in sandboxes and on user PCs.

    Sandboxed runtimes route egress through a proxy that needs an explicit
    proxy URL and a custom CA bundle (the default no_proxy parsing in
    httpx can also crash on bracketed IPv6 entries, so env trust is off).
    On a normal machine neither is set and the client connects directly
    with default TLS verification.
    """
    proxy = (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("HTTP_PROXY")
    )
    verify: str | bool = True
    ca = os.environ.get("SSL_CERT_FILE")
    if ca and os.path.exists(ca):
        verify = ca
    return httpx.AsyncClient(
        trust_env=False, proxy=proxy, verify=verify, **kwargs
    )


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


class _BingHTMLParser(HTMLParser):
    """Extract web results from Bing's HTML (``li.b_algo`` blocks).

    Bing wraps result links in a redirect (``/ck/a?...&u=a1<base64>``);
    the real URL is base64-decoded from the ``u`` parameter.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._in_algo = False
        self._in_h2 = False
        self._in_a = False
        self._in_caption = False
        self._in_p = False
        self._cur: dict | None = None
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if tag == "li" and "b_algo" in a.get("class", ""):
            self._in_algo = True
            self._cur = {"title": "", "url": "", "snippet": ""}
            self._title_parts = []
            self._snippet_parts = []
        elif self._in_algo and tag == "h2":
            self._in_h2 = True
        elif (
            self._in_algo
            and self._in_h2
            and tag == "a"
            and self._cur is not None
            and not self._cur["url"]
        ):
            self._in_a = True
            self._cur["url"] = self._decode_bing_url(a.get("href", ""))
        elif self._in_algo and tag == "div" and "b_caption" in a.get("class", ""):
            self._in_caption = True
        elif self._in_caption and tag == "p":
            self._in_p = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._in_algo:
            if self._cur is not None:
                self._cur["title"] = " ".join("".join(self._title_parts).split())
                self._cur["snippet"] = " ".join("".join(self._snippet_parts).split())
                if self._cur["title"] and self._cur["url"]:
                    self.results.append(self._cur)
            self._in_algo = False
            self._cur = None
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "a":
            self._in_a = False
        elif tag == "div" and self._in_caption:
            self._in_caption = False
        elif tag == "p":
            self._in_p = False

    def handle_data(self, data: str) -> None:
        if self._in_a and self._cur is not None:
            self._title_parts.append(data)
        elif self._in_p and self._cur is not None:
            self._snippet_parts.append(data)

    @staticmethod
    def _decode_bing_url(href: str) -> str:
        href = (href or "").replace("&amp;", "&")
        if not href:
            return ""
        if href.startswith("http") and "bing.com/ck/a" not in href:
            return href
        try:
            u = parse_qs(urlparse(href).query).get("u", [""])[0]
            if u.startswith("a1"):
                u = u[2:]
            u += "=" * (-len(u) % 4)
            return base64.b64decode(u).decode("utf-8", errors="ignore")
        except Exception:
            return ""


class SearchTool(BaseTool):
    name = "search"
    description = "Searches the web for current information, facts, and summaries."
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
            raise ToolError("What should I search for?")
        limit = max(1, min(int(kwargs.get("limit") or _MAX_RESULTS), 10))

        results: list[dict] = []
        source_used = ""
        any_source_responded = False
        try:
            async with _make_client(timeout=_TIMEOUT, headers=_UA) as client:
                for source_name, search_fn in (
                    ("bing", self._bing_html_search),
                    ("bing-rss", self._bing_rss_search),
                    ("duckduckgo", self._ddg_instant_search),
                    ("wikipedia", self._wikipedia_search),
                ):
                    try:
                        results = await search_fn(client, query, limit)
                    except Exception:
                        results = []
                    if results:
                        any_source_responded = True
                    # Skip junk: Bing sometimes returns degraded/unrelated
                    # results (bot mitigation). Fall through to next source.
                    if results and not self._is_relevant(results, query):
                        results = []
                    if results:
                        source_used = source_name
                        break
        except Exception:
            raise ToolError("Web search is unavailable right now.")
        if not results:
            if any_source_responded:
                raise ToolError(
                    f"I searched the web but couldn't find relevant results "
                    f"for \"{query}\"."
                )
            raise ToolError("Web search is unavailable right now.")
        if mode == "news":
            for r in results:
                r["source"] = f"{r.get('source', source_used)} (news)"
        return {
            "query": query,
            "mode": mode,
            "search_source": source_used,
            "results": results,
        }

    @staticmethod
    def _is_relevant(results: list[dict], query: str) -> bool:
        """Heuristic: at least one result title should contain a significant
        query word. Filters out Bing's degraded/junk responses (e.g. oven
        repair pages for a person name) so the chain falls through to the
        next source instead of presenting garbage."""
        words = [
            w.lower()
            for w in query.split()
            if len(w) > 3 and w.lower() not in ("what", "when", "where", "which", "how", "latest")
        ]
        if not words:
            return True
        for r in results:
            title = (r.get("title") or "").lower()
            if any(w in title for w in words):
                return True
        return False

    # --- source 1: Bing HTML ------------------------------------------------
    @staticmethod
    async def _bing_html_search(
        client: httpx.AsyncClient, query: str, limit: int
    ) -> list[dict]:
        """Real web results by parsing Bing's HTML (no key required)."""
        resp = await client.get(
            _BING_RSS_URL,
            params={"q": query},
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        )
        resp.raise_for_status()
        parser = _BingHTMLParser()
        try:
            parser.feed(resp.text)
        except Exception:
            return []
        results: list[dict] = []
        for r in parser.results[:limit]:
            # Skip Bing-internal links that slipped through.
            if "bing.com" in r["url"]:
                continue
            results.append(
                {
                    "title": r["title"][:200],
                    "url": r["url"],
                    "snippet": r["snippet"][:500],
                    "source": "Bing",
                }
            )
        return results

    # --- source 2: Bing RSS -------------------------------------------------
    @staticmethod
    async def _bing_rss_search(
        client: httpx.AsyncClient, query: str, limit: int
    ) -> list[dict]:
        """Real web results via Bing's RSS endpoint (no key required)."""
        import xml.etree.ElementTree as ET

        resp = await client.get(
            _BING_RSS_URL,
            params={"q": query, "format": "rss"},
            # Bypass aggressive caching proxies that would otherwise serve
            # stale RSS for unrelated queries.
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []
        results: list[dict] = []
        for item in channel.findall("item")[:limit]:
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            snippet = (item.findtext("description") or "").strip()
            if not title or not url:
                continue
            # Skip Bing-internal redirect wrappers.
            if "bing.com" in url and "/search" in url:
                continue
            results.append(
                {
                    "title": title[:200],
                    "url": url,
                    "snippet": snippet[:500],
                    "source": "Bing",
                }
            )
        return results

    # --- source 3: DuckDuckGo instant-answer --------------------------------
    @staticmethod
    async def _ddg_instant_search(
        client: httpx.AsyncClient, query: str, limit: int
    ) -> list[dict]:
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
        return SearchTool._parse_ddg(data, query, limit)

    @staticmethod
    def _parse_ddg(data: dict, query: str, limit: int) -> list[dict]:
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

    # --- source 4: Wikipedia API --------------------------------------------
    @staticmethod
    async def _wikipedia_search(
        client: httpx.AsyncClient, query: str, limit: int
    ) -> list[dict]:
        resp = await client.get(
            _WIKI_URL,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[dict] = []
        for hit in (data.get("query") or {}).get("search") or []:
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            snippet = _TextExtractor()
            try:
                snippet.feed(hit.get("snippet") or "")
                snippet_text = snippet.text()
            except Exception:
                snippet_text = ""
            results.append(
                {
                    "title": title[:200],
                    "url": "https://en.wikipedia.org/wiki/"
                    + quote_plus(title.replace(" ", "_")),
                    "snippet": snippet_text[:500],
                    "source": "Wikipedia",
                }
            )
        return results

    async def _summarize(self, url: str) -> dict:
        """Fetch *url* and summarize it with the configured AI provider."""
        from urllib.parse import urlparse

        url = (url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ToolError("I need a valid http(s) URL to summarize.")
        try:
            text = await self._fetch_text(url)
        except ToolError:
            raise
        except Exception:
            raise ToolError("I couldn't fetch that page.")
        if not text:
            raise ToolError("That page had no readable text to summarize.")
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
            raise ToolError("I fetched the page but couldn't summarize it.")
        return {"action": "summarize", "url": url, "summary": summary}

    @staticmethod
    async def _fetch_text(url: str) -> str:
        """GET *url* with a 10s timeout and a 200KB body cap; return text."""
        chunks: list[bytes] = []
        total = 0
        async with _make_client(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
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
            raise ToolError("I couldn't read that page's content.")
        extractor = _TextExtractor()
        try:
            extractor.feed(html)
        except Exception:
            raise ToolError("I couldn't parse that page.")
        return extractor.text()
