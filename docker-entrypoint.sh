#!/bin/sh
# subwire container entrypoint.
#
# The CLI auto-seeds a missing config file from the bundled example, so this
# script is now a thin shim: resolve the config path and exec subwire. No
# bespoke fallback logic needed — `subwire` itself will print a clear message
# when it seeds, and the bundled example ships with public demo targets so the
# container is usable from the first request.
set -e
exec subwire --http --config "${SUBWIRE_CONFIG:-/etc/subwire/config.yaml}"
