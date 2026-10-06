"""Shared proxy-aware HTTP client for providers.

Sandboxed runtimes route egress through a proxy that needs an explicit
proxy URL and a custom CA bundle (httpx's default no_proxy parsing can
also crash on bracketed IPv6 entries, so env trust is off). On a normal
machine (the user's own PC) neither is set and the client connects
directly with default TLS verification.
"""

import os

import httpx


def make_client(**kwargs) -> httpx.AsyncClient:
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
