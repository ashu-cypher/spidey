"""WhatsApp integration tests: signature verification, config round-trip,
credential validation (mocked HTTP), send confirmation honesty, recipient
gating, and the Meta webhook handshake."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.routes import whatsapp as wr
from app.services import whatsapp as wa


# --- helpers ---------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal stand-in for the proxy-aware httpx.AsyncClient."""

    def __init__(self, get_payload=None, post_payload=None, exc=None):
        self.get_payload = get_payload
        self.post_payload = post_payload
        self.exc = exc
        self.last_post = None
        self.last_get = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        self.last_get = (url, params)
        if self.exc:
            raise self.exc
        return _FakeResponse(self.get_payload)

    async def post(self, url, headers=None, json=None):
        self.last_post = (url, headers, json)
        if self.exc:
            raise self.exc
        return _FakeResponse(self.post_payload)


def _fake_client(monkeypatch, **kwargs):
    client = _FakeClient(**kwargs)
    monkeypatch.setattr(wa, "_make_client", lambda **kw: client)
    return client


def _clean_env(monkeypatch):
    for var in (
        "WHATSAPP_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_RECIPIENT",
        "WHATSAPP_VERIFY_TOKEN",
        "WHATSAPP_APP_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    # settings were bound at import; blank them directly.
    from app.config import settings

    monkeypatch.setattr(settings, "whatsapp_token", "")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "")
    monkeypatch.setattr(settings, "whatsapp_recipient", "")
    monkeypatch.setattr(settings, "whatsapp_verify_token", "")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No env creds; config file redirected to a tmp path."""
    _clean_env(monkeypatch)
    cfg = tmp_path / ".mew_whatsapp.json"
    monkeypatch.setattr(wa, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(wa, "_last_send_ts", 0.0)
    return cfg


def _wa_payload(sender="919876543210", body="hello mew"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": sender,
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()


# --- signature verification ------------------------------------------------


def test_verify_signature_valid():
    body = b'{"object":"whatsapp_business_account"}'
    sig = _sign(body, "s3cret")
    assert wa.verify_webhook_signature(body, sig, "s3cret") is True


def test_verify_signature_wrong_secret():
    body = b"{}"
    sig = _sign(body, "s3cret")
    assert wa.verify_webhook_signature(body, sig, "other") is False


def test_verify_signature_tampered_body():
    sig = _sign(b'{"a":1}', "s3cret")
    assert wa.verify_webhook_signature(b'{"a":2}', sig, "s3cret") is False


def test_verify_signature_missing_header():
    assert wa.verify_webhook_signature(b"{}", None, "s3cret") is False


def test_verify_signature_bad_prefix():
    body = b"{}"
    raw = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert wa.verify_webhook_signature(body, raw, "s3cret") is False


def test_verify_signature_no_secret_configured():
    body = b"{}"
    sig = _sign(body, "s3cret")
    assert wa.verify_webhook_signature(body, sig, "") is False


# --- config save/load round-trip -------------------------------------------


def test_config_round_trip(isolated):
    wa.save_config("tok123", "pnid456", "919876543210", "vtok", "asec")
    assert wa._get_token() == "tok123"
    assert wa._get_phone_number_id() == "pnid456"
    assert wa._get_recipient() == "919876543210"
    assert wa._get_verify_token() == "vtok"
    assert wa._get_app_secret() == "asec"
    assert wa.is_configured() is True
    # Owner-only permissions on the secrets file.
    import os
    import stat

    mode = stat.S_IMODE(os.stat(str(isolated)).st_mode)
    assert mode == 0o600


def test_is_configured_requires_all_three(isolated):
    assert wa.is_configured() is False
    wa.save_config("tok", "pnid", "")
    assert wa.is_configured() is False
    wa.save_config("tok", "pnid", "91111")
    assert wa.is_configured() is True


def test_env_takes_precedence_over_file(isolated, monkeypatch):
    wa.save_config("file-token", "file-pnid", "91111")
    monkeypatch.setenv("WHATSAPP_TOKEN", "env-token")
    from app.config import settings

    monkeypatch.setattr(settings, "whatsapp_token", "env-token")
    assert wa._get_token() == "env-token"
    assert wa._get_phone_number_id() == "file-pnid"  # file still fallback


def test_status_never_leaks_secrets(isolated):
    wa.save_config("tok123", "pnid456", "919876543210", "vtok", "asec")
    st = wa.status()
    assert st == {"configured": True, "state": "configured"}
    blob = json.dumps(st)
    for secret in ("tok123", "pnid456", "vtok", "asec"):
        assert secret not in blob


def test_status_not_configured(isolated):
    assert wa.status() == {"configured": False, "state": "not_configured"}


# --- credential validation (mocked HTTP) -----------------------------------


async def test_validate_credentials_ok(isolated, monkeypatch):
    client = _fake_client(
        monkeypatch, get_payload={"name": "Test Business", "id": "1"}
    )
    ok, msg = await wa.validate_credentials("tok123", "pnid456")
    assert ok is True
    assert "Test Business" in msg
    url, params = client.last_get
    assert url == "https://graph.facebook.com/v21.0/me"
    assert params == {"access_token": "tok123"}


async def test_validate_credentials_rejected(isolated, monkeypatch):
    _fake_client(
        monkeypatch,
        get_payload={"error": {"message": "Invalid OAuth access token."}},
    )
    ok, msg = await wa.validate_credentials("bad")
    assert ok is False
    assert "Invalid OAuth access token" in msg


async def test_validate_credentials_network_failure(isolated, monkeypatch):
    _fake_client(monkeypatch, exc=RuntimeError("boom"))
    ok, msg = await wa.validate_credentials("tok")
    assert ok is False
    assert "Couldn't reach" in msg


async def test_validate_credentials_empty_token(isolated):
    ok, msg = await wa.validate_credentials("")
    assert ok is False
    assert "isn't configured" in msg


# --- send_message honesty ----------------------------------------------------


async def test_send_confirmed_only_on_message_id(isolated, monkeypatch):
    wa.save_config("tok", "pnid999", "919876543210")
    client = _fake_client(
        monkeypatch, post_payload={"messages": [{"id": "wamid.abc123"}]}
    )
    ok, msg = await wa.send_message("hello")
    assert ok is True
    assert "wamid.abc123" in msg
    url, headers, payload = client.last_post
    assert url == "https://graph.facebook.com/v21.0/pnid999/messages"
    assert headers == {"Authorization": "Bearer tok"}
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "919876543210"
    assert payload["text"] == {"body": "hello"}


async def test_send_rejected_by_meta(isolated, monkeypatch):
    wa.save_config("tok", "pnid", "91111")
    _fake_client(
        monkeypatch,
        post_payload={"error": {"message": "(#132000) bad param"}},
    )
    ok, msg = await wa.send_message("hello")
    assert ok is False
    assert "Meta rejected" in msg


async def test_send_unconfirmed_not_claimed(isolated, monkeypatch):
    wa.save_config("tok", "pnid", "91111")
    _fake_client(monkeypatch, post_payload={"ok": True})  # no messages[].id
    ok, msg = await wa.send_message("hello")
    assert ok is False
    assert "didn't confirm" in msg


async def test_send_not_configured(isolated):
    ok, msg = await wa.send_message("hello")
    assert ok is False
    assert "isn't configured" in msg


async def test_send_throttled_to_one_per_second(isolated, monkeypatch):
    import time

    wa.save_config("tok", "pnid", "91111")
    _fake_client(
        monkeypatch, post_payload={"messages": [{"id": "wamid.1"}]}
    )
    start = time.monotonic()
    await wa.send_message("one")
    await wa.send_message("two")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.9  # second send waited out the 1s window


# --- recipient gating --------------------------------------------------------


def test_normalize_number():
    assert wa.normalize_number("+91 98765 43210") == "919876543210"
    assert wa.normalize_number("919876543210") == "919876543210"


def test_sender_allowed_only_recipient(isolated):
    wa.save_config("tok", "pnid", "+91 98765 43210")
    assert wr._sender_allowed("919876543210") is True
    assert wr._sender_allowed("+91 98765 43210") is True
    assert wr._sender_allowed("911111111111") is False
    assert wr._sender_allowed("") is False


def test_extract_incoming_text_only():
    payload = _wa_payload()
    assert wr._extract_incoming(payload) == [("919876543210", "hello mew")]


def test_extract_incoming_skips_non_text():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "91111", "type": "image"},
                                {
                                    "from": "91111",
                                    "type": "text",
                                    "text": {"body": "  "},
                                },
                            ],
                            "statuses": [{"id": "x"}],
                        }
                    }
                ]
            }
        ],
    }
    assert wr._extract_incoming(payload) == []
    assert wr._extract_incoming({}) == []
    assert wr._extract_incoming(None) == []


# --- webhook handshake + route -----------------------------------------------


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def test_verify_handshake_ok(client, monkeypatch, isolated):
    monkeypatch.setattr(wr, "_get_verify_token", lambda: "tok123")
    r = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "tok123",
            "hub.challenge": "CHALLENGE42",
        },
    )
    assert r.status_code == 200
    assert r.text == "CHALLENGE42"


def test_verify_handshake_wrong_token(client, monkeypatch, isolated):
    monkeypatch.setattr(wr, "_get_verify_token", lambda: "tok123")
    r = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "nope",
            "hub.challenge": "CHALLENGE42",
        },
    )
    assert r.status_code == 403


def test_verify_handshake_no_token_configured(client, monkeypatch, isolated):
    monkeypatch.setattr(wr, "_get_verify_token", lambda: "")
    r = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "tok123",
            "hub.challenge": "CHALLENGE42",
        },
    )
    assert r.status_code == 403


def test_webhook_rejects_bad_signature(client, monkeypatch, isolated):
    monkeypatch.setattr(wr, "_get_app_secret", lambda: "appsecret")
    body = json.dumps(_wa_payload()).encode()
    r = client.post(
        "/api/whatsapp/webhook",
        content=body,
        headers={"x-hub-signature-256": "sha256=deadbeef"},
    )
    assert r.status_code == 403


def test_webhook_ignores_non_recipient(client, monkeypatch, isolated):
    monkeypatch.setattr(wr, "_get_app_secret", lambda: "appsecret")
    monkeypatch.setattr(wr, "_get_recipient", lambda: "911111111111")
    calls = []

    async def recorder(text):
        calls.append(text)

    monkeypatch.setattr(wr, "_process_whatsapp_message", recorder)
    body = json.dumps(_wa_payload(sender="919876543210")).encode()
    r = client.post(
        "/api/whatsapp/webhook",
        content=body,
        headers={"x-hub-signature-256": _sign(body, "appsecret")},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert calls == []  # never reached the agent


async def test_webhook_processes_recipient(client, monkeypatch, isolated):
    import asyncio

    monkeypatch.setattr(wr, "_get_app_secret", lambda: "appsecret")
    monkeypatch.setattr(wr, "_get_recipient", lambda: "919876543210")
    calls = []

    async def recorder(text):
        calls.append(text)

    monkeypatch.setattr(wr, "_process_whatsapp_message", recorder)
    body = json.dumps(_wa_payload()).encode()
    r = client.post(
        "/api/whatsapp/webhook",
        content=body,
        headers={"x-hub-signature-256": _sign(body, "appsecret")},
    )
    assert r.status_code == 200
    await asyncio.sleep(0.2)  # let the background task run
    assert calls == ["hello mew"]
