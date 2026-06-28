# subwire — deployment

This folder is **your deployment**, kept separate from the
[subwire code repo](https://github.com/Corundex/subwire). Your `config.yaml`,
your `certs/`, and the subwire version you run all live here. The upstream code
is pulled in when the image builds — it never sits in this folder — so updating
subwire is a one-line change and never touches your data.

Copy this folder out of the code repo to somewhere you own (and, recommended,
`git init` it as your private deploy repo — see [Remote hosts](#remote-hosts)).

## Setup

```bash
# 1. Edit your config — add your homelab targets:
nano config.yaml

# 2. (optional) Using an internal CA? Drop the root cert here and point
#    `verify:` at /etc/subwire/certs/<file> in config.yaml:
cp ~/home-ca.pem certs/

# 3. (optional) If any target needs a secret token/key:
cp .env.example .env && nano .env

# 4. Build (pulls pinned subwire + bakes your data) and start:
docker compose up -d --build
```

The MCP endpoint is then `http://<host>:8081/mcp` — point Claude Desktop at it
via `mcp-remote`.

## Updating subwire

Your data is untouched; you just change which version gets pulled.

```bash
# Edit SUBWIRE_VERSION in docker-compose.yaml (e.g. main -> v0.2.0), then:
docker compose up -d --build
```

Pin to a release **tag** (e.g. `v0.1.0`) for reproducible deploys; use `main`
for the latest. Because this deploy repo never tracks the upstream code, there's
no `git pull` to reconcile and nothing of yours to overwrite.

## Remote hosts

Two clean options:

- **Deploy repo (recommended).** `git init` this folder, push it to your own
  (private) repo. On the server: `git clone <your-deploy-repo> && cd it &&
  docker compose up -d --build`. Your config, CA cert, and version pin all travel
  with the repo. To update: bump the version, commit, `git pull` on the server,
  rebuild.
- **Just a folder.** `scp -r` this directory to the host and run
  `docker compose up -d --build` there. No git involved.

Either way there are no volume mounts to get right — everything is baked from
this folder at build time.

## Why this layout

- **Your data and the code are decoupled.** Pulling a new subwire version can't
  clobber your config, because your config was never in the code repo.
- **Reproducible.** The version pin lives next to your data, so a rebuild on any
  host produces the same thing.
- **Portable.** One folder (or one repo) is the whole deployment.

For the fully annotated config reference, see `config.example.yaml` in the
subwire code repo.
