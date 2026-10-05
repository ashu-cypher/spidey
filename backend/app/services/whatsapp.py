"""WhatsApp integration for MEW (Meta WhatsApp Cloud API).

Sends notifications / chat replies via the WhatsApp Cloud API and receives
inbound messages through a Meta webhook (see ``app/routes/whatsapp.py``).

The credentials come ONLY from backend environment variables
(``WHATSAPP_TOKEN``, ``WHATSAPP_PHONE_NUMBER_ID``, ``WHATSAPP_RECIPIENT``)
or the backend-only config file (``~/.mew_whatsapp.json``) — never exposed
to the frontend.

Status is honest:
- Configured + API reachable -> "connected"
- Not configured -> "not_configured"
- Configured but API fails -> "error" with the real reason

Never claims a message was sent unless Meta's API confirms it
(``messages[0].id`` present in the response).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time

import httpx

from app.config import settings


def _make_client(**kwargs) -> httpx.AsyncClient:
    """Proxy-aware client (same pattern as telegram/search): works in
    sandboxes and on normal machines. WhatsApp API calls must not crash on
    proxy env vars."""
    proxy = (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("HTTP_PROXY")
    )
    verify: str | bool = True
    ca = os.environ.get("SSL_CERT_FILE")
    if ca and os.path.exists(ca):
        verify = ca
    return httpx.AsyncClient(
        trust_env=False, proxy=proxy, verify=verify, **kwargs
    )

logger = logging.getLogger(__name__)

_GRAPH_API = "https://graph.facebook.com/v21.0"

# Backend-only config file (alternative to env vars). Lives in the user's
# home dir, never in the repo, never exposed to the frontend.
_CONFIG_PATH = None


def _config_path():
    global _CONFIG_PATH
    if _CONFIG_PATH is None:
        from pathlib import Path

        _CONFIG_PATH = Path.home() / ".mew_whatsapp.json"
    return _CONFIG_PATH


def _read_config_file() -> dict:
    try:
        import json

        p = _config_path()
        if p.exists():
            data = json.loads(p.read_text())
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_config(
    token: str,
    phone_number_id: str,
    recipient: str,
    verify_token: str = "",
    app_secret: str = "",
) -> None:
    """Save WhatsApp config to the backend-only file."""
    import json

    p = _config_path()
    p.write_text(
        json.dumps(
            {
                "token": token,
                "phone_number_id": phone_number_id,
                "recipient": recipient,
                "verify_token": verify_token,
                "app_secret": app_secret,
            }
        )
    )
    try:
        p.chmod(0o600)  # owner-only
    except Exception:
        pass


def _get_token() -> str:
    # Env vars take precedence; config file is the fallback.
    token = settings.whatsapp_token.strip()
    if token:
        return token
    return str(_read_config_file().get("token", "")).strip()


def _get_phone_number_id() -> str:
    pnid = settings.whatsapp_phone_number_id.strip()
    if pnid:
        return pnid
    return str(_read_config_file().get("phone_number_id", "")).strip()


def _get_recipient() -> str:
    recipient = settings.whatsapp_recipient.strip()
    if recipient:
        return recipient
    return str(_read_config_file().get("recipient", "")).strip()


def _get_verify_token() -> str:
    token = settings.whatsapp_verify_token.strip()
    if token:
        return token
    return str(_read_config_file().get("verify_token", "")).strip()


def _get_app_secret() -> str:
    secret = settings.whatsapp_app_secret.strip()
    if secret:
        return secret
    return str(_read_config_file().get("app_secret", "")).strip()


def save_simple_config(phone: str, apikey: str) -> None:
    """Save the SIMPLE (CallMeBot) WhatsApp config — no business account.

    Free path for personal reminders: the user messages CallMeBot's
    WhatsApp bot once to get an API key, then MEW sends via a plain
    HTTPS GET. Send-only (no inbound webhook possible).
    """
    import json

    p = _config_path()
    p.write_text(
        json.dumps(
            {
                "provider": "callmebot",
                "phone": normalize_number(phone),
                "apikey": apikey.strip(),
            }
        )
    )
    try:
        p.chmod(0o600)  # owner-only
    except Exception:
        pass


def _get_provider() -> str:
    """'callmebot' when the simple config is present, else 'meta'."""
    cfg = _read_config_file()
    if cfg.get("provider") == "callmebot" and cfg.get("apikey"):
        return "callmebot"
    return "meta"


def _get_simple_phone() -> str:
    return normalize_number(str(_read_config_file().get("phone", "")))


def _get_simple_apikey() -> str:
    return str(_read_config_file().get("apikey", "")).strip()


def normalize_number(number: str) -> str:
    """Digits only — WhatsApp `from` fields arrive without '+' or spaces,
    so compare recipients in normalized form."""
    return "".join(ch for ch in (number or "") if ch.isdigit())


def is_configured() -> bool:
    """True when either provider is ready:
    - callmebot: phone + apikey in the config file, or
    - meta: token + phone number ID + recipient (env vars or config file).
    """
    if _get_provider() == "callmebot":
        return bool(_get_simple_phone()) and bool(_get_simple_apikey())
    return bool(_get_token()) and bool(_get_phone_number_id()) and bool(
        _get_recipient()
    )


async def validate_credentials(
    token: str | None = None,
    phone_number_id: str | None = None,
) -> tuple[bool, str]:
    """Verify the token via Graph API ``/me`` WITHOUT requiring the phone
    number ID / recipient to be saved yet (config flow submits everything
    together). Returns (ok, message)."""
    tok = (token if token is not None else _get_token()).strip()
    if not tok:
        return False, "WhatsApp token isn't configured yet."
    try:
        async with _make_client(timeout=10.0) as client:
            resp = await client.get(
                f"{_GRAPH_API}/me", params={"access_token": tok}
            )
            data = resp.json()
    except Exception as e:
        logger.warning("whatsapp /me failed: %s", e)
        return False, "Couldn't reach Meta's Graph API — check your network."
    err = data.get("error")
    if err:
        msg = str(err.get("message", "unknown error")).strip()
        return False, f"Meta rejected the token: {msg}"
    name = str(data.get("name", "your WhatsApp Business Account")).strip()
    pnid = (phone_number_id if phone_number_id is not None
            else _get_phone_number_id()).strip()
    extra = f" Phone number ID {pnid} will be used for sending." if pnid else ""
    return True, f"Token verified for {name}.{extra}"


async def test_connection(
    token: str | None = None,
    phone_number_id: str | None = None,
    recipient: str | None = None,
) -> tuple[bool, str]:
    """Verify token + that the full send path is configured. Never claims
    success unless Meta's API confirms the token."""
    tok = (token if token is not None else _get_token()).strip()
    pnid = (phone_number_id if phone_number_id is not None
            else _get_phone_number_id()).strip()
    to = (recipient if recipient is not None else _get_recipient()).strip()
    if not tok:
        return False, "WhatsApp token isn't configured yet."
    ok, message = await validate_credentials(tok, pnid)
    if not ok:
        return False, message
    if not pnid:
        return False, message + " But the phone number ID isn't set."
    if not to:
        return False, message + " But the recipient number isn't set."
    return True, message + " Ready to send."


# --- Rate limiting: max 1 message/second ------------------------------------
# Simple in-memory throttle: concurrent senders serialize on the lock, each
# waiting out the remainder of the 1-second window since the previous send.
_send_lock = asyncio.Lock()
_last_send_ts = 0.0
_SEND_MIN_INTERVAL = 1.0


async def _throttle():
    global _last_send_ts
    async with _send_lock:
        now = time.monotonic()
        wait = _SEND_MIN_INTERVAL - (now - _last_send_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_send_ts = time.monotonic()


_MAX_TEXT_LEN = 4000  # Meta caps text messages at 4096 chars


async def send_message(text: str) -> tuple[bool, str]:
    """Send *text* to the configured recipient. Returns (sent, message).

    Provider is chosen automatically:
    - ``callmebot``: free send-only path, no business account needed.
      ``sent`` is True when CallMeBot's API accepts the request.
    - ``meta``: WhatsApp Cloud API. ``sent`` is True ONLY when Meta's API
      confirms delivery (``messages[0].id`` present).

    Outbound sends are throttled to at most 1 message per second.
    """
    if not is_configured():
        return False, "WhatsApp isn't configured yet."
    body = (text or "").strip()
    if not body:
        return False, "Nothing to send — the message is empty."
    if _get_provider() == "callmebot":
        return await _send_via_callmebot(body)
    if len(body) > _MAX_TEXT_LEN:
        body = body[:_MAX_TEXT_LEN] + "…(truncated)"
    await _throttle()
    try:
        async with _make_client(timeout=15.0) as client:
            resp = await client.post(
                f"{_GRAPH_API}/{_get_phone_number_id()}/messages",
                headers={"Authorization": f"Bearer {_get_token()}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": _get_recipient(),
                    "type": "text",
                    "text": {"body": body},
                },
            )
            data = resp.json()
    except Exception as e:
        logger.warning("whatsapp send failed: %s", e)
        return False, "Couldn't reach Meta's Graph API to send the message."
    err = data.get("error")
    if err:
        msg = str(err.get("message", "unknown error")).strip()
        logger.warning("whatsapp send rejected: %s", msg)
        return False, f"Meta rejected the message: {msg}"
    messages = data.get("messages") or []
    if messages and messages[0].get("id"):
        return True, f"sent (id {messages[0]['id']})"
    logger.warning("whatsapp send unconfirmed: %s", data)
    return False, "Meta didn't confirm the message — not sent."


async def _send_via_callmebot(body: str) -> tuple[bool, str]:
    """Send via CallMeBot's free WhatsApp API (no business account needed).

    The user gets their apikey by messaging CallMeBot's WhatsApp bot once:
    save +34 644 71 56 43 as a contact, send "I allow callmebot to send me
    messages", and CallMeBot replies with the apikey.
    """
    import urllib.parse

    phone = _get_simple_phone()
    apikey = _get_simple_apikey()
    if not phone or not apikey:
        return False, "WhatsApp simple setup isn't complete yet."
    await _throttle()
    params = {
        "phone": phone,
        "text": body[:1500],  # CallMeBot is happiest with shorter texts
        "apikey": apikey,
    }
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        params
    )
    try:
        async with _make_client(timeout=20.0) as client:
            resp = await client.get(url)
            result = resp.text
    except Exception as e:
        logger.warning("callmebot send failed: %s", e)
        return False, "Couldn't reach CallMeBot — check your network."
    # CallMeBot returns a human-readable string; errors contain "ERROR".
    if "ERROR" in result.upper():
        logger.warning("callmebot rejected: %s", result[:200])
        return False, f"CallMeBot rejected the message: {result[:200]}"
    return True, "sent via WhatsApp"


async def send_reminder(title: str) -> tuple[bool, str]:
    """Format and send a reminder notification (spider theme, plain text —
    WhatsApp has no HTML parse mode)."""
    text = f"🕷 *MEW Reminder*\n\n{title}"
    return await send_message(text)


def verify_webhook_signature(
    raw_body: bytes, signature: str | None, app_secret: str | None = None
) -> bool:
    """Check the ``X-Hub-Signature-256`` header: ``sha256=<hex(HMAC-SHA256(
    raw_body, app_secret))>``. Uses compare_digest (timing-safe)."""
    secret = (app_secret if app_secret is not None else _get_app_secret()).strip()
    if not secret or not signature:
        return False
    sig = signature.strip()
    if not sig.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body or b"", hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig[len("sha256="):], expected)


def status() -> dict:
    """Honest status dict for the API: never claims connected unless a live
    check confirmed it (done via test_connection, not here, to avoid a
    network call on every status poll)."""
    if not is_configured():
        return {"configured": False, "state": "not_configured"}
    return {
        "configured": True,
        "state": "configured",
        "provider": _get_provider(),  # 'callmebot' (simple) or 'meta'
    }
