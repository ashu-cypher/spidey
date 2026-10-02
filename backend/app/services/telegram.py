"""Telegram integration for MEW (spec 18-19).

Sends reminder notifications via a Telegram bot. The bot token and chat ID
come ONLY from backend environment variables (TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID) — never exposed to the frontend.

Status is honest:
- Configured + API reachable -> "connected"
- Not configured -> "not_configured"
- Configured but API fails -> "error" with the real reason

Never claims a message was sent unless Telegram's API confirms it.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"

# Backend-only config file (alternative to env vars). Lives in the user's
# home dir, never in the repo, never exposed to the frontend.
_CONFIG_PATH = None


def _config_path():
    global _CONFIG_PATH
    if _CONFIG_PATH is None:
        from pathlib import Path

        _CONFIG_PATH = Path.home() / ".mew_telegram.json"
    return _CONFIG_PATH


def _read_config_file() -> dict:
    try:
        import json

        p = _config_path()
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def save_config(bot_token: str, chat_id: str) -> None:
    """Save Telegram config to the backend-only file."""
    import json

    p = _config_path()
    p.write_text(json.dumps({"bot_token": bot_token, "chat_id": chat_id}))
    try:
        p.chmod(0o600)  # owner-only
    except Exception:
        pass


def _get_token() -> str:
    # Env vars take precedence; config file is the fallback.
    token = settings.telegram_bot_token.strip()
    if token:
        return token
    return _read_config_file().get("bot_token", "").strip()


def _get_chat_id() -> str:
    chat_id = settings.telegram_chat_id.strip()
    if chat_id:
        return chat_id
    return str(_read_config_file().get("chat_id", "")).strip()


def is_configured() -> bool:
    """True when both bot token and chat ID are set (env or config file)."""
    return bool(_get_token()) and bool(_get_chat_id())


async def validate_token(bot_token: str) -> tuple[bool, str]:
    """Check a bot token via getMe WITHOUT requiring a chat ID.

    Used by the config endpoint: the user is submitting both token and
    chat ID together, so we must not fail on the not-yet-saved chat ID.
    """
    token = (bot_token or "").strip()
    if not token:
        return False, "Telegram bot token isn't configured yet."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_TELEGRAM_API}/bot{token}/getMe")
            data = resp.json()
    except Exception as e:
        logger.warning("telegram getMe failed: %s", e)
        return False, "Couldn't reach Telegram — check your network."
    if data.get("ok"):
        name = (data.get("result") or {}).get("username", "bot")
        return True, f"Bot @{name} verified."
    desc = (data.get("description") or "unknown error").strip()
    return False, f"Telegram rejected the token: {desc}"


async def test_connection(bot_token: str | None = None) -> tuple[bool, str]:
    """Verify the bot token works via getMe. Returns (ok, message)."""
    token = (bot_token or "").strip() or _get_token()
    if not token:
        return False, "Telegram bot token isn't configured yet."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_TELEGRAM_API}/bot{token}/getMe")
            data = resp.json()
    except Exception as e:
        logger.warning("telegram getMe failed: %s", e)
        return False, "Couldn't reach Telegram — check your network."
    if data.get("ok"):
        name = (data.get("result") or {}).get("username", "bot")
        if not _get_chat_id():
            return False, (
                f"Bot @{name} works, but chat ID isn't set — "
                "message the bot first, then set your chat ID."
            )
        return True, f"Connected as @{name}."
    desc = (data.get("description") or "unknown error").strip()
    return False, f"Telegram rejected the token: {desc}"


async def send_message(text: str) -> tuple[bool, str]:
    """Send *text* to the configured chat. Returns (sent, message).

    ``sent`` is True ONLY when Telegram's API confirms delivery.
    """
    if not is_configured():
        return False, "Telegram isn't configured yet."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_TELEGRAM_API}/bot{_get_token()}/sendMessage",
                json={
                    "chat_id": _get_chat_id(),
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            data = resp.json()
    except Exception as e:
        logger.warning("telegram sendMessage failed: %s", e)
        return False, "Couldn't reach Telegram to send the message."
    if data.get("ok"):
        return True, "sent"
    desc = (data.get("description") or "unknown error").strip()
    logger.warning("telegram sendMessage rejected: %s", desc)
    return False, f"Telegram rejected the message: {desc}"


async def send_reminder(title: str) -> tuple[bool, str]:
    """Format and send a reminder notification."""
    text = f"🕷 <b>MEW Reminder</b>\n\n{title}"
    return await send_message(text)


def status() -> dict:
    """Honest status dict for the API: never claims connected unless true."""
    if not is_configured():
        return {"configured": False, "state": "not_configured"}
    # Configured — but "connected" requires a live check (done via
    # test_connection, not here, to avoid a network call on every status).
    return {"configured": True, "state": "configured"}
