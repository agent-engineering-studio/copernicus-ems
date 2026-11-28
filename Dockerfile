# copernicus-ems with every extra: the library, the command line, the MCP server
# and the API the demo asks (demo/api.py). The page of the demo is another
# image, built from demo/web.
#
#   docker compose up            the demo, on http://localhost:3009
#   docker build -t copernicus-ems:local .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

# rasterio (ml extra) brings its own GDAL, which needs libexpat from the system.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DATA_ROOT=/data \
    COPERNICUS_EMS_RUN=

WORKDIR /app

# The dependencies first: this layer is rebuilt only when they change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --all-extras --group demo --no-install-project

COPY README.md LICENSE ./
COPY src src
RUN uv sync --locked --no-dev --all-extras --group demo --no-editable

# Fail the build, not the first run, if a system library is missing.
RUN python -c "import rasterio, shapely, pyproj, mcp, starlette, uvicorn, copernicus_ems"

COPY demo demo
COPY scripts scripts

RUN useradd --create-home ems && mkdir -p /data/copernicus-ems && chown -R ems /data
USER ems
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"]

CMD ["uvicorn", "--app-dir", "demo", "api:app", "--host", "0.0.0.0", "--port", "8000"]
