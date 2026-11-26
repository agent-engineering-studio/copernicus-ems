.PHONY: check lint format typecheck test build init data demo docker down

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

# The same with Docker alone: build the images, run the demo.
docker:
	mkdir -p "$${DATA_ROOT:-data}/copernicus-ems"
	docker compose up --build

# Stop the demo and remove its containers (the data downloaded stays).
down:
	docker compose --profile tools down --remove-orphans
