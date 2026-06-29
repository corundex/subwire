"""The request engine: take tool arguments + config, perform the HTTP call,
and shape a structured, size-capped result for the model.

Precedence for every setting is: explicit argument > target value > global
default. TLS `verify` is resolved here and passed per-request to httpx, which is
the whole reason this server can keep strict verification for public hosts while
skipping it for one self-signed internal box.
"""

from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import certifi
import httpx

from .auth import resolve_auth
from .config import Config, Target
from .security import PolicyError, SecurityPolicy

# Response headers worth surfacing to the model; the rest are noise.
_INTERESTING_HEADERS = {
    "content-type",
    "content-length",
    "location",
    "server",
    "date",
    "cache-control",
    "x-ratelimit-remaining",
    "x-ratelimit-limit",
    "retry-after",
    "www-authenticate",
}


@dataclass
class RequestError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return self.message


class HttpClient:
    def __init__(self, config: Config):
        self.cfg = config
        self.policy = SecurityPolicy(config)
        # Merged-trust SSLContexts, cached per CA-bundle path. Built lazily
        # in _resolve_verify so we don't re-parse certifi (~150 certs) on
        # every request.
        self._ssl_cache: dict[str, ssl.SSLContext] = {}

    def _resolve_target(self, name: str | None) -> Target | None:
        if name is None:
            return None
        target = self.cfg.targets.get(name)
        if target is None:
            known = ", ".join(sorted(self.cfg.targets)) or "(none configured)"
            raise RequestError(
                f"unknown target {name!r}. Configured targets: {known}"
            )
        return target

    @staticmethod
    def _build_url(target: Target | None, url: str) -> str:
        if target is None or not target.base_url:
            return url
        # absolute URL passed alongside a target: honor the absolute URL
        if url.lower().startswith(("http://", "https://")):
            return url
        base = target.base_url
        if not base.endswith("/"):
            base += "/"
        return urljoin(base, url.lstrip("/"))

    def _resolve_verify(
        self, target: Target | None, verify_arg: bool | str | None
    ) -> bool | str | ssl.SSLContext:
        if verify_arg is not None:
            verify = verify_arg
        elif target is not None and target.verify is not None:
            verify = target.verify
        else:
            verify = self.cfg.defaults.verify

        # bool stays bool (True = system trust only; False = no verification).
        if isinstance(verify, bool):
            return verify

        # CA-bundle path: fail early and clearly if the file is missing,
        # then return a merged trust store that combines certifi's public
        # roots with the internal CA. Without the merge, pointing `verify`
        # at a homelab CA bundle silently REPLACES httpx's default trust
        # store, so every public HTTPS call (api.github.com, etc.) fails
        # with an opaque "unable to get local issuer certificate" error.
        if isinstance(verify, str):
            if not Path(verify).is_file():
                raise RequestError(
                    f"TLS verify is set to CA bundle {verify!r}, but that file "
                    "does not exist (check the path, or the volume mount if "
                    "running in Docker)"
                )
            ctx = self._ssl_cache.get(verify)
            if ctx is None:
                ctx = ssl.create_default_context(cafile=certifi.where())
                ctx.load_verify_locations(cafile=verify)
                self._ssl_cache[verify] = ctx
            return ctx

        return verify

    async def request(
        self,
        *,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        body: str | None = None,
        target: str | None = None,
        timeout: float | None = None,
        verify: bool | str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        tgt = self._resolve_target(target)
        final_url = self._build_url(tgt, url)
        method = method.upper()

        # Security gate (raises PolicyError -> surfaced as an error result)
        target_methods = tgt.allowed_methods if tgt else None
        try:
            self.policy.check(final_url, method, target_methods)
        except PolicyError as exc:
            raise RequestError(f"blocked by security policy: {exc}") from exc

        # Merge headers: target defaults < explicit args
        merged_headers: dict[str, str] = {}
        if tgt:
            merged_headers.update(tgt.headers)
        if headers:
            merged_headers.update(headers)

        # Auth
        redact: set[str] = set()
        httpx_auth = None
        if tgt:
            try:
                httpx_auth, auth_headers, redact = resolve_auth(tgt.auth)
            except Exception as exc:  # AuthError and friends
                raise RequestError(f"auth error for target {tgt.name!r}: {exc}")
            merged_headers.update(auth_headers)

        eff_timeout = (
            timeout
            if timeout is not None
            else (tgt.timeout if tgt and tgt.timeout else self.cfg.defaults.timeout)
        )
        eff_verify = self._resolve_verify(tgt, verify)
        eff_max = max_bytes if max_bytes is not None else self.cfg.defaults.max_response_bytes

        content = None
        json_payload = None
        if json_body is not None:
            json_payload = json_body
        elif body is not None:
            content = body

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                verify=eff_verify,
                timeout=eff_timeout,
                follow_redirects=self.cfg.defaults.follow_redirects,
            ) as client:
                resp = await client.request(
                    method,
                    final_url,
                    headers=merged_headers or None,
                    params=params or None,
                    json=json_payload,
                    content=content,
                    auth=httpx_auth,
                )
        except httpx.ConnectError as exc:
            raise RequestError(f"connection failed to {final_url}: {exc}")
        except httpx.TimeoutException:
            raise RequestError(f"request to {final_url} timed out after {eff_timeout}s")
        except httpx.HTTPError as exc:
            raise RequestError(f"http error calling {final_url}: {exc}")
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)

        return self._shape(
            resp=resp,
            method=method,
            final_url=final_url,
            elapsed_ms=elapsed_ms,
            max_bytes=eff_max,
            redact=redact,
            sent_headers=merged_headers,
        )

    @staticmethod
    def _shape(
        *,
        resp: httpx.Response,
        method: str,
        final_url: str,
        elapsed_ms: float,
        max_bytes: int,
        redact: set[str],
        sent_headers: dict[str, str],
    ) -> dict[str, Any]:
        raw = resp.text or ""
        truncated = False
        if len(raw.encode("utf-8", errors="ignore")) > max_bytes:
            raw = raw.encode("utf-8", errors="ignore")[:max_bytes].decode(
                "utf-8", errors="ignore"
            )
            truncated = True

        # Try to parse JSON bodies into structured data for the model.
        parsed: Any = raw
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype.lower() and not truncated:
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                parsed = raw

        echo_headers = {
            k: ("<redacted>" if k.lower() in redact else v)
            for k, v in sent_headers.items()
        }
        resp_headers = {
            k: v for k, v in resp.headers.items() if k.lower() in _INTERESTING_HEADERS
        }

        return {
            "request": {
                "method": method,
                "url": final_url,
                "headers": echo_headers,
            },
            "status": resp.status_code,
            "ok": resp.is_success,
            "elapsed_ms": elapsed_ms,
            "headers": resp_headers,
            "body": parsed,
            "truncated": truncated,
        }
