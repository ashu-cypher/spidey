"""WhatsApp webhook for MEW (Meta WhatsApp Cloud API).

* ``GET /api/whatsapp/webhook`` — Meta's verification handshake: checks
  ``hub.verify_token`` against the configured ``WHATSAPP_VERIFY_TOKEN``
  and returns ``hub.challenge`` as plain text.
* ``POST /api/whatsapp/webhook`` — inbound messages. The
  ``X-Hub-Signature-256`` header is verified against
  ``WHATSAPP_APP_SECRET`` (when set). Only messages from the configured
  recipient number are processed — everything else is silently ignored
  (logged). The text is routed through the SAME agent pipeline as web
  chat (the shared SpideyAgent singleton + workflow engine, channel
  marked via the run's user_id), reusing the same user memory/stores,
  and the reply is sent back via the WhatsApp service.

Replies are processed in a background task so Meta gets its 200 quickly.
Failures are logged honestly; delivery is never claimed unless Meta's
API confirms it.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.services.whatsapp import (
    _get_app_secret,
    _get_recipient,
    _get_verify_token,
    normalize_number,
    send_message,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _extract_incoming(payload: dict) -> list[tuple[str, str]]:
    """Pull (sender, text) pairs from a WhatsApp webhook payload.

    Only text messages are returned; statuses, receipts and media-only
    messages are skipped (logged by the caller as ignored).
    """
    out: list[tuple[str, str]] = []
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            for msg in value.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                sender = str(msg.get("from") or "")
                text = str((msg.get("text") or {}).get("body") or "")
                if sender and text.strip():
                    out.append((sender, text))
    return out


def _sender_allowed(sender: str) -> bool:
    """True only for the single configured recipient number (normalized)."""
    recipient = _get_recipient()
    return bool(sender) and bool(recipient) and (
        normalize_number(sender) == normalize_number(recipient)
    )


@router.get("/api/whatsapp/webhook")
async def verify_webhook(request: Request):
    """Meta verification handshake.

    Meta calls this with ``hub.mode=subscribe``,
    ``hub.verify_token=<your token>`` and ``hub.challenge=<nonce>``. We
    return the challenge as plain text only when the verify token matches
    the configured ``WHATSAPP_VERIFY_TOKEN``.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token") or ""
    challenge = params.get("hub.challenge") or ""
    expected = _get_verify_token()
    if (
        mode == "subscribe"
        and expected
        and token
        and hmac.compare_digest(token, expected)
    ):
        return PlainTextResponse(challenge)
    logger.warning("whatsapp webhook verification failed (mode=%s)", mode)
    raise HTTPException(403, "Webhook verification failed.")


@router.post("/api/whatsapp/webhook")
async def receive_webhook(request: Request):
    """Inbound WhatsApp messages. Verifies the signature, ignores
    non-recipient senders, and hands the text to the agent pipeline."""
    raw = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    app_secret = _get_app_secret()
    if app_secret:
        if not verify_webhook_signature(raw, signature, app_secret):
            logger.warning("whatsapp webhook: signature check failed")
            raise HTTPException(403, "Invalid webhook signature.")
    else:
        # No app secret configured: we cannot verify. Log it honestly and
        # keep the non-recipient sender gate as the remaining protection.
        logger.warning(
            "whatsapp webhook: WHATSAPP_APP_SECRET not set — "
            "accepting payload without signature verification"
        )
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        logger.warning("whatsapp webhook: payload is not valid JSON")
        return {"status": "ignored"}
    if not isinstance(payload, dict) or payload.get("object") not in (
        "whatsapp_business_account",
        "whatsapp",
    ):
        return {"status": "ignored"}
    for sender, text in _extract_incoming(payload):
        if not _sender_allowed(sender):
            # Silently ignore (logged): anyone except the configured
            # recipient must never reach the agent.
            logger.info(
                "whatsapp webhook: ignoring message from non-recipient %s",
                sender,
            )
            continue
        logger.info(
            "whatsapp webhook: processing message from recipient (%d chars)",
            len(text),
        )
        asyncio.create_task(_process_whatsapp_message(text))
    return {"status": "ok"}


async def _process_whatsapp_message(text: str) -> None:
    """Run inbound WhatsApp text through the SAME agent pipeline as web
    chat: the shared SpideyAgent singleton + workflow engine (the run's
    user_id marks the channel as 'whatsapp'), so the same user
    memory/stores apply. The reply goes back via WhatsApp."""
    # Lazy imports: whatsapp.py must stay importable without pulling the
    # whole chat module (and its agent/provider singletons) at import time.
    from app.routes.chat import agent
    from app.workflows.engine import engine

    run = engine.create_run(text, user_id="whatsapp")
    try:
        response = await agent.run(text, run, engine, history=[], lang="auto")
        # Agent normally finishes the run itself; safety net mirrors
        # app/routes/chat.py::_execute.
        if engine.get_run(run.workflow_id).status == "running":
            engine.finish_run(run.workflow_id, "completed", result=response)
    except Exception:
        logger.exception("whatsapp: agent run failed")
        if engine.get_run(run.workflow_id).status == "running":
            engine.finish_run(
                run.workflow_id, "failed", result="WhatsApp message failed."
            )
        return
    reply = (response or "").strip()
    if not reply:
        logger.warning("whatsapp: agent produced no reply — nothing sent")
        return
    sent, msg = await send_message(reply)
    if sent:
        logger.info("whatsapp: reply delivered")
    else:
        # Honest: log the real reason, never claim delivery.
        logger.warning("whatsapp: reply NOT delivered: %s", msg)
