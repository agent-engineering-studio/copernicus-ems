.PHONY: help check lint format typecheck test bench build docs notebooks lab init data demo \
	radar llm describe docker up logs down

# The targets, with what each does (the lines marked with two hashes).
help: ## this list
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  make %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check: lint typecheck test ## everything CI checks: lint, types, tests with coverage

lint: ## ruff, and the format
	uv run ruff check
	uv run ruff format --check

format: ## format the code and fix what ruff can
	uv run ruff format
	uv run ruff check --fix

typecheck: ## mypy, strict
	uv run mypy

test: ## the tests, with coverage
	uv run pytest --cov --cov-report=term-missing

# No network, no account: the cases are committed (benchmarks/README.md).
bench: ## the known cases: 24 real maps, and what the checks of describe catch
	uv run python -m benchmarks.run --check
	uv run python -m benchmarks.faithful --check

build: ## the wheel and the sdist, in dist/
	uv build

# The site in ./site, with llms.txt, llms-full.txt and the agent skill.
docs: ## the documentation site, with llms.txt
	uv run --group docs zensical build --clean --strict
	uv run --group docs python tools/build_llms.py

# Run every notebook again and save the results. Needs a Copernicus Data Space
# account (CDSE_CLIENT_ID, CDSE_CLIENT_SECRET); the images already in the data
# directory are not asked again, so a second run spends no processing units.
notebooks: ## run the eight notebooks again (needs CDSE credentials)
	cd notebooks && uv run --extra cdse --group notebooks jupyter nbconvert --to notebook --execute --inplace 0*.ipynb

lab: ## the notebooks in JupyterLab, on http://localhost:8887
	uv run --extra cdse --group notebooks --with jupyterlab jupyter lab --port "$${COPERNICUS_EMS_NOTEBOOKS_PORT:-8887}" --notebook-dir notebooks

# Install everything and download the floods of Italy (needs uv; Node for the demo).
init: ## install everything and download the floods of Italy
	sh scripts/init.sh

# Download the list, the details and the layers again, into $$DATA_ROOT (default ./data).
data: ## download the list, the details and the layers again
	sh scripts/fetch-data.sh

# The demo, on http://localhost:3009 (needs uv and Node).
demo: ## the demo, on http://localhost:3009 (uv and Node)
	sh scripts/demo.sh

# Radar check of the maps of one activation (needs CODE, CDSE credentials and PU):
# fetches the 40 m Sentinel-1 pairs, then writes the quality and agreement files the demo reads,
# in $COPERNICUS_EMS_DATA_DIR if set, else $DATA_ROOT/copernicus-ems (as the library does).
#   make radar CODE=EMSR664
radar: ## the radar check of one activation: make radar CODE=EMSR664
	@test -n "$(CODE)" || { echo "usage: make radar CODE=EMSR664 (needs CDSE credentials and PU)"; exit 2; }
	uv run copernicus-ems --dir "$${COPERNICUS_EMS_DATA_DIR:-$${DATA_ROOT:-data}/copernicus-ems}" quality $(CODE) --fetch
	uv run copernicus-ems --dir "$${COPERNICUS_EMS_DATA_DIR:-$${DATA_ROOT:-data}/copernicus-ems}" radar $(CODE)

# A local language model for `describe` (llama.cpp in Docker; the first start downloads ~2 GB).
llm: ## a local language model for describe (Docker)
	docker compose --profile ai up -d llm
	@echo "llm: http://127.0.0.1:$${COPERNICUS_EMS_LLM_PORT:-8780}/v1 (set COPERNICUS_EMS_LLM_URL to it)"

# Describe one activation (CODE=EMSR762) or, without CODE, all of them: with Claude
# (ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN set), else the local model of `make llm`
# (COPERNICUS_EMS_LLM_URL, default http://127.0.0.1:$COPERNICUS_EMS_LLM_PORT/v1, port 8780).
# Writes <data>/<CODE>/description.json, in the same folder as `make radar`.
#   make describe CODE=EMSR762
describe: ## describe one activation (CODE=...) or all of them
	COPERNICUS_EMS_LLM_URL="$${COPERNICUS_EMS_LLM_URL:-http://127.0.0.1:$${COPERNICUS_EMS_LLM_PORT:-8780}/v1}" \
	uv run copernicus-ems --dir "$${COPERNICUS_EMS_DATA_DIR:-$${DATA_ROOT:-data}/copernicus-ems}" describe $(if $(CODE),$(CODE),--all)

# The same with Docker alone: build the images, run the demo in the background.
docker: ## the demo with Docker alone
	mkdir -p "$${DATA_ROOT:-data}/copernicus-ems"
	docker compose up -d --build api web
	@echo "demo: http://localhost:$${COPERNICUS_EMS_PORT:-3009}"

# The demo and the MCP server for AI agents, in the background.
up: ## the demo and the MCP server, in the background
	mkdir -p "$${DATA_ROOT:-data}/copernicus-ems"
	docker compose --profile mcp up -d --build
	@echo "demo: http://localhost:$${COPERNICUS_EMS_PORT:-3009}"
	@echo "MCP:  http://127.0.0.1:$${COPERNICUS_EMS_MCP_DOCKER_PORT:-8769}/mcp"

# Follow the logs of the containers.
logs: ## follow the logs of the containers
	docker compose --profile mcp logs -f --tail=50

# Stop the demo and the MCP server, remove their containers (the data downloaded stays).
down: ## stop the containers (the data stays)
	docker compose --profile mcp --profile tools down --remove-orphans
