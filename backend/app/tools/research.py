"""Deep Research tool — multi-angle web research with synthesized report.

When the user asks to "research [topic]" or "deep dive into [topic]", this
tool:
1. Generates 3-4 sub-queries covering different angles (overview, latest
   developments, pros/cons, outlook).
2. Runs them against the SearchTool (which tries Bing HTML, Bing RSS,
   DuckDuckGo, Wikipedia in order).
3. Synthesizes a structured markdown report with real source citations.

No invented facts: every claim comes from a search result with its URL.
If searches come back empty, the report says so honestly.
"""
from __future__ import annotations

import asyncio

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


class ResearchTool(BaseTool):
    name = "research"
    description = (
        "Deep research on a topic: searches multiple angles and "
        "synthesizes a structured report with sources."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
        },
        "required": ["topic"],
    }
    output_schema = {"report": "string"}
    permission = "read"

    async def _run(self, **kwargs) -> dict:
        topic = (kwargs.get("topic") or "").strip()
        if not topic:
            raise ToolError("What should I research?")
        # Clean the topic: strip "research"/"deep dive" instruction words.
        import re

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
        report = self._synthesize(topic, results)
        return {"topic": topic, "report": report}

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
    ) -> str:
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
            )
        if all_sources:
            lines.append("## Sources")
            lines.append("")
            for s in all_sources:
                lines.append(f"- [{s['title']}]({s['url']})")
        return "\n".join(lines).strip()
