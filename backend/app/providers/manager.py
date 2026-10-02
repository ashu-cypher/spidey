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

_DEFAULT_MODELS = {"openai": "gpt-4o-mini"}


def _ollama_default_model() -> str:
    """The default Ollama model: OLLAMA_MODEL env, spec default qwen3:0.6b."""
    from app.config import settings

    return (settings.ollama_model or "").strip() or "qwen3:0.6b"


def _ollama_base_url() -> str:
    from app.config import settings

    return settings.ollama_base_url.rstrip("/")


async def _fetch_ollama_tags(base: str) -> list[str] | None:
    """Model names from ``GET {base}/api/tags``.

    Returns the list on success, ``None`` when Ollama is unreachable or the
    payload is unusable. Never raises. A short timeout: this is a probe, not
    a generation — no model is loaded by /api/tags.
    """
    try:
        import httpx

        # trust_env=False: Ollama is localhost — sandbox proxy env vars must
        # never route or break this probe (httpx chokes on some no_proxy
        # forms, e.g. IPv6 literals, raising InvalidURL instead of skipping
        # the proxy).
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            payload = resp.json()
    except Exception:
        logger.warning("ollama /api/tags probe failed for %s", base)
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return None
    return [
        str(m.get("name") or "")
        for m in models
        if isinstance(m, dict) and m.get("name")
    ]


def _model_available(pulled: list[str], want: str) -> bool:
    """True when ``want`` matches a pulled model name.

    Ollama lists tags as ``name:tag`` (e.g. ``qwen3:0.6b``); a bare
    ``qwen3`` request matches ``qwen3:0.6b`` via the ``want + ":"`` prefix.
    """
    want = (want or "").strip()
    return any(p == want or p.startswith(want + ":") for p in pulled)


async def list_ollama_models() -> tuple[bool, list[str], str]:
    """Models available on the local Ollama server.

    Returns ``(ok, models, message)`` — ``ok=False`` with a human-readable
    message (never a traceback) when Ollama is unreachable.
    """
    pulled = await _fetch_ollama_tags(_ollama_base_url())
    if pulled is None:
        return (
            False,
            [],
            "I couldn't reach the local Ollama server. "
            "Start it with: bash ~/workspace/start-ollama.sh",
        )
    return True, pulled, "ok"

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".provider.json"
_MAX_MODEL_LEN = 128

_lock = threading.RLock()
_cache: dict | None = None


def _default_selection() -> dict:
    # MEW real-agent transformation: the startup probe (lifespan) sets an
    # honest default — ollama when the configured model is present, else
    # rule_based with model_degraded=True. Until the probe runs (tests,
    # direct imports), fall back to the SPIDEY_PROVIDER env default.
    if _startup_default is not None:
        return dict(_startup_default)
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
    """The model name actually used: explicit choice or provider default.

    The Ollama default is OLLAMA_MODEL (spec default qwen3:0.6b), read live
    from settings so env changes and tests are honored.
    """
    if provider == "rule_based":
        return None
    if provider == "ollama":
        return model or _ollama_default_model()
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
            settings.ollama_base_url,
            model=model or _ollama_default_model(),
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
        base = _ollama_base_url()
        want = (model or "").strip() or _ollama_default_model()
        pulled = await _fetch_ollama_tags(base)
        if pulled is None:
            return (
                False,
                "I couldn't access the local AI model. "
                "Please check that Ollama is running.",
            )
        if not _model_available(pulled, want):
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


# --- Startup probe: Ollama as default, honest fallback (MEW Phase 1) --------

# Set once by startup_probe() during app lifespan. When no explicit
# .provider.json choice exists, _default_selection() prefers this over the
# SPIDEY_PROVIDER env var.
_startup_default: dict | None = None

# True when the startup probe found the configured Ollama model unavailable.
# Recorded even when the user explicitly selected another provider — it
# describes the model, not the selection.
_model_degraded: bool = False

# conversation_id -> notice already shown. The degraded notice fires once
# per session (first chat message), never spammed every message.
_notice_shown_for: set[str] = set()

# Exact spec wording for the one-time degraded notice.
DEGRADED_NOTICE = (
    "MEW's local model is unavailable. "
    "Start Ollama or configure another provider."
)

# Actionable setup guide shown with the degraded notice so the user knows
# exactly what to do. MEW keeps working (rule-based) in the meantime, and
# auto-detects Ollama when it comes online — no restart needed.
DEGRADED_SETUP_GUIDE = (
    "To get the full MEW experience:\n"
    "1. Install Ollama from https://ollama.com\n"
    "2. Run: ollama pull qwen3:0.6b\n"
    "3. Run: ollama serve\n"
    "MEW will detect it automatically within a minute — no restart needed. "
    "Or open Settings → Provider to use OpenAI instead."
)


def is_model_degraded() -> bool:
    """True when the startup probe found the configured Ollama model
    unavailable (unreachable server or model not pulled)."""
    with _lock:
        return _model_degraded


def degraded_notice_once(conversation_id: str | None) -> str | None:
    """The one-time degraded notice for this session, or None.

    Returns the spec's exact notice text on the FIRST call for a session
    while the model is degraded; every later call for that session returns
    None. Sessions are keyed by ``conversation_id`` (the frontend persists
    one per conversation); messages without one share a single anonymous
    session key. When the model is healthy this always returns None.
    """
    if not is_model_degraded():
        return None
    key = (conversation_id or "").strip() or "__anonymous__"
    with _lock:
        if key in _notice_shown_for:
            return None
        _notice_shown_for.add(key)
    return DEGRADED_NOTICE


async def startup_probe() -> dict:
    """Probe Ollama at startup; pick the default provider honestly.

    * Configured model present in /api/tags -> default ``ollama``,
      ``model_degraded=False``.
    * Unreachable / model missing -> default ``rule_based``,
      ``model_degraded=True`` (the agent keeps working; the first chat
      message in that state carries the one-time degraded notice).

    An explicit persisted choice (``PUT /api/system/provider`` wrote
    ``.provider.json``) is never overridden — the probe only sets the
    default used when no choice exists. The selection cache is reset so a
    pre-probe ``get_selection()`` (e.g. module-level provider construction
    at import time) cannot go stale.

    Returns ``{"provider", "model", "model_degraded"}`` for startup logging.
    Never raises.
    """
    global _startup_default, _model_degraded
    base = _ollama_base_url()
    want = _ollama_default_model()
    pulled = await _fetch_ollama_tags(base)
    available = pulled is not None and _model_available(pulled, want)
    degraded = not available
    selection = {
        "provider": "ollama" if available else "rule_based",
        "model": want if available else None,
    }
    with _lock:
        _startup_default = dict(selection)
        _model_degraded = degraded
        # Force get_selection() to re-read: no file -> _startup_default;
        # a persisted user choice still wins over the probe default.
        global _cache
        _cache = None
    logger.info(
        "startup model probe: provider=%s model=%s degraded=%s",
        selection["provider"],
        want,
        degraded,
    )
    return {
        "provider": selection["provider"],
        "model": want,
        "model_degraded": degraded,
    }


def _reset_startup_state() -> None:
    """Test hook: clear the startup probe state (default, degraded flag,
    shown-notice keys) so tests start clean."""
    global _startup_default, _model_degraded
    with _lock:
        _startup_default = None
        _model_degraded = False
        _notice_shown_for.clear()
        global _cache
        _cache = None


async def recheck_model() -> bool:
    """Re-probe Ollama when degraded; auto-recover if it's back.

    Called periodically by the model-recovery poller. If the model is not
    degraded, does nothing. If Ollama has come back online with the
    configured model, clears the degraded flag and restores ollama as the
    default — the user doesn't need to restart the backend after starting
    Ollama. Returns True when recovery happened. Never raises.
    """
    global _startup_default, _model_degraded
    with _lock:
        if not _model_degraded:
            return False
    try:
        base = _ollama_base_url()
        want = _ollama_default_model()
        pulled = await _fetch_ollama_tags(base)
        available = pulled is not None and _model_available(pulled, want)
    except Exception:
        return False
    if not available:
        return False
    with _lock:
        _model_degraded = False
        _startup_default = {"provider": "ollama", "model": want}
        _notice_shown_for.clear()
        global _cache
        _cache = None
    logger.info("model recovery: Ollama is back, restored as default")
    return True
