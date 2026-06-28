# subwire

A small, configurable **HTTP/REST MCP server** for talking to everything in your
homelab — and beyond. Check a Prometheus metric, poke a container's API, chat
with a local LLM, or call an authenticated external service and pipe the result
somewhere else. One universal `http_request` tool, named per-host profiles,
per-host TLS control, and a security policy built for a network where reaching
internal hosts is the *point*.

> Status: v0.1. MIT licensed. Issues and PRs welcome.

## Why another HTTP MCP server?

The existing ones are close but trip on three things for homelab use:

- **TLS is all-or-nothing.** Internal boxes use self-signed certs or an internal
  CA; most servers either verify strictly (and fail) or expose a single global
  "disable SSL" switch that also weakens your *public* calls. subwire resolves
  `verify` **per scope** — point the global default at your internal CA so the
  whole `*.home.lan` fleet validates with verification **on**, skip it for one
  un-reissuable appliance, keep strict verification for public hosts — all
  independently. See [Internal CA / wildcard certs](#internal-ca--wildcard-certs).
- **Single base-URL lock-in.** A homelab hits many hosts. subwire lets you define
  as many named targets as you like, or just pass an absolute URL with no target.
- **URL-as-blind-string → SSRF.** Security reviews of the MCP ecosystem flag that
  an LLM-supplied URL can be steered (via prompt injection) at internal-only
  services or the cloud-metadata endpoint. subwire keeps the deliberate internal
  access but **blocks link-local/metadata by default** and gives you allow/deny
  lists, a read-only mode, and per-target method allowlists.

No shell-out to `curl`; the request engine is `httpx`.

## Install

From source (recommended while it's young):

```bash
git clone https://github.com/Corundex/subwire && cd subwire
make install          # = pip install .
```

Or, once published to PyPI:

```bash
pip install subwire
```

Requires Python 3.11+ (for local install) or just Docker (for the server setup —
nothing to install on the host but Docker itself). Dependencies: `httpx`, `mcp`,
`pyyaml`. Run `make help` to see all the shortcuts.

## Quickstart

First, make your config (this copies the example; edit it to list your hosts):

```bash
make config        # creates config.yaml from config.example.yaml
# or: cp config.example.yaml config.yaml
```

Then pick how you want to run it:

### A. On the machine you use Claude Desktop from (simplest)

```bash
make install       # installs the `subwire` command
make run           # runs on stdio, using config.yaml
```

Then add it to Claude Desktop (see [below](#connecting-it-to-claude-desktop)).

### B. As a server on your homelab / a remote host (Docker)

Config is **baked into the image**, so there's nothing to mount and it behaves
the same locally or remotely:

```bash
make up            # builds the image (with your config.yaml inside) and starts it
# equivalently: docker compose up -d --build
```

The MCP endpoint is then `http://<host>:8080/mcp`. Point Claude Desktop at it via
`mcp-remote` (see [below](#connecting-it-to-claude-desktop)).

> No config at all? `subwire` still runs — it just has no named targets, and you
> pass full URLs directly. Default policy: reach LAN + public, block cloud
> metadata.

## Connecting it to Claude Desktop

**Local (stdio):**

```json
{
  "mcpServers": {
    "subwire": {
      "command": "subwire",
      "args": ["--config", "/path/to/config.yaml"]
    }
  }
}
```

**Remote (HTTP server on your LAN, via `mcp-remote`):**

```json
{
  "mcpServers": {
    "subwire": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://subwire.home.lan/mcp",
               "--transport", "http-only", "--allow-http"]
    }
  }
}
```

## Tools

### `http_request`
Make a request. Either pass an absolute `url`, or a `target` + relative `url`.

| arg | type | notes |
|-----|------|-------|
| `url` | string | absolute, or relative to the target's `base_url` |
| `method` | string | default `GET` |
| `headers` | object | merged over target defaults |
| `params` | object | query string |
| `json_body` | any | JSON body (sets Content-Type) |
| `body` | string | raw body (non-JSON) |
| `target` | string | named profile to use |
| `timeout` | number | seconds |
| `verify` | bool \| string | TLS override: `true`, `false`, or a CA-bundle path |
| `max_bytes` | int | response body cap |

Returns JSON: `{request, status, ok, elapsed_ms, headers, body, truncated}`.
JSON response bodies are parsed into structured data automatically.

### `list_targets`
Lists configured targets (base URL, allowed methods, TLS posture, auth type — no
secrets) and the active security policy. Good first call so the model knows what
it may reach.

## Configuration

YAML. Every setting resolves by precedence **explicit arg > target > default**.
See [`config.example.yaml`](./config.example.yaml) for an annotated copy.

```yaml
defaults:
  timeout: 30
  verify: /etc/subwire/home-ca.pem  # internal CA root: whole-LAN trust anchor
                                    # (use `true` for public-only, `false` to disable)
  allow_http: true             # permit plain http://
  read_only: false             # true => only GET/HEAD/OPTIONS
  max_response_bytes: 100000
  follow_redirects: true

security:
  allow_private: true          # RFC1918 + *.home.lan etc.
  allow_loopback: true
  allow_metadata: false        # block 169.254.169.254 (keep false)
  allow_hosts: []              # extra explicit allow globs
  deny_hosts: []               # explicit deny globs (win over all)

targets:
  prometheus:
    base_url: http://prometheus.home.lan:9090
    allowed_methods: [GET]     # monitoring: read-only
  llama:
    base_url: http://llama.home.lan
  dozzle:
    base_url: https://dozzle.home.lan   # CA-signed => inherits default, verifies
  legacy-appliance:
    base_url: https://nas.home.lan
    verify: false              # the exception: a cert you can't re-issue
  smartoffs:
    base_url: https://www.smartoffs.com
    auth: { type: bearer, token_env: SMARTOFFS_TOKEN }
```

### Auth

Credentials are **never** in the file — auth blocks reference environment variable
*names*, resolved at request time:

```yaml
auth: { type: basic,  username_env: SVC_USER, password_env: SVC_PASS }
auth: { type: bearer, token_env: SVC_TOKEN }
auth: { type: apikey, header: X-API-Key, value_env: SVC_KEY }
```

Need a scheme that isn't built in? `auth.py` is a small, self-contained module
with a clear shape — add a case and you're done.

### Internal CA / wildcard certs

The cleanest way to get verified TLS across a whole homelab is **one internal CA**
at `defaults.verify` — not a pile of per-host `verify: false`. Sign each service's
cert with your CA, point the global default at the CA root, and every
`*.home.lan` host validates with verification **on**. Override per-target only for
the odd box you can't re-issue a cert for.

```yaml
defaults:
  verify: /etc/subwire/home-ca.pem   # whole-LAN trust anchor
```

Prefer a single self-signed **wildcard** cert over a CA? That works too — a
self-signed cert is its own trust anchor, so point `verify` at the cert file.
Two caveats:

- **It must carry the name in `subjectAltName`, not just CN.** Modern OpenSSL
  (and thus httpx) ignores CN for hostname matching — a CN-only or no-SAN cert
  fails validation no matter what you trust. This is the usual reason a
  hand-rolled self-signed cert "doesn't work."
- **A wildcard matches exactly one label.** `*.home.lan` covers
  `dozzle.home.lan` but **not** the apex `home.lan` nor `a.b.home.lan`. Add extra
  SAN entries (or use the CA approach) for those.

Generating a wildcard self-signed cert with the right SANs:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout home-lan.key -out home-lan.pem \
  -subj "/CN=*.home.lan" \
  -addext "subjectAltName=DNS:*.home.lan,DNS:home.lan"
```

If `verify` points at a CA/cert path that doesn't exist, subwire fails the
request with a clear message naming the path (rather than an opaque SSL error) —
handy when a Docker volume mount is wrong.

> Note: subwire keeps `verify` at just two scopes — global default and
> per-target — on purpose. The global default *is* the wildcard; per-target
> handles exceptions. There's deliberately no "trust CA X for hosts matching
> pattern Y" map, since it adds config surface for no capability the two scopes
> don't already cover.

### Environment overrides

`SUBWIRE_CONFIG`, `SUBWIRE_HOST`, `SUBWIRE_PORT`, `SUBWIRE_READ_ONLY`,
`SUBWIRE_ALLOW_HTTP`, `SUBWIRE_VERIFY` (`true`/`false`/CA path),
`SUBWIRE_MAX_RESPONSE_BYTES`.

## Docker

There are two ways to run subwire in Docker, depending on who you are:

### Run a pinned release with your own data (recommended for users)

Use the [`deploy/`](./deploy/) template. It keeps **your** config and certs in a
folder *you* own, pulls a pinned subwire version from GitHub at build time, and
bakes them together — so updating subwire never touches your data, and there are
no volume mounts.

```bash
cp -r deploy ~/subwire-deploy     # copy the template out of the code repo
cd ~/subwire-deploy
nano config.yaml                  # add your targets
docker compose up -d --build      # pulls subwire + bakes your data
```

Updating later is a one-line version bump in `deploy/docker-compose.yaml`, then
rebuild — see [`deploy/README.md`](./deploy/README.md). This is the cleanest path
for a homelab, especially for shipping to a remote host (clone your deploy repo
on the server and `up`).

### Build from a local clone (contributors / hacking on the code)

If you've cloned this repo to work on subwire itself, the root `Dockerfile` builds
straight from the local source, baking in `config.yaml` and `certs/` from here:

```bash
make config                    # create config.yaml (once), then edit it
make up                        # build from local source, start detached
```

In both cases, secrets referenced by your targets (e.g. `token_env: SMARTOFFS_TOKEN`)
are passed as environment variables — never baked into the image. Using an
internal CA? Drop the `.pem` in the relevant `certs/` folder before building and
reference it as `/etc/subwire/certs/home-ca.pem`.

## Security model (read this)

subwire is designed to reach internal hosts on purpose, so it can't just block
private IPs. Instead, evaluated in order:

1. `deny_hosts` glob match → refused.
2. `http://` with `allow_http: false` → refused.
3. method not permitted (read-only mode and/or target allowlist) → refused.
4. address class: **link-local/cloud-metadata blocked by default**
   (`allow_metadata: false`); loopback/private allowed if enabled; public allowed.

TLS verification is per-target and never silently disabled globally. Secrets stay
in env vars and configured auth headers are redacted from echoed requests.

**Known v0.1 limitations:** no DNS-rebinding defense (host is classified by
literal/pattern, not re-resolved); allow/deny are globs, not regex.

## Troubleshooting

**subwire doesn't show up in Claude Desktop.** Tools only appear when the app
connects to the server at startup. Fully quit and reopen Claude Desktop (don't
just close the window), then check Settings → Connectors. For the remote/HTTP
setup, make sure the container is running (`make logs`) and the URL ends in
`/mcp`.

**"config file not found".** The path you passed with `--config` doesn't exist.
Run `make config` to create `config.yaml`, or run `subwire` with no `--config` to
start with no targets.

**A request fails with "TLS verify is set to CA bundle ... but that file does not
exist".** Your `verify:` path is wrong, or (in Docker) the cert wasn't baked in.
Put the file in [`certs/`](./certs/), reference it as
`/etc/subwire/certs/<file>`, and rebuild with `make up`.

**A request fails with a certificate error against an internal host.** The host's
cert almost certainly lacks a `subjectAltName` (CN alone is ignored). Re-issue the
cert with proper SANs — see [Internal CA / wildcard certs](#internal-ca--wildcard-certs).

**"blocked by security policy".** Working as intended — the request hit a gate.
The message says which one (deny-list, read-only mode, a target's method
allowlist, or the metadata block). Adjust the relevant setting in `config.yaml`
if it's a host you do want to reach.

**Plain `http://` is refused.** You (or an env override) set `allow_http: false`.
Set it back to `true` for homelab use.

## License

MIT — see [LICENSE](./LICENSE).
