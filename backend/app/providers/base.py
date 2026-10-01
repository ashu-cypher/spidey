from abc import ABC, abstractmethod


class ProviderError(Exception):
    """Raised when an AI provider cannot classify or generate."""


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
