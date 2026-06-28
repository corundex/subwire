"""Resolve a target's auth config into concrete request material.

Secrets are read from environment variables (by name) at request time, so the
config file on disk never contains credentials. Returns a tuple of
(httpx_auth, extra_headers, redact_header_names) so the client can apply auth and
know which headers to redact when echoing the request back to the model.
"""

from __future__ import annotations

import os

import httpx

from .config import AuthConfig


class AuthError(ValueError):
    """Raised when an auth config references a missing or empty env var."""


def _require_env(name: str | None, what: str) -> str:
    if not name:
        raise AuthError(f"auth is missing the env-var name for {what}")
    val = os.getenv(name)
    if not val:
        raise AuthError(f"environment variable {name!r} (for {what}) is unset/empty")
    return val


def resolve_auth(
    auth: AuthConfig,
) -> tuple[httpx.Auth | None, dict[str, str], set[str]]:
    """Return (httpx auth, extra headers, header names to redact)."""
    if auth.type == "none":
        return None, {}, set()

    if auth.type == "basic":
        user = _require_env(auth.username_env, "basic username")
        pwd = _require_env(auth.password_env, "basic password")
        return httpx.BasicAuth(user, pwd), {}, {"authorization"}

    if auth.type == "bearer":
        token = _require_env(auth.token_env, "bearer token")
        return None, {"Authorization": f"Bearer {token}"}, {"authorization"}

    if auth.type == "apikey":
        if not auth.header:
            raise AuthError("apikey auth requires a 'header' name")
        value = _require_env(auth.value_env, "apikey value")
        return None, {auth.header: value}, {auth.header.lower()}

    raise AuthError(f"unknown auth type: {auth.type!r}")
