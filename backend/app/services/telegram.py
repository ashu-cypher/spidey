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


def is_configured() -> bool:
    """True when both bot token and chat ID are set."""
    return bool(settings.telegram_bot_token.strip()) and bool(
        settings.telegram_chat_id.strip()
    )


async def test_connection() -> tuple[bool, str]:
    """Verify the bot token works via getMe. Returns (ok, message)."""
    if not settings.telegram_bot_token.strip():
        return False, "Telegram bot token isn't configured yet."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_TELEGRAM_API}/bot{settings.telegram_bot_token}/getMe"
            )
            data = resp.json()
    except Exception as e:
        logger.warning("telegram getMe failed: %s", e)
        return False, "Couldn't reach Telegram — check your network."
    if data.get("ok"):
        name = (data.get("result") or {}).get("username", "bot")
        if not settings.telegram_chat_id.strip():
            return False, (
                f"Bot @{name} works, but TELEGRAM_CHAT_ID isn't set — "
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
                f"{_TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": settings.telegram_chat_id,
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
