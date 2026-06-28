# subwire — Design Document

A small, configurable HTTP/REST MCP server for talking to everything in a
homelab and beyond — from `GET`ting a Prometheus metric, to chatting with a
local LLM, to calling an authenticated external API and posting the result
somewhere else.

Status: v0.1 design. Single author, MIT-licensed, intended for a public repo.

---

## 1. Why this exists

There are already several "HTTP request" MCP servers. After reviewing them and
their reported issues, three recurring gaps make them awkward for homelab use:

1. **No TLS escape hatch, or only a global one.** Internal services run with
   self-signed certs or an internal CA. Most servers either verify strictly
   (and fail) or expose a single global "disable SSL" switch that also disables
   verification for *public* calls — unacceptable when the same server also
   talks to the outside world. `dkmaker/mcp-rest-api` has `REST_ENABLE_SSL_VERIFY`,
   but it is global.

2. **Base-URL lock-in.** Servers built around a single `REST_BASE_URL` are great
   for testing one API and clumsy for a homelab where you hit a dozen different
   hosts (Prometheus, Portainer, llama.cpp, a router, an external API…).

3. **URL-as-unconstrained-string → SSRF.** Security reviews of the ecosystem
   (OWASP MCP cheat sheet; the `modelcontextprotocol/servers` fetch advisories)
   call out that an LLM-supplied URL, under prompt injection, can be pointed at
   internal-only services or the cloud metadata endpoint. A homelab tool *wants*
   to reach internal services, so the mitigation has to be selective rather than
   "block all private IPs."

Some also wrap the shell `curl` binary, which hands an LLM shell-adjacent
execution — a surface we'd rather not have.

`subwire` is the union of the good ideas with those gaps closed.

---

## 2. Goals and non-goals

### Goals
- **One small tool surface**: a universal `http_request` plus a `list_targets`
  introspection tool. Easy for a model to use correctly.
- **Named targets**: reusable per-host profiles (base URL, default headers,
  auth, TLS policy, allowed methods). Call by `target` + relative path, *or*
  pass a fully absolute URL with no target at all.
- **Scoped TLS policy**: `verify: true | false | /path/to/ca.pem` at the global
  default and per-target levels. The recommended homelab pattern is an internal
  CA at the global default — one trust anchor that validates the whole
  `*.home.lan` fleet with verification on — with per-target overrides reserved
  for genuine exceptions (e.g. an appliance whose cert you can't re-issue). Keep
  strict verification for public hosts independently.
- **Plain HTTP allowed** (gated, default-on for homelab).
- **Selective SSRF protection**: operator-controlled allow/deny lists;
  link-local / cloud-metadata blocked by default; private ranges allowed by
  default (configurable).
- **Optional read-only mode** and **per-target method allowlists** so a
  "monitoring only" deployment can't be talked into a `DELETE`.
- **Dual transport from one codebase**: run as a **stdio** process (for Claude
  Desktop / mcp-remote) or as a standalone **streamable-HTTP** server, chosen by
  a flag. Runs bare or in Docker.
- **Secrets out of the config file**: auth values come from environment
  variables referenced by name.
- **Pluggable auth**: `none | basic | bearer | apikey` built in, with a small,
  self-contained `auth.py` that's easy to extend with another scheme if needed.

### Non-goals (v0.1)
- Built-in signed auth schemes (e.g. AWS SigV4). Out of scope to keep the
  dependency surface minimal; `auth.py` is structured so one can be added later.
- DNS-rebinding-grade network isolation. v0.1 classifies by IP literal and host
  pattern; it does not re-resolve to defeat rebinding. Documented as a known
  limitation.
- Response transformation / scraping. This returns status + headers + body
  (JSON parsed when possible, otherwise text, size-capped). It is an API client,
  not a content extractor — pair it with a fetch/search MCP for that.

---

## 3. Architecture

```
┌────────────────────────────────────────────────────────────┐
│  subwire                                                     │
│                                                              │
│  server.py  ── FastMCP app, defines tools, picks transport   │
│      │                                                       │
│      ├── config.py   load + validate YAML, env overrides     │
│      ├── security.py SecurityPolicy: allow/deny, scheme gate  │
│      ├── auth.py     resolve auth scheme → headers/credentials│
│      └── client.py   build httpx request, exec, shape result  │
└────────────────────────────────────────────────────────────┘
        stdio  ──────────────┐         ┌────────── streamable-http
   (Claude Desktop,          │         │           (standalone / docker,
    mcp-remote)              ▼         ▼            behind your reverse proxy)
```

- **Language / deps**: Python 3.11+, `httpx` (request engine: per-request
  `verify`, HTTP/2, async), `mcp` (official SDK; FastMCP high-level API),
  `pyyaml` (config). Three direct dependencies, all small.
- **Why httpx**: first-class `verify=False | <ca-bundle path>` *per client*,
  clean async, HTTP/2, redirect control. The per-request TLS control is exactly
  the feature the existing servers lack.
- **Why FastMCP**: a single `mcp.run(transport=...)` call switches between stdio
  and streamable-HTTP, satisfying the dual-transport goal without a second
  codepath or an external gateway.

### Request lifecycle
1. Tool call arrives (`http_request`).
2. Resolve `target` (if given) → merge its base URL, headers, auth, TLS, method
   allowlist over the global defaults.
3. Build the final URL (join base + relative, or use absolute).
4. `SecurityPolicy.check(url, method)` — scheme gate, method allowlist,
   allow/deny evaluation. Raises on violation; the violation is returned to the
   model as a clear error, never silently swallowed.
5. Resolve auth → inject credentials/headers.
6. Resolve effective `verify` (call arg > target > global default).
7. Execute via a short-lived `httpx.AsyncClient`.
8. Shape the result: status, selected response headers, elapsed ms, and body —
   JSON-parsed when the content type or payload allows, otherwise text — capped
   at `max_response_bytes` with explicit truncation metadata. Redact configured
   secret headers from any echo.

---

## 4. Security model

The threat is a prompt-injected or confused agent issuing requests the operator
did not intend. Controls, in order of evaluation:

1. **Deny list wins.** Any host matching `security.deny_hosts` (glob) is refused
   outright.
2. **Scheme gate.** `http://` requires `allow_http: true`. (Default true; this
   is a homelab tool. Set false to force TLS-only.)
3. **Method gate.** Global `read_only: true` restricts to `GET`/`HEAD`. A
   target's `allowed_methods` further narrows it. Effective set is the
   intersection.
4. **Address classification** (for the request host):
   - IP literal in **link-local `169.254.0.0/16` / `fd00:ec2::254`** (cloud
     metadata) → **deny** unless `allow_metadata: true`. *On by default as a
     deny* because this is the single most-reported SSRF target and nobody's
     homelab needs it.
   - IP literal in **loopback** → allow iff `allow_loopback` (default true).
   - IP literal in **private** (RFC1918, `fc00::/7`) → allow iff `allow_private`
     (default true — the whole point).
   - **Hostname** matching `security.allow_hosts` glob → allow.
   - Hostname with a LAN-ish suffix (`.home.lan`, `.lan`, `.local`, or bare) →
     treated as private → allow iff `allow_private`.
   - Otherwise (public host / public IP) → allow. External calls are a feature.

Defaults therefore: reach your LAN and the public internet freely, refuse the
metadata endpoint, and give the operator a denylist for anything else (e.g. an
internal admin box the agent should never touch).

5. **TLS is scoped, never silently global.** `verify` resolves at global-default
   then per-target. The intended pattern is an internal CA at the global default
   (whole-LAN trust anchor); per-target `verify: false` is the documented escape
   hatch for an un-reissuable cert. A single self-signed *wildcard* cert is also
   supported as the trust anchor — but it must carry the name in `subjectAltName`
   (CN is ignored by modern TLS) and matches only one label. A global
   `insecure`/`verify: false` exists but is documented as a footgun and is *not*
   the default. If a CA/cert path is missing, the request fails with a clear,
   path-naming error rather than an opaque SSL failure.

6. **Secrets** live only in env vars referenced by name in the config; the
   server redacts configured auth headers from any response echo and from logs.

Known v0.1 limitations (documented, not hidden): no DNS-rebinding defense; host
classification trusts the literal/pattern; allowlist is glob, not regex.

---

## 5. Configuration shape

YAML, with env-var overrides for container-friendliness. Full annotated example
ships as `config.example.yaml`. Sketch:

```yaml
defaults:
  timeout: 30
  verify: /etc/subwire/home-ca.pem   # internal CA: whole-LAN trust anchor
                                     # (true => public-only; false => disabled)
  allow_http: true
  read_only: false                 # true => only GET/HEAD/OPTIONS
  max_response_bytes: 100000

security:
  allow_private: true
  allow_loopback: true
  allow_metadata: false            # block 169.254.169.254 by default
  allow_hosts: []                  # extra explicit allows (globs)
  deny_hosts: []                   # explicit denies (globs); win over everything

targets:
  prometheus:
    base_url: http://prometheus.home.lan:9090
    allowed_methods: [GET]         # monitoring: read-only
  dozzle:
    base_url: https://dozzle.home.lan   # CA-signed => inherits default, verifies
  legacy-appliance:
    base_url: https://nas.home.lan
    verify: false                  # exception: cert can't be re-issued
  smartoffs:
    base_url: https://www.smartoffs.com
    auth: { type: bearer, token_env: SMARTOFFS_TOKEN }
```

Selection precedence for any setting: **explicit tool argument > target value >
global default**.

---

## 6. Tool surface

- `http_request(url, method="GET", headers=None, params=None, json_body=None,
  body=None, target=None, timeout=None, verify=None, max_bytes=None)`
  → JSON: `{ request:{...}, status, ok, elapsed_ms, headers:{...}, body, truncated }`
- `list_targets()` → configured targets (with non-secret metadata) + the active
  security policy summary, so the model can discover what it may call.

Two tools is deliberate: a wide universal verb plus introspection. No
convenience `get`/`post` wrappers — they add surface without adding power, since
`method` defaults to `GET`.

---

## 7. Deployment

- **stdio** (Claude Desktop directly, or via `mcp-remote` from a remote host):
  `subwire --config config.yaml` (defaults to stdio).
- **streamable-HTTP** (standalone or Docker, typically behind your existing
  reverse proxy / on the swarm): `subwire --config config.yaml --http --host
  0.0.0.0 --port 8080`.
- **Docker**: configuration is **baked into the image** at build time —
  `config*.yaml` and anything in `certs/` are copied in — so a deploy needs no
  volume mounts and behaves identically on a laptop or a remote host. Secrets
  stay out of the image and arrive as environment variables at run time. A
  graceful entrypoint falls back to the bundled example (with a message) if no
  `config.yaml` was baked in, rather than crashing. An optional commented-out
  bind mount is provided for live config editing during local development.
- A `Makefile` wraps the common flows (`make config`, `make run`, `make up`) so
  the happy path is a couple of words rather than a remembered command line.

Why bake rather than mount: for a less-experienced user shipping to a remote
host, a wrong mount path is the most common and most confusing failure. Baking
trades "rebuild on config change" (cheap, explicit) for "no mount to get wrong"
(one less thing to debug). The bind mount remains available for those who want
live-reload locally.

**Decoupled deployment (`deploy/`).** To keep a user's data fully separate from
the upstream code — so pulling a new subwire version can never clobber their
config — the repo ships a `deploy/` template: the user's `config.yaml`, `certs/`,
and a version pin live in a folder they own (ideally their own git "deploy repo"),
and the Dockerfile there `pip install`s a pinned subwire straight from GitHub
(`git+https://…@<tag>`) at build time. Updating subwire is a one-line version
bump; the user's data is never in the code tree, so there's nothing to reconcile.
The template works as a plain folder or a git repo, and degrades to an Option B
form (`FROM ghcr.io/…/subwire:<tag>`) once prebuilt images are published — noted
inline in `deploy/Dockerfile` for later.

---

## 8. Roadmap

- v0.1: core `http_request` + `list_targets`, YAML config, per-target TLS,
  selective SSRF policy, stdio + HTTP transports, Docker. (this document)
- v0.2: request templates / saved requests, optional per-target rate limiting.
- later: structured response filters (JSONPath extract), optional DNS
  re-resolution guard, metrics endpoint for self-monitoring.
