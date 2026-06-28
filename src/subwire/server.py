"""subwire MCP server.

Defines two tools — `http_request` and `list_targets` — over a FastMCP app, and
chooses the transport (stdio or streamable-http) at runtime. The same code path
serves Claude Desktop over stdio and a standalone/Docker HTTP deployment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .client import HttpClient, RequestError
from .config import Config, ConfigError, load_config

_INSTRUCTIONS = """\
subwire is a configurable HTTP/REST client for homelab and external APIs.

Use `http_request` to call any endpoint. Either pass a fully-qualified `url`, or
pass a `target` (a named profile) plus a relative path as `url`. Call
`list_targets` first to see configured targets and the security policy.

For JSON request bodies use `json_body`; for raw text use `body`. Methods other
than GET may be restricted by the server's read-only mode or a target's method
allowlist — the error message will say so.
"""


def build_app(config: Config) -> tuple[FastMCP, HttpClient]:
    mcp = FastMCP("subwire", instructions=_INSTRUCTIONS)
    client = HttpClient(config)

    @mcp.tool()
    async def http_request(
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
    ) -> str:
        """Make an HTTP request to a homelab or external endpoint.

        Args:
            url: Absolute URL, or a path relative to the chosen target's base_url.
            method: HTTP method (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS).
            headers: Extra request headers (override target defaults).
            params: Query-string parameters.
            json_body: A JSON-serializable body; sets Content-Type to JSON.
            body: A raw string body (use instead of json_body for non-JSON).
            target: Name of a configured target profile to use.
            timeout: Per-request timeout in seconds.
            verify: TLS verification override: true, false, or a CA bundle path.
                    Use false for a self-signed internal host.
            max_bytes: Cap on returned body size in bytes.

        Returns:
            A JSON string: {request, status, ok, elapsed_ms, headers, body, truncated}.
        """
        try:
            result = await client.request(
                url=url,
                method=method,
                headers=headers,
                params=params,
                json_body=json_body,
                body=body,
                target=target,
                timeout=timeout,
                verify=verify,
                max_bytes=max_bytes,
            )
            return json.dumps(result, indent=2, default=str)
        except RequestError as exc:
            return json.dumps({"error": str(exc)}, indent=2)

    @mcp.tool()
    async def list_targets() -> str:
        """List configured target profiles and the active security policy.

        Returns names, base URLs, allowed methods, TLS posture, and auth type for
        each target (no secrets), plus the global security settings — so you know
        what you can call and how.
        """
        targets = {}
        for name, t in config.targets.items():
            targets[name] = {
                "base_url": t.base_url,
                "allowed_methods": t.allowed_methods or "all (subject to read_only)",
                "verify": t.verify if t.verify is not None else "(inherit default)",
                "auth": t.auth.type,
                "default_headers": sorted(t.headers.keys()),
            }
        policy = {
            "read_only": config.defaults.read_only,
            "allow_http": config.defaults.allow_http,
            "verify_default": config.defaults.verify,
            "allow_private": config.security.allow_private,
            "allow_loopback": config.security.allow_loopback,
            "allow_metadata": config.security.allow_metadata,
            "allow_hosts": config.security.allow_hosts,
            "deny_hosts": config.security.deny_hosts,
        }
        return json.dumps({"targets": targets, "policy": policy}, indent=2)

    return mcp, client


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="subwire",
        description="A configurable HTTP/REST MCP server for homelab and beyond.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=os.getenv("SUBWIRE_CONFIG"),
        help="Path to YAML config (or set SUBWIRE_CONFIG). Optional.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run as a streamable-HTTP server instead of stdio.",
    )
    parser.add_argument("--host", default=os.getenv("SUBWIRE_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("SUBWIRE_PORT", "8080"))
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        metavar="HOST",
        help=(
            "Host header value to accept for the streamable-HTTP transport "
            "(repeatable). Supports 'host:*' port-wildcard. Required for LAN "
            "deploys reached by hostname; otherwise the MCP SDK rejects them "
            "with 421 Misdirected Request."
        ),
    )
    parser.add_argument(
        "--disable-dns-rebinding-protection",
        action="store_true",
        default=False,
        help=(
            "Disable the streamable-HTTP transport's Host/Origin allowlist "
            "entirely. Use only for trusted LAN deployments."
        ),
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"subwire: configuration error: {exc}", file=sys.stderr)
        print(
            "subwire: check your config file, or run without --config to start "
            "with no targets.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    mcp, _ = build_app(config)

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port

        # Effective transport security: CLI flags override config.
        allowed_hosts = list(config.defaults.allowed_hosts)
        if args.allowed_host:
            allowed_hosts.extend(args.allowed_host)
        disable_protection = (
            args.disable_dns_rebinding_protection
            or config.defaults.disable_dns_rebinding_protection
        )
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=not disable_protection,
            allowed_hosts=allowed_hosts,
        )

        print(
            f"subwire: HTTP server on http://{args.host}:{args.port}  "
            f"(MCP endpoint: /mcp) — {len(config.targets)} target(s) configured",
            file=sys.stderr,
        )
        if disable_protection:
            print(
                "subwire: DNS-rebinding protection DISABLED — accepting any "
                "Host header. Use only on a trusted network.",
                file=sys.stderr,
            )
        elif args.host in {"0.0.0.0", "::"} and not allowed_hosts:
            # The SDK default allowlist is localhost-only, so binding to all
            # interfaces and reaching the server by hostname will return 421.
            # Surface the cause at startup rather than letting the user chase
            # cryptic "Invalid Host header" lines.
            print(
                "subwire: WARNING — bound to all interfaces but no "
                "defaults.allowed_hosts configured. Clients reaching this "
                "server by hostname (e.g. subwire.home.lan) will get 421 "
                "Misdirected Request. Add the hostnames to "
                "defaults.allowed_hosts in config.yaml, pass --allowed-host, "
                "or use --disable-dns-rebinding-protection.",
                file=sys.stderr,
            )
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
