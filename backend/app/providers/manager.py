"""Runtime AI-provider management (MEW capability upgrade).

The configured provider can be switched at runtime via
``GET``/``PUT /api/system/provider``. The choice persists server-side in
``backend/.provider.json`` — a small, gitignored JSON file that is NOT
``.env``. Secrets (``OPENAI_API_KEY`` etc.) stay in ``.env`` server-side and
are never written here or exposed through any API.

Semantics:
* No file yet -> the ``SPIDEY_PROVIDER`` env var (``settings.spidey_provider``)
  is the default, exactly as before.
* A file exists -> its ``{"provider": ..., "model": ...}`` selection wins.
* ``app.providers.get_provider()`` consults this module, so the agent's
  per-request provider factory picks up switches with no restart.
* ``probe_provider`` health-checks a candidate *before* it is persisted:
  an unreachable Ollama / OpenAI is rejected with a human-readable message,
  never a traceback.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger("spidey")

AVAILABLE_PROVIDERS = ("rule_based", "ollama", "openai")

# Real LLM providers (as opposed to rule_based and test doubles). The agent
# routes around these: classification and fast-path generation never call
# them; complex intents prefer them with a local fallback.
LLM_PROVIDER_NAMES = ("ollama", "openai")

_DEFAULT_MODELS = {"ollama": "llama3.1", "openai": "gpt-4o-mini"}

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".provider.json"
_MAX_MODEL_LEN = 128

_lock = threading.RLock()
_cache: dict | None = None


def _default_selection() -> dict:
    from app.config import settings

    name = (settings.spidey_provider or "rule_based").strip().lower()
    if name not in AVAILABLE_PROVIDERS:
        name = "rule_based"
    return {"provider": name, "model": None}


def _load() -> dict:
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_selection()
    name = str(data.get("provider") or "").strip().lower()
    if name not in AVAILABLE_PROVIDERS:
        return _default_selection()
    model = data.get("model")
    model = str(model).strip() or None if model is not None else None
    if model and len(model) > _MAX_MODEL_LEN:
        model = None
    return {"provider": name, "model": model}


def get_selection() -> dict:
    """Current ``{"provider", "model"}`` selection (thread-safe)."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load()
        return dict(_cache)


def set_selection(provider: str, model: str | None = None) -> dict:
    """Validate, persist (atomic write), and cache a new selection.

    Raises :class:`ValueError` on an unknown provider name or an invalid
    model. Callers should :func:`probe_provider` first for network providers.
    """
    name = (provider or "").strip().lower()
    if name not in AVAILABLE_PROVIDERS:
        raise ValueError(
            f"Unknown provider {provider!r} — "
            f"choose one of: {', '.join(AVAILABLE_PROVIDERS)}."
        )
    clean_model = (model or "").strip() or None
    if clean_model and len(clean_model) > _MAX_MODEL_LEN:
        raise ValueError("Model name is too long.")
    selection = {"provider": name, "model": clean_model}
    tmp = _STATE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(selection), encoding="utf-8")
        tmp.replace(_STATE_FILE)
    except OSError as exc:
        raise ValueError(f"Could not persist provider choice: {exc}") from exc
    global _cache
    with _lock:
        _cache = dict(selection)
    return dict(selection)


def _reset_cache() -> None:
    """Test hook: force the next :func:`get_selection` to re-read the file."""
    global _cache
    with _lock:
        _cache = None


def effective_model(provider: str, model: str | None) -> str | None:
    """The model name actually used: explicit choice or provider default."""
    if provider == "rule_based":
        return None
    return model or _DEFAULT_MODELS.get(provider)


def build_provider(name: str, model: str | None = None):
    """Instantiate the provider for ``name`` (lazy imports, no cycles)."""
    from app.providers.ollama import OllamaProvider
    from app.providers.openai_provider import OpenAIProvider
    from app.providers.rule_based import RuleBasedProvider

    clean = (name or "").strip().lower()
    if clean == "ollama":
        from app.config import settings

        return OllamaProvider(
            settings.ollama_base_url, model=model or _DEFAULT_MODELS["ollama"]
        )
    if clean == "openai":
        from app.config import settings

        return OpenAIProvider(
            settings.openai_api_key, model=model or _DEFAULT_MODELS["openai"]
        )
    return RuleBasedProvider()


async def probe_provider(name: str, model: str | None = None) -> tuple[bool, str]:
    """Health-check a candidate provider *before* persisting the switch.

    Returns ``(True, "ok")`` or ``(False, <human-readable reason>)`` — never
    raises, never a traceback. Ollama is probed via ``GET /api/tags`` (no
    model load); OpenAI via ``GET /v1/models`` (no tokens spent).
    """
    clean = (name or "").strip().lower()
    if clean == "rule_based":
        return True, "ok"
    if clean == "ollama":
        from app.config import settings

        base = settings.ollama_base_url.rstrip("/")
        want = model or _DEFAULT_MODELS["ollama"]
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base}/api/tags")
                resp.raise_for_status()
                payload = resp.json()
        except Exception:
            logger.warning("ollama probe failed for %s", base)
            return (
                False,
                "I couldn't access the local AI model. "
                "Please check that Ollama is running.",
            )
        pulled = [
            str(m.get("name") or "")
            for m in (payload.get("models") or [])
            if isinstance(m, dict)
        ]
        if not any(p == want or p.startswith(want + ":") for p in pulled):
            return (
                False,
                f"Ollama is running, but the model '{want}' isn't available "
                f"locally. Pull it first with: ollama pull {want}",
            )
        return True, "ok"
    if clean == "openai":
        from app.config import settings

        key = settings.openai_api_key
        if not key:
            return (
                False,
                "The OpenAI API key is not configured on the server "
                "(OPENAI_API_KEY). Add it to backend/.env first.",
            )
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
        except Exception:
            logger.warning("openai probe failed (network)")
            return (
                False,
                "I couldn't reach OpenAI — please check the network "
                "connection and try again.",
            )
        if resp.status_code == 401:
            return (
                False,
                "OpenAI rejected the API key. Please check that "
                "OPENAI_API_KEY is set correctly.",
            )
        if resp.status_code >= 400:
            return (
                False,
                f"OpenAI returned an error (HTTP {resp.status_code}). "
                "Please try again later.",
            )
        return True, "ok"
    return False, f"Unknown provider {name!r}."
