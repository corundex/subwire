"""Configuration loading and validation for subwire.

Config is YAML on disk, with a handful of environment-variable overrides so the
server is container-friendly. Secrets are never stored in the file directly;
auth blocks reference environment variable *names* which are resolved at request
time (see auth.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "subwire requires PyYAML. Install with: pip install pyyaml"
    ) from exc


class ConfigError(ValueError):
    """Raised when the configuration file is malformed."""


@dataclass
class AuthConfig:
    type: str = "none"  # none | basic | bearer | apikey
    # basic
    username_env: str | None = None
    password_env: str | None = None
    # bearer
    token_env: str | None = None
    # apikey
    header: str | None = None
    value_env: str | None = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "AuthConfig":
        if not isinstance(d, dict):
            raise ConfigError("auth must be a mapping")
        t = d.get("type", "none")
        if t not in {"none", "basic", "bearer", "apikey"}:
            raise ConfigError(f"unknown auth type: {t!r}")
        return AuthConfig(
            type=t,
            username_env=d.get("username_env"),
            password_env=d.get("password_env"),
            token_env=d.get("token_env"),
            header=d.get("header"),
            value_env=d.get("value_env"),
        )


@dataclass
class Target:
    name: str
    base_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    auth: AuthConfig = field(default_factory=AuthConfig)
    verify: bool | str | None = None  # None => inherit global default
    allowed_methods: list[str] | None = None  # None => all (subject to read_only)
    timeout: float | None = None

    @staticmethod
    def from_dict(name: str, d: dict[str, Any]) -> "Target":
        if not isinstance(d, dict):
            raise ConfigError(f"target {name!r} must be a mapping")
        methods = d.get("allowed_methods")
        if methods is not None:
            methods = [m.upper() for m in methods]
        return Target(
            name=name,
            base_url=d.get("base_url"),
            headers={str(k): str(v) for k, v in (d.get("headers") or {}).items()},
            auth=AuthConfig.from_dict(d.get("auth", {})),
            verify=d.get("verify"),
            allowed_methods=methods,
            timeout=d.get("timeout"),
        )


@dataclass
class SecurityConfig:
    allow_private: bool = True
    allow_loopback: bool = True
    allow_metadata: bool = False
    allow_hosts: list[str] = field(default_factory=list)
    deny_hosts: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "SecurityConfig":
        d = d or {}
        return SecurityConfig(
            allow_private=bool(d.get("allow_private", True)),
            allow_loopback=bool(d.get("allow_loopback", True)),
            allow_metadata=bool(d.get("allow_metadata", False)),
            allow_hosts=list(d.get("allow_hosts", []) or []),
            deny_hosts=list(d.get("deny_hosts", []) or []),
        )


@dataclass
class Defaults:
    timeout: float = 30.0
    verify: bool | str = True
    allow_http: bool = True
    read_only: bool = False
    max_response_bytes: int = 100_000
    follow_redirects: bool = True
    # Streamable-HTTP transport DNS-rebinding protection. The MCP SDK rejects
    # requests whose Host header isn't in `allowed_hosts` (defaults to
    # localhost only) with `421 Misdirected Request`. For a LAN deploy you
    # must either list the hostnames clients use (e.g. "subwire.home.lan",
    # "master.home.lan:8081"; "host:*" port-wildcard supported) or disable
    # the check outright.
    allowed_hosts: list[str] = field(default_factory=list)
    disable_dns_rebinding_protection: bool = False

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Defaults":
        d = d or {}
        return Defaults(
            timeout=float(d.get("timeout", 30.0)),
            verify=d.get("verify", True),
            allow_http=bool(d.get("allow_http", True)),
            read_only=bool(d.get("read_only", False)),
            max_response_bytes=int(d.get("max_response_bytes", 100_000)),
            follow_redirects=bool(d.get("follow_redirects", True)),
            allowed_hosts=[str(h) for h in (d.get("allowed_hosts") or [])],
            disable_dns_rebinding_protection=bool(
                d.get("disable_dns_rebinding_protection", False)
            ),
        )


@dataclass
class Config:
    defaults: Defaults = field(default_factory=Defaults)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    targets: dict[str, Target] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Config":
        d = d or {}
        targets_raw = d.get("targets", {}) or {}
        if not isinstance(targets_raw, dict):
            raise ConfigError("targets must be a mapping of name -> target")
        targets = {
            name: Target.from_dict(name, body) for name, body in targets_raw.items()
        }
        return Config(
            defaults=Defaults.from_dict(d.get("defaults", {})),
            security=SecurityConfig.from_dict(d.get("security", {})),
            targets=targets,
        )

    def _apply_env_overrides(self) -> None:
        """A few env knobs so containers can tweak without editing the file."""
        if (v := os.getenv("SUBWIRE_READ_ONLY")) is not None:
            self.defaults.read_only = v.strip().lower() in {"1", "true", "yes"}
        if (v := os.getenv("SUBWIRE_ALLOW_HTTP")) is not None:
            self.defaults.allow_http = v.strip().lower() in {"1", "true", "yes"}
        if (v := os.getenv("SUBWIRE_VERIFY")) is not None:
            low = v.strip().lower()
            if low in {"true", "1", "yes"}:
                self.defaults.verify = True
            elif low in {"false", "0", "no"}:
                self.defaults.verify = False
            else:
                self.defaults.verify = v  # path to CA bundle
        if (v := os.getenv("SUBWIRE_MAX_RESPONSE_BYTES")) is not None:
            self.defaults.max_response_bytes = int(v)
        if (v := os.getenv("SUBWIRE_ALLOWED_HOSTS")) is not None:
            self.defaults.allowed_hosts = [
                h.strip() for h in v.split(",") if h.strip()
            ]
        if (v := os.getenv("SUBWIRE_DISABLE_DNS_REBINDING_PROTECTION")) is not None:
            self.defaults.disable_dns_rebinding_protection = (
                v.strip().lower() in {"1", "true", "yes"}
            )


def load_config(path: str | os.PathLike[str] | None) -> Config:
    """Load config from YAML, or return permissive defaults if no path is given.

    A missing path is *not* an error: subwire is usable with zero config (no
    named targets, default security policy). This keeps the "just point it at a
    URL" path frictionless.
    """
    if path is None:
        cfg = Config()
        cfg._apply_env_overrides()
        return cfg

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"top-level config in {p} must be a mapping")
    cfg = Config.from_dict(raw)
    cfg._apply_env_overrides()
    return cfg
