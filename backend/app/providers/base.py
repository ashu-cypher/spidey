from abc import ABC, abstractmethod


class ProviderError(Exception):
    """Raised when an AI provider cannot classify or generate."""


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def aclassify_intent(self, text: str) -> dict:
        """Classify user intent. Returns a dict with keys:
        intent, requires_memory, requires_tools, tools, response_mode."""
        raise NotImplementedError

    @abstractmethod
    async def agenerate(self, text: str, context: str = "") -> str:
        """Generate a reply. If context is non-empty, it carries agent-composed facts."""
        raise NotImplementedError
