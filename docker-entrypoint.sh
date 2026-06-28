#!/bin/sh
set -e

CONFIG="${SUBWIRE_CONFIG:-/etc/subwire/config.yaml}"

if [ ! -f "$CONFIG" ]; then
  echo "subwire: '$CONFIG' was not baked into this image." >&2
  echo "subwire: starting with the bundled example (no targets configured)." >&2
  echo "subwire: to use your own targets: copy config.example.yaml to config.yaml," >&2
  echo "subwire: edit it, then rebuild the image (docker compose up -d --build)." >&2
  CONFIG="/etc/subwire/config.example.yaml"
fi

exec subwire --http --config "$CONFIG"
