"""Security policy: the gate every request passes through before execution.

The threat model is a prompt-injected or confused agent issuing requests the
operator did not intend. This module makes the allow/deny decision *explicit and
selective* — a homelab tool must reach internal hosts on purpose, so we cannot
simply block all private addresses. Instead we block the one thing nobody's
homelab needs (cloud metadata) by default, give the operator allow/deny globs,
and gate scheme and method.

Evaluation order (first decisive rule wins):
  1. deny_hosts glob match            -> DENY
  2. scheme http but allow_http=False -> DENY
  3. method not permitted             -> DENY
  4. address classification           -> ALLOW/DENY per policy
"""

from __future__ import annotations

import fnmatch
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import Config

# Link-local / cloud-metadata ranges. 169.254.0.0/16 covers the canonical
# 169.254.169.254 AWS/GCP/Azure metadata IP; fd00:ec2::254 is the IPv6 form.
_METADATA_NETS = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fd00:ec2::/32"),
]

_LAN_SUFFIXES = (".home.lan", ".lan", ".local", ".internal")


class PolicyError(PermissionError):
    """Raised when a request is refused by the security policy."""


@dataclass
class Decision:
    allowed: bool
    reason: str


class SecurityPolicy:
    def __init__(self, config: Config):
        self.cfg = config
        self.sec = config.security
        self.defaults = config.defaults

    # -- public API -----------------------------------------------------------

    def permitted_methods(self, target_methods: list[str] | None) -> set[str]:
        """Effective method set = read_only gate ∩ target allowlist."""
        all_methods = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
        if self.defaults.read_only:
            base = {"GET", "HEAD", "OPTIONS"}
        else:
            base = set(all_methods)
        if target_methods is not None:
            base &= {m.upper() for m in target_methods}
        return base

    def check(self, url: str, method: str, target_methods: list[str] | None) -> None:
        """Raise PolicyError if the request is not permitted. Otherwise return."""
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname or ""

        if scheme not in {"http", "https"}:
            raise PolicyError(f"unsupported scheme {scheme!r}; only http/https")

        # 1. explicit deny list wins over everything
        if self._matches(host, self.sec.deny_hosts):
            raise PolicyError(f"host {host!r} is in deny_hosts")

        # 2. scheme gate
        if scheme == "http" and not self.defaults.allow_http:
            raise PolicyError("plain http is disabled (allow_http=false)")

        # 3. method gate
        method = method.upper()
        allowed = self.permitted_methods(target_methods)
        if method not in allowed:
            raise PolicyError(
                f"method {method} not permitted (allowed: {sorted(allowed)})"
            )

        # 4. address classification
        decision = self._classify(host)
        if not decision.allowed:
            raise PolicyError(decision.reason)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _matches(host: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(host, pat) for pat in patterns)

    def _classify(self, host: str) -> Decision:
        if not host:
            return Decision(False, "missing host")

        # explicit allow list short-circuits to allow
        if self._matches(host, self.sec.allow_hosts):
            return Decision(True, "host in allow_hosts")

        ip = self._as_ip(host)
        if ip is not None:
            return self._classify_ip(ip)

        return self._classify_hostname(host)

    @staticmethod
    def _as_ip(host: str) -> ipaddress._BaseAddress | None:
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            return None

    def _classify_ip(self, ip: ipaddress._BaseAddress) -> Decision:
        for net in _METADATA_NETS:
            if ip in net:
                if self.sec.allow_metadata:
                    return Decision(True, "metadata allowed by policy")
                return Decision(
                    False,
                    f"{ip} is a link-local/metadata address (blocked; "
                    "set security.allow_metadata=true to override)",
                )
        if ip.is_loopback:
            return (
                Decision(True, "loopback allowed")
                if self.sec.allow_loopback
                else Decision(False, f"loopback {ip} blocked (allow_loopback=false)")
            )
        if ip.is_private:
            return (
                Decision(True, "private allowed")
                if self.sec.allow_private
                else Decision(False, f"private {ip} blocked (allow_private=false)")
            )
        # public IP
        return Decision(True, "public address allowed")

    def _classify_hostname(self, host: str) -> Decision:
        lowered = host.lower()
        is_lanish = (
            "." not in lowered  # bare hostname, e.g. "prometheus"
            or lowered.endswith(_LAN_SUFFIXES)
        )
        if is_lanish:
            return (
                Decision(True, "LAN hostname allowed")
                if self.sec.allow_private
                else Decision(
                    False, f"LAN hostname {host!r} blocked (allow_private=false)"
                )
            )
        # public DNS name
        return Decision(True, "public hostname allowed")
