FROM python:3.12-slim

WORKDIR /app

# Install the package (dependencies + the `subwire` command)
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Bake configuration INTO the image, so the container runs identically on your
# laptop and on a remote host with no volume mounts to manage.
#   - config*.yaml  : always copies config.example.yaml; copies your config.yaml too if present
#   - certs/        : your internal CA / TLS files, referenced as /etc/subwire/certs/<file>
COPY config*.yaml /etc/subwire/
COPY certs/ /etc/subwire/certs/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV SUBWIRE_CONFIG=/etc/subwire/config.yaml \
    SUBWIRE_HOST=0.0.0.0 \
    SUBWIRE_PORT=8080

EXPOSE 8080

# HTTP transport by default in a container. The entrypoint uses your baked-in
# config.yaml, or falls back to the example (with a message) if you didn't make one.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
