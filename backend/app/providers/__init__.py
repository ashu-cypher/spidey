from app.providers.base import AIProvider, ProviderError
from app.providers.ollama import OllamaProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.rule_based import RuleBasedProvider

__all__ = [
    "AIProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "ProviderError",
    "RuleBasedProvider",
    "get_provider",
]


def get_provider(settings=None) -> AIProvider:
    # MEW capability upgrade: a runtime-persisted selection (PUT
    # /api/system/provider -> backend/.provider.json) wins over the env var.
    # No file -> the SPIDEY_PROVIDER default, exactly as before.
    from app.providers.manager import build_provider, get_selection

    selection = get_selection()
    return build_provider(selection["provider"], selection.get("model"))
