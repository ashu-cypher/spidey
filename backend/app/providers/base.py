from abc import ABC, abstractmethod
from inspect import Parameter, signature
from typing import AsyncIterator


class ProviderError(Exception):
    """Raised when an AI provider cannot classify or generate."""


def _accepts_kwarg(fn, name: str) -> bool:
    """True when ``fn`` takes keyword ``name`` (or **kwargs)."""
    try:
        params = signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(p.kind == Parameter.VAR_KEYWORD for p in params.values())


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def aclassify_intent(
        self, text: str, history: list[dict] | None = None
    ) -> dict:
        """Classify user intent. Returns a dict with keys:
        intent, requires_memory, requires_tools, tools, response_mode.

        ``history`` is the recent conversation (list of
        {"role": "user"|"assistant", "content": str}) so the classifier can
        resolve pronoun follow-ups; it may be None."""
        raise NotImplementedError

    @abstractmethod
    async def agenerate(
        self, text: str, context: str = "", history: list[dict] | None = None
    ) -> str:
        """Generate a reply. If context is non-empty, it carries agent-composed facts.

        ``history`` lets the provider resolve pronouns ("it", "that") against
        the recent conversation; None means no history was supplied."""
        raise NotImplementedError

    async def agenerate_stream(
        self,
        message: str,
        context: str = "",
        history: list[dict] | None = None,
        lang: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a reply as an async generator of text chunks.

        Default implementation composes the whole reply with ``agenerate``
        and yields it as a single chunk. Providers with real streaming
        override this. ``lang`` is forwarded to ``agenerate`` only when its
        signature accepts it (backward compatible with older providers)."""
        kwargs: dict = {}
        if lang is not None and _accepts_kwarg(self.agenerate, "lang"):
            kwargs["lang"] = lang
        if _accepts_kwarg(self.agenerate, "history"):
            kwargs["history"] = history
        result = await self.agenerate(message, context=context, **kwargs)
        yield result
