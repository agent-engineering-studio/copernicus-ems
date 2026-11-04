"""Sentinel-1 backscatter from the Copernicus Data Space, on the grid of the labels.

Needs the ``cdse`` extra (``pip install 'copernicus-ems[cdse]'``) and a free
account on https://dataspace.copernicus.eu with an OAuth client
(Dashboard → User Settings → OAuth clients → Create). The Sentinel Hub
Process API then returns analysis-ready backscatter (calibrated,
orthorectified, optionally speckle-filtered) cut to **exactly** the
:class:`~copernicus_ems.ml.Grid` of a label raster, so image and labels line
up pixel for pixel: no 1 GB GRD archive, no SNAP.

Files are written to the data directory (``COPERNICUS_EMS_DATA_DIR``, a
Docker volume in a container), next to the layers, and read from there on
the next call; nothing is stored in a bucket.

Credentials come from the environment, never from code:

* ``CDSE_CLIENT_ID`` and ``CDSE_CLIENT_SECRET``, or
* ``CDSE_CREDENTIALS_FILE``: a JSON file ``{"client_id": ..., "client_secret": ...}``
  (e.g. a Docker secret in ``/run/secrets``).

Every request costs processing units (PU) of the account's monthly quota
(10,000 for a general user): :func:`estimate_pu` says how many before
anything is sent, and ``budget_pu`` refuses requests above it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

import httpx

from copernicus_ems.client import ProductEvents, RapidMapping, RetryTransport, default_directory
from copernicus_ems.errors import EmsError
from copernicus_ems.scenes import SENTINEL_1_GRD, ScenePass, passes

try:
    import numpy as np
    import numpy.typing as npt

    from copernicus_ems.ml import Grid
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.sentinelhub needs the cdse extra: pip install 'copernicus-ems[cdse]'"
    ) from exc

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"

ENV_CLIENT_ID = "CDSE_CLIENT_ID"
ENV_CLIENT_SECRET = "CDSE_CLIENT_SECRET"
ENV_CREDENTIALS_FILE = "CDSE_CREDENTIALS_FILE"

#: Largest side, in pixels, asked in one request; bigger grids are tiled.
MAX_PIXELS = 2500
#: Renew the token this many seconds before it expires.
RENEW_MARGIN_S = 60.0

BackCoeff = Literal["BETA0", "SIGMA0_ELLIPSOID", "GAMMA0_ELLIPSOID", "GAMMA0_TERRAIN"]

SENTINEL_CREDIT = "Contains modified Copernicus Sentinel data"


@dataclass(frozen=True, slots=True)
class Credentials:
    """An OAuth client of the Copernicus Data Space. The secret never shows in ``repr``."""

    client_id: str
    client_secret: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Credentials:
        """``CDSE_CLIENT_ID`` + ``CDSE_CLIENT_SECRET``, or the file in ``CDSE_CREDENTIALS_FILE``."""
        env = os.environ if environ is None else environ
        client_id = env.get(ENV_CLIENT_ID, "").strip()
        secret = env.get(ENV_CLIENT_SECRET, "").strip()
        if client_id and secret:
            return cls(client_id, secret)
        if path := env.get(ENV_CREDENTIALS_FILE, "").strip():
            try:
                data = json.loads(Path(path).expanduser().read_text())
                return cls(str(data["client_id"]).strip(), str(data["client_secret"]).strip())
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise EmsError(f"cannot read CDSE credentials from {path}: {exc}") from exc
        raise EmsError(
            f"no CDSE credentials: set {ENV_CLIENT_ID} and {ENV_CLIENT_SECRET}, or "
            f"{ENV_CREDENTIALS_FILE} to a JSON file with client_id and client_secret "
            "(dataspace.copernicus.eu → Dashboard → User Settings → OAuth clients)"
        )


@dataclass(frozen=True, slots=True)
class Token:
    access_token: str = field(repr=False)
    expires_at: float


def parse_token(status_code: int, body: bytes | str, *, now: float) -> Token:
    """The token from the identity server's answer; :class:`EmsError` if refused."""
    try:
        data: Any = json.loads(body)
    except ValueError:
        data = None
    if status_code != 200 or not isinstance(data, dict) or "access_token" not in data:
        detail = (
            data.get("error_description") or data.get("error")
            if isinstance(data, dict)
            else str(body)[:200]
        )
        raise EmsError(f"CDSE token refused (HTTP {status_code}): {detail}")
    return Token(str(data["access_token"]), now + float(data.get("expires_in", 300)))


def estimate_pu(
    width: int,
    height: int,
    *,
    bands: int = 2,
    float32: bool = True,
    samples: int = 1,
    orthorectify: bool = True,
    terrain_correction: bool = False,
    speckle_filter: bool = False,
) -> float:
    """Processing units of one Process API request, by the CDSE formula.

    1 PU is 512 x 512 pixels, 3 bands, 16 bits; factors multiply: area,
    bands / 3 (``dataMask`` not counted), 2 for FLOAT32, samples, and for
    Sentinel-1 2 for orthorectification, 2.5 for terrain correction
    (``GAMMA0_TERRAIN``), 2 for speckle filtering. At least 0.005.
    """
    pu = max(width * height / (512 * 512), 0.01) * (bands / 3) * samples
    pu *= 2 if float32 else 1
    pu *= 2 if orthorectify else 1
    pu *= 2.5 if terrain_correction else 1
    pu *= 2 if speckle_filter else 1
    return max(pu, 0.005)


def tiles(grid: Grid, max_pixels: int = MAX_PIXELS) -> list[tuple[int, int, Grid]]:
    """``(row, col, window)`` covering ``grid`` with windows of at most ``max_pixels`` a side."""
    if max_pixels < 1:
        raise ValueError("max_pixels must be positive")
    out = []
    for row in range(0, grid.height, max_pixels):
        for col in range(0, grid.width, max_pixels):
            height = min(max_pixels, grid.height - row)
            width = min(max_pixels, grid.width - col)
            out.append((row, col, grid.window(row, col, height, width)))
    return out


def evalscript(polarizations: Sequence[str] = ("VV", "VH")) -> str:
    """Linear backscatter of each polarization, then ``dataMask``, as FLOAT32 bands."""
    bands = [*polarizations, "dataMask"]
    values = ", ".join(f"s.{b}" for b in bands)
    return (
        "//VERSION=3\n"
        "function setup() {\n"
        f"  return {{input: {json.dumps(bands)}, "
        f"output: {{id: 'default', bands: {len(bands)}, sampleType: 'FLOAT32'}}}};\n"
        "}\n"
        f"function evaluatePixel(s) {{ return [{values}]; }}\n"
    )


def backscatter_request(
    grid: Grid,
    start: datetime,
    end: datetime,
    *,
    polarizations: Sequence[str] = ("VV", "VH"),
    back_coeff: BackCoeff = "GAMMA0_ELLIPSOID",
    orthorectify: bool = True,
    speckle_filter: bool = False,
    orbit_direction: str | None = None,
) -> dict[str, Any]:
    """The Process API request for Sentinel-1 GRD backscatter on exactly ``grid``.

    The bounding box is the grid's extent and the size its pixels, so each
    output pixel is one grid pixel.
    """
    if end <= start:
        raise ValueError("end must be after start")
    if back_coeff == "GAMMA0_TERRAIN" and not orthorectify:
        raise ValueError("GAMMA0_TERRAIN needs orthorectify")
    epsg = grid.crs.upper().removeprefix("EPSG:")
    if not epsg.isdigit():
        raise ValueError(f"the grid CRS must be an EPSG code, got {grid.crs!r}")
    x1 = grid.x0 + grid.width * grid.resolution_m
    y1 = grid.y0 - grid.height * grid.resolution_m
    data_filter: dict[str, Any] = {
        "timeRange": {"from": _iso(start), "to": _iso(end)},
        "acquisitionMode": "IW",
        "polarization": _polarization(polarizations),
    }
    if orbit_direction:
        data_filter["orbitDirection"] = orbit_direction.upper()
    processing: dict[str, Any] = {
        "backCoeff": back_coeff,
        "orthorectify": "true" if orthorectify else "false",
        "demInstance": "COPERNICUS_30",
    }
    if speckle_filter:
        processing["speckleFilter"] = {"type": "LEE", "windowSizeX": 5, "windowSizeY": 5}
    return {
        "input": {
            "bounds": {
                "bbox": [grid.x0, y1, x1, grid.y0],
                "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"},
            },
            "data": [
                {"type": "sentinel-1-grd", "dataFilter": data_filter, "processing": processing}
            ],
        },
        "output": {
            "width": grid.width,
            "height": grid.height,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
        "evalscript": evalscript(polarizations),
    }


class SentinelHub:
    """An authenticated Process API client; reuses its token until it expires.

    Give it no ``client`` and it opens one that retries (see
    :class:`~copernicus_ems.RetryTransport`); use it as an async context manager.
    """

    def __init__(
        self,
        credentials: Credentials | None = None,
        client: httpx.AsyncClient | None = None,
        *,
        token_url: str = TOKEN_URL,
        process_url: str = PROCESS_URL,
        clock: Any = time.time,
    ) -> None:
        self._credentials = credentials
        self._own = client is None
        self._client = client or httpx.AsyncClient(timeout=300, transport=RetryTransport(retries=3))
        self._token_url = token_url
        self._process_url = process_url
        self._clock = clock
        self._token: Token | None = None
        self._lock = asyncio.Lock()
        #: Processing units estimated for the requests sent by this client.
        self.pu_estimated = 0.0

    async def __aenter__(self) -> SentinelHub:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._own:
            await self._client.aclose()

    async def token(self) -> Token:
        async with self._lock:
            now = self._clock()
            if self._token is None or now > self._token.expires_at - RENEW_MARGIN_S:
                credentials = self._credentials or Credentials.from_env()
                response = await self._client.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": credentials.client_id,
                        "client_secret": credentials.client_secret,
                    },
                )
                self._token = parse_token(response.status_code, response.content, now=now)
            return self._token

    async def process(self, request: dict[str, Any], *, pu: float = 0.0) -> bytes:
        """POST one Process API request; the GeoTIFF bytes."""
        token = await self.token()
        response = await self._client.post(
            self._process_url,
            json=request,
            headers={"Authorization": f"Bearer {token.access_token}", "Accept": "image/tiff"},
        )
        if response.status_code != 200:
            raise EmsError(f"Process API answered {response.status_code}: {_error(response)}")
        self.pu_estimated += pu
        return response.content


@dataclass(frozen=True, slots=True)
class Backscatter:
    """A Sentinel-1 backscatter GeoTIFF on a label grid, and how it was made."""

    path: Path
    grid: Grid
    start: datetime
    end: datetime
    pu: float
    cached: bool


async def fetch_backscatter(
    hub: SentinelHub,
    grid: Grid,
    start: datetime,
    end: datetime,
    *,
    path: Path,
    polarizations: Sequence[str] = ("VV", "VH"),
    back_coeff: BackCoeff = "GAMMA0_ELLIPSOID",
    orthorectify: bool = True,
    speckle_filter: bool = False,
    orbit_direction: str | None = None,
    budget_pu: float | None = None,
    max_pixels: int = MAX_PIXELS,
    tags: Mapping[str, Any] | None = None,
) -> Backscatter:
    """Sentinel-1 backscatter on ``grid`` between ``start`` and ``end``, as one GeoTIFF.

    Bands: each polarization (linear power), then ``dataMask`` (1 data, 0 no
    data). Read from ``path`` if it is already there; otherwise the grid is
    asked in tiles of at most ``max_pixels`` a side and mosaicked. Raises
    :class:`EmsError` before any request if the estimate exceeds ``budget_pu``.
    """
    if path.is_file():
        return Backscatter(path, grid, start, end, 0.0, cached=True)
    windows = tiles(grid, max_pixels)
    pu = sum(
        estimate_pu(
            w.width,
            w.height,
            bands=len(polarizations),
            orthorectify=orthorectify,
            terrain_correction=back_coeff == "GAMMA0_TERRAIN",
            speckle_filter=speckle_filter,
        )
        for _, _, w in windows
    )
    if budget_pu is not None and pu > budget_pu:
        raise EmsError(
            f"{pu:.1f} PU estimated for {grid.width}x{grid.height} px, over the budget of "
            f"{budget_pu:.1f}: lower the resolution, or raise budget_pu"
        )
    bands = len(polarizations) + 1
    mosaic = np.zeros((bands, grid.height, grid.width), dtype=np.float32)
    for row, col, window in windows:
        request = backscatter_request(
            window,
            start,
            end,
            polarizations=polarizations,
            back_coeff=back_coeff,
            orthorectify=orthorectify,
            speckle_filter=speckle_filter,
            orbit_direction=orbit_direction,
        )
        share = estimate_pu(
            window.width,
            window.height,
            bands=len(polarizations),
            orthorectify=orthorectify,
            terrain_correction=back_coeff == "GAMMA0_TERRAIN",
            speckle_filter=speckle_filter,
        )
        body = await hub.process(request, pu=share)
        mosaic[:, row : row + window.height, col : col + window.width] = _read_tile(
            body, window, bands
        )
    meta = {
        **(tags or {}),
        "start": _iso(start),
        "end": _iso(end),
        "back_coeff": back_coeff,
        "orthorectify": orthorectify,
        "speckle_filter": speckle_filter,
        "units": "linear power",
        "source": f"{SENTINEL_CREDIT} {start.year}, processed by Sentinel Hub (CDSE)",
    }
    _write(path, mosaic, grid, [*polarizations, "dataMask"], meta)
    return Backscatter(path, grid, start, end, pu, cached=False)


def backscatter_path(
    grid: Grid,
    start: datetime,
    end: datetime,
    *,
    activation: str,
    directory: Path | None = None,
    **options: Any,
) -> Path:
    """``<data dir>/<activation>/sentinel-1/S1_<time>_<res>m_<key>.tif``.

    The name depends on the grid, the time range and the options, not on
    the product: two products of one AOI seen by the same pass share it.
    """
    key = json.dumps(
        {
            "grid": [grid.crs, grid.x0, grid.y0, grid.resolution_m, grid.width, grid.height],
            "time": [_iso(start), _iso(end)],
            **options,
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(key.encode()).hexdigest()[:10]
    stamp = start.astimezone(UTC).strftime("%Y%m%dT%H%M")
    name = f"S1_{stamp}_{_res(grid)}m_{digest}.tif"
    return (directory or default_directory()) / activation / "sentinel-1" / name


def _res(grid: Grid) -> str:
    return f"{grid.resolution_m:g}"


def _grid_key(grid: Grid) -> str:
    raw = json.dumps([grid.crs, grid.x0, grid.y0, grid.resolution_m, grid.width, grid.height])
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def pass_window(time: datetime, minutes: float = 10.0) -> tuple[datetime, datetime]:
    """A time range around one satellite pass, narrow enough to exclude the next one."""
    margin = timedelta(minutes=minutes)
    return time - margin, time + margin


def _read_tile(body: bytes, window: Grid, bands: int) -> npt.NDArray[np.float32]:
    import rasterio
    from rasterio.io import MemoryFile

    try:
        with MemoryFile(body) as memory, memory.open() as src:
            data: npt.NDArray[np.float32] = src.read().astype(np.float32)
            got = Grid.from_transform(src.crs, src.transform, src.width, src.height)
    except rasterio.errors.RasterioIOError as exc:
        raise EmsError(f"Process API did not return a GeoTIFF: {body[:200]!r}") from exc
    if data.shape != (bands, window.height, window.width):
        raise EmsError(
            f"unexpected tile shape {data.shape}, asked {(bands, window.height, window.width)}"
        )
    if not _aligned(got, window):
        raise EmsError(f"the tile is not on the asked grid: got {got}, asked {window}")
    return data


def _aligned(got: Grid, want: Grid) -> bool:
    tolerance = want.resolution_m * 1e-3
    return (
        math.isclose(got.resolution_m, want.resolution_m, rel_tol=1e-6)
        and abs(got.x0 - want.x0) < tolerance
        and abs(got.y0 - want.y0) < tolerance
        and _epsg(got.crs) == _epsg(want.crs)
    )


def _epsg(crs: str) -> str:
    return crs.upper().removeprefix("EPSG:")


def _write(
    path: Path,
    mosaic: npt.NDArray[np.float32],
    grid: Grid,
    names: Sequence[str],
    meta: Mapping[str, Any],
) -> None:
    import rasterio
    from rasterio.transform import Affine

    bands, height, width = mosaic.shape
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": bands,
        "dtype": "float32",
        "crs": grid.crs,
        "transform": Affine(*grid.transform),
        "compress": "deflate",
        "predictor": 3,
        **(
            {"tiled": True, "blockxsize": 256, "blockysize": 256}
            if height >= 256 and width >= 256
            else {}
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with rasterio.open(partial, "w", **profile) as dst:
        dst.write(mosaic)
        dst.update_tags(**{k: str(v) for k, v in meta.items() if v is not None})
        for index, name in enumerate(names, start=1):
            dst.set_band_description(index, name)
    partial.replace(path)


def _polarization(polarizations: Sequence[str]) -> str:
    wanted = {p.upper() for p in polarizations}
    if wanted == {"VV", "VH"}:
        return "DV"
    if wanted == {"HH", "HV"}:
        return "DH"
    if len(wanted) == 1 and wanted <= {"VV", "VH", "HH", "HV"}:
        return wanted.pop()
    raise ValueError(f"unsupported polarizations: {sorted(wanted)}")


def _error(response: httpx.Response) -> str:
    try:
        data: Any = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("reason") or error)[:300]
        return str(data.get("message") or data)[:300]
    return str(data)[:300]


def _iso(when: datetime) -> str:
    aware = when if when.tzinfo else when.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class PairPlan:
    """What :func:`make_pair` would do for one product, and what it would cost."""

    event: ProductEvents
    grid: Grid
    scene_pass: ScenePass | None
    coverage: float
    pu: float
    backscatter_path: Path | None
    labels_path: Path | None
    #: Why no pass was chosen, when ``scene_pass`` is ``None``.
    reason: str | None = None

    @property
    def product(self) -> str:
        return self.event.product.label


async def plan_pair(
    ems: RapidMapping,
    event: ProductEvents,
    *,
    resolution_m: float = 20.0,
    within: timedelta = timedelta(days=2),
    min_coverage: float = 0.9,
    max_gap: timedelta = timedelta(hours=24),
    directory: Path | None = None,
) -> PairPlan:
    """Choose the Sentinel-1 pass nearest in time among those covering ``min_coverage``
    of the AOI within ``max_gap``, and price it on the labels' grid. No PU is spent.

    Every feature of ``event`` becomes the *flooded* label: pass events
    selected for floods (the default of ``observed_events``), not
    ``event_types=None``, which would label landslides as water.
    """
    from copernicus_ems.geo import coverage
    from copernicus_ems.ml import grid_for

    grid = grid_for(event, resolution_m)
    found = passes(await ems.scenes(event.product, collections=[SENTINEL_1_GRD], within=within))
    best: tuple[ScenePass, float] | None = None
    seen = 0.0
    covering_gap: timedelta | None = None
    for candidate in found:  # nearest first
        share = coverage(candidate.geometry, event)
        seen = max(seen, share)
        if share >= min_coverage and covering_gap is None:
            covering_gap = candidate.gap
        if abs(candidate.gap) <= max_gap and share >= min_coverage:
            best = (candidate, share)
            break
    if best is None:
        hours = max_gap.total_seconds() / 3600
        if not found:
            reason = f"no Sentinel-1 pass within ±{within.total_seconds() / 86400:g} days"
        elif covering_gap is None:
            reason = (
                f"no pass covers {min_coverage:.0%} of the AOI "
                f"({len(found)} passes, best {seen:.0%})"
            )
        else:
            reason = (
                f"the nearest pass covering {min_coverage:.0%} of the AOI is "
                f"{covering_gap.total_seconds() / 3600:+.1f} h away, over ±{hours:g} h"
            )
        return PairPlan(event, grid, None, seen, 0.0, None, None, reason)
    chosen, share = best
    pu = sum(estimate_pu(w.width, w.height) for _, _, w in tiles(grid))
    start, end = pass_window(chosen.time)
    path = backscatter_path(
        grid,
        start,
        end,
        activation=event.product.activation_code,
        directory=directory or ems.directory,
        orbit=chosen.scenes[0].orbit_state,
    )
    labels = path.parent / f"{event.product.label}_{_res(grid)}m_{_grid_key(grid)}_labels.tif"
    return PairPlan(event, grid, chosen, share, pu, path, labels)


async def make_pair(
    hub: SentinelHub, plan: PairPlan, *, budget_pu: float | None = None
) -> tuple[Backscatter, Path]:
    """The backscatter GeoTIFF of the plan's pass and the label GeoTIFF on the same grid."""
    from copernicus_ems.ml import rasterize

    if plan.scene_pass is None or plan.backscatter_path is None or plan.labels_path is None:
        raise EmsError(f"{plan.product}: {plan.reason}")
    chosen = plan.scene_pass
    start, end = pass_window(chosen.time)
    observed = plan.event.product.observed_time
    image = await fetch_backscatter(
        hub,
        plan.grid,
        start,
        end,
        path=plan.backscatter_path,
        orbit_direction=chosen.scenes[0].orbit_state,
        budget_pu=budget_pu,
        tags={
            "activation": plan.event.product.activation_code,
            "pass_time": chosen.time.isoformat(),
            "platform": chosen.platform,
            "scenes": " ".join(s.id for s in chosen.scenes),
        },
    )
    # always rewritten: local and free, and labels follow the event selection
    rasterize(plan.event, grid=plan.grid).to_geotiff(plan.labels_path)
    manifest = {
        "activation": plan.event.product.activation_code,
        "product": plan.product,
        "observed_time": observed.isoformat() if observed else None,
        "made_from": [
            {"sensor": i.sensor_name, "time": i.acquisition_time.isoformat()}
            for i in plan.event.product.images
        ],
        "image": image.path.name,
        "labels": plan.labels_path.name,
        "pass_time": chosen.time.isoformat(),
        "gap_hours": round(chosen.gap_hours, 2),
        "aoi_coverage": round(plan.coverage, 3),
        "scenes": [s.id for s in chosen.scenes],
        "grid": {"crs": plan.grid.crs, "transform": plan.grid.transform},
        "bands": ["VV", "VH", "dataMask"],
        "labels_values": {"flooded": 1, "dry": 0, "not_observed": 255},
        "credit": [
            "Contains modified Copernicus Emergency Management Service information, "
            "© European Union.",
            f"{SENTINEL_CREDIT} {chosen.time.year}.",
        ],
    }
    pair_json = plan.labels_path.with_name(
        plan.labels_path.name.removesuffix("_labels.tif") + "_pair.json"
    )
    pair_json.write_text(json.dumps(manifest, indent=2))
    return image, plan.labels_path
