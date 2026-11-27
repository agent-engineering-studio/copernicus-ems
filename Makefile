.PHONY: check lint format typecheck test build init data demo radar docker up logs down

check: lint typecheck test

lint:
	uv run ruff check
	uv run ruff format --check

format:
	uv run ruff format
	uv run ruff check --fix

typecheck:
	uv run mypy

test:
	uv run pytest --cov --cov-report=term-missing

build:
	uv build

# Install everything and download the floods of Italy (needs uv; Node for the demo).
init:
	sh scripts/init.sh

# Download the list, the details and the layers again, into $$DATA_ROOT (default ./data).
data:
	sh scripts/fetch-data.sh

# The demo, on http://localhost:3000 (needs uv and Node).
demo:
	sh scripts/demo.sh

# Radar check of the maps of one activation (needs CODE, CDSE credentials and PU):
# fetches the 40 m Sentinel-1 pairs, then writes the quality and agreement files the demo reads,
# in $COPERNICUS_EMS_DATA_DIR if set, else $DATA_ROOT/copernicus-ems (as the library does).
#   make radar CODE=EMSR664
radar:
	@test -n "$(CODE)" || { echo "usage: make radar CODE=EMSR664 (needs CDSE credentials and PU)"; exit 2; }
	uv run copernicus-ems --dir "$${COPERNICUS_EMS_DATA_DIR:-$${DATA_ROOT:-data}/copernicus-ems}" quality $(CODE) --fetch
	uv run copernicus-ems --dir "$${COPERNICUS_EMS_DATA_DIR:-$${DATA_ROOT:-data}/copernicus-ems}" radar $(CODE)

# The same with Docker alone: build the images, run the demo in the background.
docker:
	mkdir -p "$${DATA_ROOT:-data}/copernicus-ems"
	docker compose up -d --build api web
	@echo "demo: http://localhost:$${COPERNICUS_EMS_PORT:-3000}"

# The demo and the MCP server for AI agents, in the background.
up:
	mkdir -p "$${DATA_ROOT:-data}/copernicus-ems"
	docker compose --profile mcp up -d --build
	@echo "demo: http://localhost:$${COPERNICUS_EMS_PORT:-3000}"
	@echo "MCP:  http://127.0.0.1:$${COPERNICUS_EMS_MCP_DOCKER_PORT:-8770}/mcp"

# Follow the logs of the containers.
logs:
	docker compose --profile mcp logs -f --tail=50

# Stop the demo and the MCP server, remove their containers (the data downloaded stays).
down:
	docker compose --profile mcp --profile tools down --remove-orphans
