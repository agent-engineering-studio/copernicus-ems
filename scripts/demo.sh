#!/bin/sh
# Run the demo without Docker: the API that asks the library (demo/api.py) and
# the page that draws the map (demo/web), until Ctrl-C.
#
#   scripts/demo.sh
#
#   COPERNICUS_EMS_PORT       the port of the page (default 3009)
#   COPERNICUS_EMS_API_PORT   the port of the API (default 8000)
#   DATA_ROOT                 where the data is (default ./data, as scripts/fetch-data.sh)
#
# It needs uv and Node 20 or later.
set -eu
cd "$(dirname "$0")/.."

PORT="${COPERNICUS_EMS_PORT:-3009}"
API_PORT="${COPERNICUS_EMS_API_PORT:-8000}"
export DATA_ROOT="${DATA_ROOT:-$PWD/data}"

if ! command -v npm >/dev/null 2>&1; then
    echo "Node is not installed (https://nodejs.org/). Or use Docker: docker compose up" >&2
    exit 1
fi
[ -d demo/web/node_modules ] || (cd demo/web && npm install --no-audit --no-fund)

uv run --all-extras --group demo uvicorn --app-dir demo api:app --port "$API_PORT" &
API=$!
trap 'kill "$API" 2>/dev/null' EXIT INT TERM

echo "The demo: http://localhost:$PORT (data in $DATA_ROOT/copernicus-ems)"
cd demo/web
COPERNICUS_EMS_API="http://127.0.0.1:$API_PORT" npm run dev -- --port "$PORT"
