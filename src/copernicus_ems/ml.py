"""Label rasters and mask-aware scores for flood models.

Needs the ``ml`` extra (``pip install copernicus-ems[ml]``: numpy, shapely,
pyproj). A product becomes a **tri-state** label raster:

========  ==========  =====================================
value     meaning     where
========  ==========  =====================================
``1``     flooded     inside an ``observedEventA`` feature
``0``     dry         inside the AOI, outside every feature
``255``   ignore      outside the AOI: nobody looked
========  ==========  =====================================

``255`` is meant for ``ignore_index`` in a training loss, and :func:`evaluate`
leaves it out of the contingency table: a prediction where nobody looked is
reported (:attr:`Scores.unobserved_predicted`), never counted as a false
alarm. That is the whole point of the observation mask.

Rasters are in a metric CRS, by default the UTM zone of the AOI centre;
pixels are labelled by their centre.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import numpy.typing as npt
    import shapely
    from pyproj import CRS, Transformer
    from shapely.geometry import shape
    from shapely.geometry.base import BaseGeometry
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.ml needs the ml extra: pip install 'copernicus-ems[ml]'"
    ) from exc

from copernicus_ems.client import ProductEvents
from copernicus_ems.geo import flooded, mask

FLOODED = 1
DRY = 0
IGNORE = 255

_ROWS_PER_BLOCK = 256


@dataclass(frozen=True, slots=True)
class Grid:
    """A north-up pixel grid: ``width`` by ``height`` pixels of ``resolution_m``.

    ``x0``, ``y0`` are the top-left corner in ``crs`` (e.g. ``EPSG:32632``).
    """

    crs: str
    x0: float
    y0: float
    resolution_m: float
    width: int
    height: int

    @property
    def transform(self) -> tuple[float, float, float, float, float, float]:
        """The GDAL-style affine transform, as rasterio's ``Affine`` takes it."""
        return (self.resolution_m, 0.0, self.x0, 0.0, -self.resolution_m, self.y0)

    @property
    def pixel_area_km2(self) -> float:
        return self.resolution_m**2 / 1e6

    def window(self, row: int, col: int, height: int, width: int) -> Grid:
        """The sub-grid starting at pixel ``(row, col)``."""
        return replace(
            self,
            x0=self.x0 + col * self.resolution_m,
            y0=self.y0 - row * self.resolution_m,
            width=width,
            height=height,
        )


@dataclass(frozen=True, slots=True)
class LabelRaster:
    """A tri-state label raster (``uint8``, rows by columns) and its grid."""

    labels: npt.NDArray[np.uint8]
    grid: Grid
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Scores:
    """Contingency table of a binary flood prediction, inside the observation mask.

    Ratios are ``nan`` when undefined (e.g. :attr:`far` with no predicted
    flood). Scores add up: ``sum(per_product, Scores.zero())`` pools pixels.
    """

    hits: int
    misses: int
    false_alarms: int
    correct_negatives: int
    unobserved_predicted: int
    pixel_area_km2: float

    @classmethod
    def zero(cls, pixel_area_km2: float = 0.0) -> Scores:
        return cls(0, 0, 0, 0, 0, pixel_area_km2)

    def __add__(self, other: Scores) -> Scores:
        if (
            self.pixel_area_km2
            and other.pixel_area_km2
            and not math.isclose(self.pixel_area_km2, other.pixel_area_km2)
        ):
            raise ValueError("cannot add scores computed at different resolutions")
        return Scores(
            self.hits + other.hits,
            self.misses + other.misses,
            self.false_alarms + other.false_alarms,
            self.correct_negatives + other.correct_negatives,
            self.unobserved_predicted + other.unobserved_predicted,
            self.pixel_area_km2 or other.pixel_area_km2,
        )

    @property
    def csi(self) -> float:
        """Critical success index, ``hits / (hits + misses + false alarms)``; the IoU of flood."""
        return _ratio(self.hits, self.hits + self.misses + self.false_alarms)

    @property
    def iou(self) -> float:
        """Intersection over union of the flooded class: the same number as :attr:`csi`."""
        return self.csi

    @property
    def pod(self) -> float:
        """Probability of detection (recall), ``hits / (hits + misses)``."""
        return _ratio(self.hits, self.hits + self.misses)

    @property
    def far(self) -> float:
        """False alarm ratio, ``false alarms / (hits + false alarms)``."""
        return _ratio(self.false_alarms, self.hits + self.false_alarms)

    @property
    def f1(self) -> float:
        return _ratio(2 * self.hits, 2 * self.hits + self.misses + self.false_alarms)

    @property
    def bias(self) -> float:
        """Frequency bias, predicted over observed flood: above 1 over-predicts."""
        return _ratio(self.hits + self.false_alarms, self.hits + self.misses)

    @property
    def unobserved_predicted_km2(self) -> float:
        """Predicted flood outside the AOI: not an error, nobody looked."""
        return self.unobserved_predicted * self.pixel_area_km2

    def as_dict(self) -> dict[str, float | int]:
        names = ("csi", "pod", "far", "f1", "bias", "unobserved_predicted_km2")
        return {
            "hits": self.hits,
            "misses": self.misses,
            "false_alarms": self.false_alarms,
            "correct_negatives": self.correct_negatives,
            "unobserved_predicted": self.unobserved_predicted,
            **{name: getattr(self, name) for name in names},
        }


@dataclass(frozen=True, slots=True)
class Chip:
    """A square tile of a :class:`LabelRaster`, for training."""

    labels: npt.NDArray[np.uint8]
    grid: Grid
    row: int
    col: int
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def flooded_share(self) -> float:
        """Flooded fraction of the observed pixels of the chip."""
        observed = int(np.count_nonzero(self.labels != IGNORE))
        return int(np.count_nonzero(self.labels == FLOODED)) / observed if observed else 0.0

    def save(self, directory: Path) -> Path:
        """``labels`` as ``.npy`` and a ``.json`` sidecar (grid, provenance); the ``.npy`` path."""
        directory.mkdir(parents=True, exist_ok=True)
        stem = "_".join(str(self.meta.get(k, "")) for k in ("activation", "product")).strip("_")
        path = directory / f"{stem or 'chip'}_r{self.row}_c{self.col}.npy"
        np.save(path, self.labels)
        sidecar = {
            "crs": self.grid.crs,
            "transform": self.grid.transform,
            "row": self.row,
            "col": self.col,
            "values": {"flooded": FLOODED, "dry": DRY, "ignore": IGNORE},
            **self.meta,
        }
        path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2))
        return path


def utm_crs(lon: float, lat: float) -> str:
    """The UTM zone of a point, as ``EPSG:326xx`` (north) or ``EPSG:327xx`` (south)."""
    zone = min(int((lon + 180) // 6) + 1, 60)
    return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"


def grid_for(event: ProductEvents, resolution_m: float = 20.0, *, crs: str | None = None) -> Grid:
    """The grid covering the event's AOI, snapped to ``resolution_m``."""
    if resolution_m <= 0:
        raise ValueError("resolution_m must be positive")
    aoi = mask(event)
    if crs is None:
        centre = aoi.centroid
        crs = utm_crs(centre.x, centre.y)
    minx, miny, maxx, maxy = _project(aoi, crs).bounds
    x0 = math.floor(minx / resolution_m) * resolution_m
    y0 = math.ceil(maxy / resolution_m) * resolution_m
    width = math.ceil((maxx - x0) / resolution_m)
    height = math.ceil((y0 - miny) / resolution_m)
    return Grid(crs=crs, x0=x0, y0=y0, resolution_m=resolution_m, width=width, height=height)


def burn(geometry: BaseGeometry | dict[str, Any], grid: Grid) -> npt.NDArray[np.bool_]:
    """``True`` where a pixel centre falls in ``geometry`` (lon/lat, or GeoJSON)."""
    geom = shape(geometry) if isinstance(geometry, dict) else geometry
    out = np.zeros((grid.height, grid.width), dtype=bool)
    if geom.is_empty:
        return out
    projected = _project(geom, grid.crs)
    shapely.prepare(projected)
    res = grid.resolution_m
    minx, miny, maxx, maxy = projected.bounds
    col0 = max(math.floor((minx - grid.x0) / res), 0)
    col1 = min(math.ceil((maxx - grid.x0) / res), grid.width)
    row0 = max(math.floor((grid.y0 - maxy) / res), 0)
    row1 = min(math.ceil((grid.y0 - miny) / res), grid.height)
    if col0 >= col1 or row0 >= row1:
        return out
    xs = grid.x0 + (np.arange(col0, col1) + 0.5) * res
    for start in range(row0, row1, _ROWS_PER_BLOCK):
        stop = min(start + _ROWS_PER_BLOCK, row1)
        ys = grid.y0 - (np.arange(start, stop) + 0.5) * res
        gx, gy = np.meshgrid(xs, ys)
        out[start:stop, col0:col1] = shapely.contains_xy(projected, gx, gy)
    return out


def rasterize(
    event: ProductEvents,
    resolution_m: float = 20.0,
    *,
    grid: Grid | None = None,
    crs: str | None = None,
) -> LabelRaster:
    """The tri-state :class:`LabelRaster` of one product (see the module docs)."""
    grid = grid or grid_for(event, resolution_m, crs=crs)
    labels = np.full((grid.height, grid.width), IGNORE, dtype=np.uint8)
    labels[burn(mask(event), grid)] = DRY
    labels[burn(flooded(event), grid)] = FLOODED
    observed = event.product.observed_time
    meta = {
        "activation": event.product.activation_code,
        "product": event.product.label,
        "aoi_name": event.product.aoi_name,
        "observed_time": observed.isoformat() if observed else None,
        "source": "Copernicus Emergency Management Service, © European Union",
    }
    return LabelRaster(labels=labels, grid=grid, meta=meta)


def evaluate(predicted: npt.ArrayLike, truth: LabelRaster) -> Scores:
    """Score a flood prediction on ``truth``'s grid, inside the observation mask.

    ``predicted`` is a boolean (or 0/1) array with the shape of ``truth.labels``.
    """
    pred = np.asarray(predicted).astype(bool)
    if pred.shape != truth.labels.shape:
        raise ValueError(f"prediction shape {pred.shape} != labels shape {truth.labels.shape}")
    wet = truth.labels == FLOODED
    dry = truth.labels == DRY
    unseen = truth.labels == IGNORE
    return Scores(
        hits=int(np.count_nonzero(pred & wet)),
        misses=int(np.count_nonzero(~pred & wet)),
        false_alarms=int(np.count_nonzero(pred & dry)),
        correct_negatives=int(np.count_nonzero(~pred & dry)),
        unobserved_predicted=int(np.count_nonzero(pred & unseen)),
        pixel_area_km2=truth.grid.pixel_area_km2,
    )


def evaluate_geometry(
    predicted: BaseGeometry | dict[str, Any],
    event: ProductEvents,
    resolution_m: float = 20.0,
) -> Scores:
    """Score a predicted flood extent (lon/lat geometry, GeoJSON geometry or
    FeatureCollection) against one product, rasterized at ``resolution_m``."""
    truth = rasterize(event, resolution_m)
    return evaluate(burn(_geometry(predicted), truth.grid), truth)


def chips(
    raster: LabelRaster,
    size: int = 256,
    *,
    stride: int | None = None,
    min_observed: float = 0.5,
    min_flooded: float = 0.0,
) -> Iterator[Chip]:
    """Square tiles of ``raster``, skipping the ones mostly outside the AOI.

    A chip is kept when at least ``min_observed`` of its pixels are observed
    and at least ``min_flooded`` of the observed ones are flooded. Edge
    tiles are padded with :data:`IGNORE`.
    """
    if size < 1:
        raise ValueError("size must be positive")
    step = stride or size
    height, width = raster.labels.shape
    for row in range(0, height, step):
        for col in range(0, width, step):
            tile = np.full((size, size), IGNORE, dtype=np.uint8)
            part = raster.labels[row : row + size, col : col + size]
            tile[: part.shape[0], : part.shape[1]] = part
            chip = Chip(
                labels=tile,
                grid=raster.grid.window(row, col, size, size),
                row=row,
                col=col,
                meta=raster.meta,
            )
            observed = np.count_nonzero(tile != IGNORE) / tile.size
            if observed >= min_observed and observed > 0 and chip.flooded_share >= min_flooded:
                yield chip


def _geometry(value: BaseGeometry | dict[str, Any]) -> BaseGeometry:
    if not isinstance(value, dict):
        return value
    if value.get("type") == "FeatureCollection":
        return shapely.union_all(
            [shape(f["geometry"]) for f in value["features"] if f.get("geometry")]
        )
    if value.get("type") == "Feature":
        return shape(value["geometry"])
    return shape(value)


def _project(geometry: BaseGeometry, crs: str) -> BaseGeometry:
    transformer = Transformer.from_crs(
        CRS.from_epsg(4326), CRS.from_user_input(crs), always_xy=True
    )
    return shapely.transform(
        geometry, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1]))
    )


def _ratio(num: int, den: int) -> float:
    return num / den if den else math.nan
