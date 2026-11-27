"""Classes of agreement between a Copernicus EMS flood map and Sentinel-1 radar.

Needs the ``ml`` extra (``pip install copernicus-ems[ml]``: numpy, rasterio).

Inside the observed area the map and the radar detector (the one of
:mod:`copernicus_ems.quality`) split every pixel into:

- ``confirmed``: mapped as flood and detected by the radar;
- ``map_only``: mapped as flood, not seen by the radar;
- ``candidate``: radar water outside and away from the map, in a cluster of at
  least one square kilometre (the quality check's clustered unmapped area);
- ``radar_scattered``: any other radar water outside the map;
- ``dry``: neither.

Pixels the map did not observe, or where the radar is not valid, are
``NOT_OBSERVED``. This is a **screening**, not a judgment: radar sees open
water only, so a radar-only candidate is something to look at, never ground
truth.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from copernicus_ems import quality
from copernicus_ems.dataset import Pair, find_pairs
from copernicus_ems.ml import LabelRaster, evaluate
from copernicus_ems.quality import CLUSTER_KM2, NEAR_M, _dilate, _sieve

try:
    import numpy as np
    import numpy.typing as npt
    import rasterio
    import shapely
    from pyproj import Transformer
    from rasterio.features import shapes
    from rasterio.transform import Affine
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.agreement needs the ml extra: pip install 'copernicus-ems[ml]'"
    ) from exc

DRY = 0
CONFIRMED = 1
MAP_ONLY = 2
CANDIDATE = 3
RADAR_SCATTERED = 4
NOT_OBSERVED = 255

CLASS_NAMES: dict[int, str] = {
    DRY: "dry",
    CONFIRMED: "confirmed",
    MAP_ONLY: "map_only",
    CANDIDATE: "candidate",
    RADAR_SCATTERED: "radar_scattered",
}

_FLOODED = 1


def classify(
    detected: npt.NDArray[np.bool_],
    labels: npt.NDArray[np.uint8],
    valid: npt.NDArray[np.bool_],
    resolution_m: float,
) -> npt.NDArray[np.uint8]:
    """Class of every pixel; see the module docstring for the classes."""
    observed = labels != NOT_OBSERVED
    region = observed & valid
    px_km2 = (resolution_m / 1000.0) ** 2

    mapped = (labels == _FLOODED) & region
    det = detected & region
    unmapped = det & ~mapped
    near = _dilate(mapped, round(NEAR_M / resolution_m))
    candidate = _sieve(unmapped, round(CLUSTER_KM2 / px_km2)) & ~near

    out = np.full(labels.shape, DRY, dtype=np.uint8)
    out[unmapped] = RADAR_SCATTERED
    out[candidate] = CANDIDATE
    out[mapped] = MAP_ONLY
    out[mapped & det] = CONFIRMED
    out[~region] = NOT_OBSERVED
    return out


def class_km2(classes: npt.NDArray[np.uint8], resolution_m: float) -> dict[str, float]:
    """Area of each class in km2; all five names are always present."""
    px_km2 = (resolution_m / 1000.0) ** 2
    return {name: int((classes == value).sum()) * px_km2 for value, name in CLASS_NAMES.items()}


_BAND_DESCRIPTION = "dry=0 confirmed=1 map_only=2 candidate=3 radar_scattered=4 not observed=255"
_NO_REFERENCE = "no pre-event reference (pairs --reference)"
_POLYGONIZED = (CONFIRMED, MAP_ONLY, CANDIDATE)


@dataclass(frozen=True, slots=True)
class AgreementReport:
    """Where the map and the radar agree for one pair, and what was written.

    A screening, not a judgment: ``candidate`` is radar water outside the map
    that deserves a look. ``independent`` is false for a map made from
    Sentinel-1, where agreement proves nothing.
    """

    activation: str
    product: str
    computed: bool
    reason: str | None
    independent: bool
    gap_hours: float | None
    resolution_m: float | None
    km2: dict[str, float]
    scores: dict[str, float | int | None]
    quality_verdict: str
    inputs: dict[str, str] = field(default_factory=dict)
    detector: dict[str, float] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: float | int) -> float | int | None:
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _skipped(pair: Pair, reason: str, resolution_m: float | None) -> AgreementReport:
    inputs = {"image": pair.image.name, "labels": pair.labels.name}
    if pair.reference is not None:
        inputs["reference"] = pair.reference.name
    return AgreementReport(
        activation=pair.activation,
        product=pair.product,
        computed=False,
        reason=reason,
        independent=not quality.same_image(pair.meta),
        gap_hours=pair.meta.get("gap_hours"),
        resolution_m=resolution_m,
        km2=dict.fromkeys(CLASS_NAMES.values(), 0.0),
        scores={},
        quality_verdict=quality.check_pair(pair).verdict,
        inputs=inputs,
    )


def agree_pair(pair: Pair) -> tuple[AgreementReport, npt.NDArray[np.uint8] | None]:
    """Classify one pair on disk; ``(report, None)`` when it cannot be computed."""
    reference = pair.reference
    resolution = quality._resolution(pair, None)
    if reference is None:
        return _skipped(pair, _NO_REFERENCE, resolution), None
    if abs(resolution - quality.RESOLUTION_M) > 0.5:
        reason = f"thresholds calibrated at {quality.RESOLUTION_M:.0f} m"
        return _skipped(pair, reason, resolution), None

    raster = LabelRaster.from_geotiff(pair.labels)
    grid_shape = raster.labels.shape
    vv, valid, _ = quality._read(pair.image, grid_shape)
    vv_pre, valid_pre, _ = quality._read(reference, grid_shape)
    valid = valid & valid_pre
    detected = quality.detect(vv, vv_pre)
    classes = classify(detected, raster.labels, valid, resolution)

    # scores only where the radar is valid: elsewhere nothing is predicted nor observed
    masked = LabelRaster(np.where(valid, raster.labels, 255).astype(np.uint8), raster.grid, {})
    scores = evaluate(detected & valid, masked).as_dict()
    report = AgreementReport(
        activation=pair.activation,
        product=pair.product,
        computed=True,
        reason=None,
        independent=not quality.same_image(pair.meta),
        gap_hours=pair.meta.get("gap_hours"),
        resolution_m=resolution,
        km2=class_km2(classes, resolution),
        scores={k: _finite(v) for k, v in scores.items()},
        quality_verdict=quality.check_pair(pair).verdict,
        inputs={
            "image": pair.image.name,
            "labels": pair.labels.name,
            "reference": reference.name,
        },
        detector={
            "vv_db": quality.VV_DB,
            "vv_pre_db": quality.VV_PRE_DB,
            "min_pixels": quality.MIN_PIXELS,
            "cluster_km2": quality.CLUSTER_KM2,
            "near_m": quality.NEAR_M,
        },
    )
    return report, classes


def agreement_paths(pair: Pair) -> dict[str, Path]:
    """``…_pair.json`` becomes ``…_agreement.tif``, ``.geojson`` and ``.json``."""
    base = pair.manifest.name.replace("_pair.json", "_agreement")
    return {ext: pair.manifest.with_name(f"{base}.{ext}") for ext in ("tif", "geojson", "json")}


def _write_tif(
    path: Path, classes: npt.NDArray[np.uint8], raster: LabelRaster, report: AgreementReport
) -> None:
    height, width = classes.shape
    tiled = height >= 256 and width >= 256
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "uint8",
        "crs": raster.grid.crs,
        "transform": Affine(*raster.grid.transform),
        "nodata": NOT_OBSERVED,
        "compress": "deflate",
        **({"tiled": True, "blockxsize": 256, "blockysize": 256} if tiled else {}),
    }
    partial = path.with_name(path.name + ".part")
    with rasterio.open(partial, "w", **profile) as dst:
        dst.write(classes, 1)
        dst.update_tags(activation=report.activation, product=report.product)
        dst.set_band_description(1, _BAND_DESCRIPTION)
    partial.replace(path)


def _geojson(raster: LabelRaster, classes: npt.NDArray[np.uint8]) -> dict[str, Any]:
    transform = Affine(*raster.grid.transform)
    transformer = Transformer.from_crs(raster.grid.crs, "EPSG:4326", always_xy=True)
    mask = np.isin(classes, _POLYGONIZED)
    by_class: dict[int, list[Any]] = {value: [] for value in _POLYGONIZED}
    for geom, value in shapes(classes, mask=mask, transform=transform, connectivity=8):
        by_class[int(value)].append(shape(geom))
    features = []
    for value, polygons in by_class.items():
        if not polygons:
            continue
        merged = unary_union(polygons)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "class": CLASS_NAMES[value],
                    "km2": round(merged.area / 1e6, 6),
                },
                "geometry": mapping(
                    shapely.transform(
                        merged,
                        lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])),
                    )
                ),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def write_agreement(
    pair: Pair, report: AgreementReport, classes: npt.NDArray[np.uint8] | None
) -> AgreementReport:
    """Write the report (always) and the GeoTIFF and GeoJSON (given classes);
    returns the report with ``files`` filled. The pair's own files are never touched."""
    paths = agreement_paths(pair)
    files: dict[str, str] = {}
    if classes is not None:
        raster = LabelRaster.from_geotiff(pair.labels)
        _write_tif(paths["tif"], classes, raster, report)
        files["tif"] = paths["tif"].name
        partial = paths["geojson"].with_name(paths["geojson"].name + ".part")
        partial.write_text(json.dumps(_geojson(raster, classes)))
        partial.replace(paths["geojson"])
        files["geojson"] = paths["geojson"].name
    files["json"] = paths["json"].name
    report = replace(report, files=files)
    partial = paths["json"].with_name(paths["json"].name + ".part")
    partial.write_text(json.dumps(report.as_dict(), indent=2, allow_nan=False))
    partial.replace(paths["json"])
    return report


def agree_pairs(
    directory: Path,
    codes: Iterable[str] | None = None,
    *,
    write: bool = True,
    select: Callable[[Pair], bool] | None = None,
) -> list[AgreementReport]:
    """Classify every pair under ``directory``; with ``write``, the files beside each.

    With ``select``, only the pairs it accepts are read."""
    reports = []
    for pair in find_pairs(directory, codes):
        if select is not None and not select(pair):
            continue
        report, classes = agree_pair(pair)
        if write:
            report = write_agreement(pair, report, classes)
        reports.append(report)
    return reports
