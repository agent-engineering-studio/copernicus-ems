#!/bin/sh
# Set up copernicus-ems on this machine, without Docker: install it with every
# extra, install the page of the demo, download the floods of Italy, say what
# to do next.
#
#   scripts/init.sh
#
# It needs uv (https://docs.astral.sh/uv/), which also installs Python, and
# Node 20 or later for the page of the demo.
set -eu
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. Install it with" >&2
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    echo "or use Docker instead: docker compose up" >&2
    exit 1
fi

echo "Installing copernicus-ems with every extra, and what the demo needs"
uv sync --all-extras --group demo

if command -v npm >/dev/null 2>&1; then
    echo "Installing the page of the demo"
    (cd demo/web && npm install --no-audit --no-fund)
else
    echo "warning: Node is not installed: everything works but 'make demo'" >&2
fi

sh scripts/fetch-data.sh

cat <<'NEXT'

Ready. Now:

    make demo                              the demo, on http://localhost:3009
    uv run copernicus-ems --help           the command line
    uv run python                          then: import copernicus_ems

NEXT
