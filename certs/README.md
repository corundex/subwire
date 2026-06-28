# certs/

Drop TLS material here that should be baked into the Docker image — typically
your internal CA root, e.g. `home-ca.pem`.

In your `config.yaml`, reference it by its in-container path:

    defaults:
      verify: /etc/subwire/certs/home-ca.pem

Anything you put here is copied into the image at build time, so the container
runs the same locally and on a remote host with no volume mounts.

Real certs are git-ignored (`*.pem`) so they won't be committed — only this
folder and these notes are tracked.
