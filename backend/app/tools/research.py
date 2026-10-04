"""Deep Research tool — multi-angle web research with synthesized report.

When the user asks to "research [topic]" or "deep dive into [topic]", this
tool:
1. Generates 3-4 sub-queries covering different angles (overview, latest
   developments, pros/cons, outlook).
2. Runs them against the SearchTool (which tries Bing HTML, Bing RSS,
   DuckDuckGo, Wikipedia in order).
3. Synthesizes a structured markdown report with real source citations.
4. SAVES the report to the Research Vault (memory, "[research-vault]"
   namespace) so past research can be recalled later.

No invented facts: every claim comes from a search result with its URL.
If searches come back empty, the report says so honestly.

New action (MEW 2.0): action="recall", query="X" — "what did I research
about X" / "show my research on X" — returns past vault entries
{topic, date, summary} without dumping full reports.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from app.tools.base import BaseTool, ToolError
from app.tools.search import SearchTool

# Sub-query templates: different angles for comprehensive coverage.
# {topic} is the extracted research subject.
_RESEARCH_ANGLES = (
    ("Overview", "{topic}"),
    ("Latest developments", "{topic} latest developments"),
    ("Key points", "{topic} pros and cons analysis"),
    ("Outlook", "{topic} future outlook"),
)

_VAULT_RE = re.compile(
    r"\[research-vault\]\s+(.*?)\s+\|\s+([\d\-/]+)\s+\|\s+sources=(\d+)\s+\|\s+(.*)$"
)


class ResearchTool(BaseTool):
    name = "research"
    description = (
        "Deep research on a topic: searches multiple angles and "
        "synthesizes a structured report with sources. Also recalls "
        "past research from the vault."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["research", "recall"],
                "description": "research (default) or recall past research",
            },
            "topic": {"type": "string"},
            "query": {"type": "string", "description": "recall search text"},
        },
        "required": [],
    }
    output_schema = {"report": "string"}
    permission = "read"

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "research").strip().lower()
        if action == "recall":
            query = (kwargs.get("query") or kwargs.get("topic") or "").strip()
            return {"vault": await self._recall_vault(query)}
        return await self._research(kwargs)

    # -- research ---------------------------------------------------------
    async def _research(self, kwargs: dict) -> dict:
        topic = (kwargs.get("topic") or "").strip()
        if not topic:
            raise ToolError("What should I research?")
        # Clean the topic: strip "research"/"deep dive" instruction words.
        topic = re.sub(
            r"^(?:please\s+)?(?:deep\s+dive\s+into|research|investigate|"
            r"look\s+into)\s+",
            "",
            topic,
            flags=re.IGNORECASE,
        ).strip().rstrip(".?!")
        if not topic:
            raise ToolError("What should I research?")

        search = SearchTool()
        # Run angle searches concurrently for speed.
        results = await asyncio.gather(
            *[
                self._search_angle(search, label, tmpl.format(topic=topic))
                for label, tmpl in _RESEARCH_ANGLES
            ]
        )
        report, n_sources = self._synthesize(topic, results)
        await self._save_to_vault(topic, report, n_sources)
        return {"topic": topic, "report": report}

    async def _save_to_vault(self, topic: str, report: str, n_sources: int) -> None:
        """Persist the report to the Research Vault. Never fails the run."""
        try:
            from app.tools.memory_tool import MemoryTool

            date_str = datetime.now().strftime("%Y-%m-%d")
            summary = re.sub(r"\s+", " ", report)[:400]
            await MemoryTool().execute(
                action="save",
                content=(
                    f"[research-vault] {topic} | {date_str} | "
                    f"sources={n_sources} | {summary}"
                ),
                category="research",
                importance=0.6,
            )
        except Exception:
            pass

    # -- vault recall -----------------------------------------------------
    async def _recall_vault(self, query: str) -> list[dict]:
        from app.tools.memory_tool import MemoryTool

        if not query:
            raise ToolError("What topic should I look up in your research vault?")
        res = await MemoryTool().execute(
            action="recall", query=f"research-vault {query}", limit=20
        )
        # The recall matches on token overlap; "research-vault" alone would
        # match every vault entry, so filter to entries whose topic shares
        # a token with the actual query (same tokenization as memory
        # recall, so short acronyms like "RAG" still work).
        q_toks = MemoryTool._tokens(query) - {
            "research", "vault", "about", "what", "find", "show",
        }
        entries = []
        for m in res.get("results", []):
            content = m.get("content", "")
            if not content.startswith("[research-vault]"):
                continue
            mm = _VAULT_RE.match(content)
            if not mm:
                continue
            if q_toks and not (q_toks & MemoryTool._tokens(mm.group(1))):
                continue
            entries.append({
                "topic": mm.group(1),
                "date": mm.group(2),
                "sources": int(mm.group(3)),
                "summary": mm.group(4),
            })
        # Dedupe by (topic, date); newest memory row wins (memory append-only).
        seen: dict[tuple[str, str], dict] = {}
        for e in entries:
            seen[(e["topic"], e["date"])] = e
        return list(seen.values())

    @staticmethod
    async def _search_angle(
        search: SearchTool, label: str, query: str
    ) -> tuple[str, list[dict]]:
        try:
            out = await search.execute(query=query, limit=3)
            return label, out.get("results", [])
        except Exception:
            return label, []

    @staticmethod
    def _synthesize(
        topic: str, angle_results: list[tuple[str, list[dict]]]
    ) -> tuple[str, int]:
        """Return (report, number_of_unique_sources)."""
        lines = [f"# Research: {topic}", ""]
        all_sources: list[dict] = []
        any_results = False
        for label, results in angle_results:
            if not results:
                continue
            any_results = True
            lines.append(f"## {label}")
            lines.append("")
            for r in results[:3]:
                title = r.get("title", "")
                url = r.get("url", "")
                snippet = (r.get("snippet", "") or "")[:250]
                lines.append(f"- **{title}**")
                if snippet:
                    lines.append(f"  {snippet}")
                if url:
                    lines.append(f"  Source: {url}")
                lines.append("")
                if url and not any(s["url"] == url for s in all_sources):
                    all_sources.append({"title": title, "url": url})
        if not any_results:
            return (
                f"# Research: {topic}\n\n"
                "I couldn't find reliable web results for this topic. "
                "Try rephrasing or being more specific."
            ), 0
        if all_sources:
            lines.append("## Sources")
            lines.append("")
            for s in all_sources:
                lines.append(f"- [{s['title']}]({s['url']})")
        return "\n".join(lines).strip(), len(all_sources)
