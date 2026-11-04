"""Observed events as files: GeoJSON (core) or GeoParquet (``parquet`` extra).

Every feature is tagged with where it comes from (``activation``,
``product``, ``aoi_name``, ``observed_time``); the observation masks are
features too, one per product, with the same tags. GeoParquet follows the
`GeoParquet 1.1 <https://geoparquet.org/releases/v1.1.0/>`_ specification
(WKB geometry, lon/lat CRS84), and opens in GeoPandas, DuckDB and QGIS.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from copernicus_ems.client import ProductEvents

#: The version of the GeoParquet specification written.
GEOPARQUET_VERSION = "1.1.0"


def tags(event: ProductEvents) -> dict[str, Any]:
    """Where an event comes from, as feature properties."""
    observed = event.product.observed_time
    return {
        "activation": event.product.activation_code,
        "product": event.product.label,
        "aoi_name": event.product.aoi_name,
        "observed_time": observed.isoformat() if observed else None,
    }


def tagged_features(events: Iterable[ProductEvents]) -> list[dict[str, Any]]:
    """Every feature of the events, its properties extended with :func:`tags`."""
    out: list[dict[str, Any]] = []
    for event in events:
        extra = tags(event)
        out.extend(
            {**f, "properties": {**(f.get("properties") or {}), **extra}} for f in event.features
        )
    return out


def mask_features(events: Iterable[ProductEvents]) -> list[dict[str, Any]]:
    """One feature per product: its observation mask (AOI), with :func:`tags`."""
    return [
        {"type": "Feature", "geometry": geometry, "properties": tags(event)}
        for event in events
        if (geometry := event.product.aoi_extent) is not None
    ]


def write_geojson(path: Path, features: list[dict[str, Any]]) -> Path:
    """A GeoJSON FeatureCollection, written whole (``.part`` then renamed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    partial.replace(path)
    return path


def write_geoparquet(path: Path, features: list[dict[str, Any]]) -> Path:
    """A GeoParquet 1.1 file: one row per feature, a WKB ``geometry`` column.

    Needs the ``parquet`` extra (pyarrow, shapely). Property columns are
    typed when their values agree, else stored as text; ``observed_time``
    is a UTC timestamp.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import shapely
        from shapely.geometry import shape
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "GeoParquet needs the parquet extra: pip install 'copernicus-ems[parquet]'"
        ) from exc

    geometries = [shape(f["geometry"]) for f in features]
    names: dict[str, None] = {}
    for feature in features:
        names.update(dict.fromkeys(feature.get("properties") or {}))
    columns: dict[str, Any] = {}
    for name in names:
        values = [(f.get("properties") or {}).get(name) for f in features]
        columns[name] = _column(pa, name, values)
    columns["geometry"] = pa.array(shapely.to_wkb(geometries).tolist(), type=pa.binary())

    bounds = shapely.total_bounds(geometries) if geometries else None
    meta = {
        "version": GEOPARQUET_VERSION,
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": sorted({g.geom_type for g in geometries}),
                **({"bbox": [float(v) for v in bounds]} if bounds is not None else {}),
            }
        },
    }
    table = pa.table(columns).replace_schema_metadata({"geo": json.dumps(meta)})
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    pq.write_table(table, partial, compression="zstd")
    partial.replace(path)
    return path


def write_features(path: Path, features: list[dict[str, Any]]) -> Path:
    """GeoParquet for ``.parquet`` / ``.geoparquet``, GeoJSON otherwise."""
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        return write_geoparquet(path, features)
    return write_geojson(path, features)


def _column(pa: Any, name: str, values: list[Any]) -> Any:
    if name == "observed_time":
        times = [datetime.fromisoformat(v) if isinstance(v, str) else None for v in values]
        return pa.array(times, type=pa.timestamp("us", tz="UTC"))
    try:
        return pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError):
        return pa.array(
            [None if v is None else v if isinstance(v, str) else json.dumps(v) for v in values],
            type=pa.string(),
        )
