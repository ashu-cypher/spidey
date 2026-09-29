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
    if settings is None:
        from app.config import settings as _settings

        settings = _settings
    name = settings.spidey_provider.strip().lower()
    if name == "openai":
        return OpenAIProvider(settings.openai_api_key)
    if name == "ollama":
        return OllamaProvider(settings.ollama_base_url)
    return RuleBasedProvider()
