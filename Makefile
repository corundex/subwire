# subwire — common tasks. Run `make help` to see them.

.PHONY: help config install run http build up down logs clean

help:
	@echo "subwire — common tasks:"
	@echo "  make config    create config.yaml from the example (won't overwrite)"
	@echo "  make install   install locally so the 'subwire' command works"
	@echo "  make run       run on stdio (use this for Claude Desktop on this machine)"
	@echo "  make http      run as an HTTP server on port 8080"
	@echo "  make up        build + start in Docker (config baked in), detached"
	@echo "  make logs      follow the Docker logs"
	@echo "  make down      stop the Docker container"
	@echo "  make clean     remove build artifacts"

config:
	@cp -n config.example.yaml config.yaml && echo "Created config.yaml — edit it, then 'make run' or 'make up'." \
		|| echo "config.yaml already exists; leaving it alone."

install:
	pip install .

run: ensure-config
	subwire --config config.yaml

http: ensure-config
	subwire --http --host 0.0.0.0 --port 8080 --config config.yaml

up:
	docker compose up -d --build
	@echo "subwire is running. MCP endpoint: http://localhost:8080/mcp"

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	rm -rf build dist *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# internal: make sure a config exists before running locally
.PHONY: ensure-config
ensure-config:
	@test -f config.yaml || { echo "No config.yaml yet — run 'make config' first (or it'll run with no targets)."; }
