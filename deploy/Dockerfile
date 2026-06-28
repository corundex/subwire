# subwire deployment image.
#
# Upstream subwire code is PULLED from GitHub at a pinned version; YOUR data
# (config.yaml + certs/) is overlaid on top from this folder. The two are fully
# decoupled — updating subwire is a one-line version bump here, and never touches
# your config. Your config lives in this folder (your deploy repo), not in the
# subwire code repo.

ARG SUBWIRE_VERSION=main
#   ^ For reproducible deploys, pin this to a release tag, e.g. v0.1.0.
#     `main` always builds the latest; a tag freezes the version until you bump it.

FROM python:3.12-slim
ARG SUBWIRE_VERSION

# Pull the pinned subwire release straight from GitHub (git needed only at build).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir "git+https://github.com/Corundex/subwire@${SUBWIRE_VERSION}" \
    && apt-get purge -y git && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Overlay YOUR data — kept entirely outside the upstream code repo.
COPY config.yaml /etc/subwire/config.yaml
COPY certs/ /etc/subwire/certs/

ENV SUBWIRE_CONFIG=/etc/subwire/config.yaml \
    SUBWIRE_HOST=0.0.0.0 \
    SUBWIRE_PORT=8080
EXPOSE 8080
ENTRYPOINT ["subwire", "--http", "--config", "/etc/subwire/config.yaml"]

# ---------------------------------------------------------------------------
# Option B (once subwire publishes prebuilt images to a registry):
# replace the FROM + RUN block above with a single line, and builds become
# near-instant (no compile, just pull + overlay your data):
#
#   ARG SUBWIRE_VERSION=v0.1.0
#   FROM ghcr.io/corundex/subwire:${SUBWIRE_VERSION}
#   COPY config.yaml /etc/subwire/config.yaml
#   COPY certs/ /etc/subwire/certs/
#
# (keep the ENV/EXPOSE/ENTRYPOINT lines as-is, or drop them if the base image
#  already sets them).
# ---------------------------------------------------------------------------
