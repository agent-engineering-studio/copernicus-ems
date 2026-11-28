"""Fixed inputs of the parity tests against Limen (``tests/test_limen_parity.py``).

The same inputs are given to Limen's implementation by
``scripts/limen_parity.py`` (run once, in Limen's environment), which writes
``tests/fixtures/limen_parity.json``, and to this library by the tests. Only
the standard library, httpx and shapely: both environments can import it.
"""

from __future__ import annotations

import json
import os
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "limen_parity.json"
DETAIL = FIXTURES / "detail_EMSR762.json"
LAYER = FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json"

CODE = "EMSR762"
EVENT_TIME = datetime(2024, 9, 18, 7, 0, tzinfo=UTC)

# --- identifiers -----------------------------------------------------------------------------

LABELS = [
    ("EMSR664", "AOI01_GRA_PRODUCT"),
    ("emsr664", " aoi01_del_monit03 "),
    ("EMSR762", "AOI12_FEP_PRODUCT"),
    ("EMSR762", "AOI01_DEL_MONIT00"),
    ("EMSR762", "AOI01_DEL"),
    ("EMSR762", "AOI01_DEL_PRODUCT_40m"),
    ("EMSR762", ""),
]
POLYGON_IDS = [
    "EMSR664-aoi01-DEL1-12-0",
    "EMSR664-aoi01-DEL0-0-3",
    "EMSR664-aoi01-GRA0",
    "EMSR664-aoi10-FEP0-7-1",
    "nonsense",
]
PRODUCT_IDS = [
    "EMSR664-aoi01-DEL0",
    "EMSR664-aoi01-DEL1",
    "EMSR664-aoi01-GRA0",
    "EMSR664-aoi01-FEP0",
    "EMSR664-aoi02-DEL12",
    "EMSR664-aoi01-del1",
    "EMSR664-aoi01",
    "",
]

# --- the truth of one activation -------------------------------------------------------------

#: A layer written by hand: every branch of the selection in one file.
SYNTHETIC_LAYER: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {  # 0: a MultiPolygon: two records
            "type": "Feature",
            "properties": {
                "event_type": "5-Flood",
                "notation": "Flooded area",
                "obj_desc": "Riverine flood",
                "det_method": "Photo-interpretation",
            },
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[11.0, 44.0], [11.01, 44.0], [11.01, 44.01], [11.0, 44.01], [11.0, 44.0]]],
                    [[[11.1, 44.1], [11.13, 44.1], [11.13, 44.12], [11.1, 44.12], [11.1, 44.1]]],
                ],
            },
        },
        {  # 1: not a flood
            "type": "Feature",
            "properties": {"event_type": "6-Mass Movement", "notation": "Flooded area"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[11.2, 44.2], [11.21, 44.2], [11.21, 44.21], [11.2, 44.2]]],
            },
        },
        {  # 2: a flood trace, no description
            "type": "Feature",
            "properties": {"event_type": "5-Flood", "notation": "Flood trace"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[11.3, 44.3], [11.35, 44.3], [11.35, 44.33], [11.3, 44.33], [11.3, 44.3]],
                    [[11.31, 44.31], [11.32, 44.31], [11.32, 44.32], [11.31, 44.31]],
                ],
            },
        },
        {  # 3: no geometry
            "type": "Feature",
            "properties": {"event_type": "5-Flood", "notation": "Flooded area"},
            "geometry": None,
        },
        {  # 4: another notation
            "type": "Feature",
            "properties": {"event_type": "5-Flood", "notation": "Not analysed"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[11.4, 44.4], [11.41, 44.4], [11.41, 44.41], [11.4, 44.4]]],
            },
        },
        {  # 5: an empty geometry
            "type": "Feature",
            "properties": {"event_type": "5-Flood", "notation": "Flooded area"},
            "geometry": {"type": "Polygon", "coordinates": []},
        },
        {  # 6: no properties
            "type": "Feature",
            "properties": None,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[11.5, 44.5], [11.51, 44.5], [11.51, 44.51], [11.5, 44.5]]],
            },
        },
    ],
}


def activation() -> Any:
    from copernicus_ems import Activation

    return Activation(
        code=CODE,
        name="Flood in Emilia-Romagna, Italy",
        category="Flood",
        event_time=EVENT_TIME,
        countries=("Italy",),
        sub_category="Riverine flood",
    )


def respond(url: str, scenario: str) -> tuple[int, bytes]:
    """The service, for one scenario: status and body of ``url``.

    ``layers``: AOI01 is the real layer, AOI02 first issue the synthetic one,
    AOI02 monitoring answers 500, AOI03 first issue is not GeoJSON, every
    other layer is empty. ``corrupt-cache``: every layer is the real one (the
    damaged file is written by :func:`damage_cache`).
    """
    if "public-activations/" in url:
        return 200, DETAIL.read_bytes()
    if scenario == "corrupt-cache":
        return 200, LAYER.read_bytes()
    if "AOI01_DEL_PRODUCT" in url:
        return 200, LAYER.read_bytes()
    if "AOI02_DEL_PRODUCT" in url:
        return 200, json.dumps(SYNTHETIC_LAYER).encode()
    if "AOI02_DEL_MONIT01" in url:
        return 500, b"upstream error"
    if "AOI03_DEL_PRODUCT" in url:
        return 200, b"<html>not json</html>"
    return 200, b'{"type": "FeatureCollection", "features": []}'


def damage_cache(path: Path) -> None:
    """A cache file cut short: it must be removed and downloaded again."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"type": "FeatureCollection", "featu')


# --- the radar reports -----------------------------------------------------------------------


def _write(path: Path, body: Any, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    os.utime(path, (mtime, mtime))


def _square(x: float, y: float, side: float = 0.01) -> list[list[list[float]]]:
    return [[[x, y], [x + side, y], [x + side, y + side], [x, y + side], [x, y]]]


def write_reports(root: Path) -> None:
    """A tree of quality and agreement files with every case the reader handles."""
    s1 = root / "EMSR664" / "sentinel-1"
    # AOI01 first issue: quality, agreement and geojson, the 40 m pair.
    stem = "AOI01_DEL_PRODUCT_40m_20230520T051500"
    quality = {
        "activation": "EMSR664",
        "product": "AOI01_DEL_PRODUCT",
        "verdict": "to check",
        "reason": None,
        "flags": [
            {"code": "possibly_partial", "message": "x", "value": float("nan")},
            {"code": "", "message": "no code"},
            {"code": "much_unmapped_water", "values": [1.5, float("inf")]},
        ],
        "measurements": {"map_km2": 12.5, "ratio": float("inf"), "nested": {"a": float("nan")}},
        "thresholds": {"partial_extent": 0.1},
        "gap_hours": 6.5,
        "resolution_m": 40,
    }
    _write(s1 / f"{stem}_quality.json", quality, 1_700_000_100)
    agreement = {
        "activation": "EMSR664",
        "product": "AOI01_DEL_PRODUCT",
        "computed": True,
        "reason": None,
        "independent": True,
        "gap_hours": 6.5,
        "resolution_m": 40.0,
        "km2": {"confirmed": 8.0, "map_only": 4.5, "candidate": 2.25, "bad": float("nan")},
        "scores": {"csi": 0.5, "pod": None, "n": 3},
        "quality_verdict": "to check",
        "files": {"geojson": f"{stem}_agreement.geojson", "tif": f"{stem}_agreement.tif"},
    }
    _write(s1 / f"{stem}_agreement.json", agreement, 1_700_000_100)
    classes = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"class": "confirmed", "km2": 8.0},
                "geometry": {"type": "Polygon", "coordinates": _square(11.0, 44.0)},
            },
            {
                "type": "Feature",
                "properties": {"class": "map_only", "km2": 4.5},
                "geometry": {"type": "Polygon", "coordinates": _square(11.1, 44.0)},
            },
            {
                "type": "Feature",
                "properties": {"class": "candidate", "km2": "many"},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [_square(11.2, 44.0), _square(11.3, 44.0)],
                },
            },
            {"type": "Feature", "properties": {"class": "confirmed"}, "geometry": None},
            {"type": "Feature", "properties": None, "geometry": None},
        ],
    }
    _write(s1 / f"{stem}_agreement.geojson", classes, 1_700_000_100)
    # The same product, an older pair: the newer one above is kept.
    old = "AOI01_DEL_PRODUCT_40m_20230519T170000"
    _write(s1 / f"{old}_quality.json", {**quality, "verdict": "coherent"}, 1_600_000_000)
    # AOI02 monitoring: two pairs with the same time: the greater stem wins. Quality only.
    for name, verdict in (("a", "coherent"), ("b", "not assessable")):
        body = {"product": "AOI02_DEL_MONIT03", "verdict": verdict, "reason": name}
        _write(s1 / f"AOI02_DEL_MONIT03_40m_{name}_quality.json", body, 1_700_000_000)
    # AOI03: agreement not computed, no quality file; an unknown verdict in it.
    not_computed = {
        "activation": "emsr664",
        "product": "AOI03_GRA_PRODUCT",
        "computed": False,
        "reason": "no pre-event reference (pairs --reference)",
        "independent": None,
        "quality_verdict": "great",
        "gap_hours": "soon",
        "resolution_m": True,
        "files": {"geojson": "never_read.geojson"},
    }
    _write(s1 / "AOI03_GRA_PRODUCT_20m_x_agreement.json", not_computed, 1_700_000_000)
    # AOI04: the agreement names a geojson that is not there.
    lost = {**agreement, "product": "AOI04_FEP_PRODUCT", "files": {"geojson": "lost.geojson"}}
    _write(s1 / "AOI04_FEP_PRODUCT_40m_x_agreement.json", lost, 1_700_000_000)
    # AOI05: the geojson is not JSON.
    broken = {
        **agreement,
        "product": "AOI05_DEL_PRODUCT",
        "independent": 0,
        "files": {"geojson": "AOI05_DEL_PRODUCT_40m_x_agreement.geojson"},
    }
    _write(s1 / "AOI05_DEL_PRODUCT_40m_x_agreement.json", broken, 1_700_000_000)
    _write(s1 / "AOI05_DEL_PRODUCT_40m_x_agreement.geojson", "{not json", 1_700_000_000)
    # AOI06: an unknown verdict: the file is skipped, and with it the product.
    unknown = {"product": "AOI06_DEL_PRODUCT", "verdict": "excellent"}
    _write(s1 / "AOI06_DEL_PRODUCT_40m_x_quality.json", unknown, 1_700_000_000)
    # AOI07: quality unreadable, agreement readable: the agreement stands alone.
    _write(s1 / "AOI07_DEL_PRODUCT_40m_x_quality.json", "{", 1_700_000_000)
    alone = {**agreement, "product": "AOI07_DEL_PRODUCT", "quality_verdict": "coherent"}
    alone["files"] = {}
    _write(s1 / "AOI07_DEL_PRODUCT_40m_x_agreement.json", alone, 1_700_000_000)
    # AOI08: no product in the file.
    _write(s1 / "AOI08_DEL_PRODUCT_40m_x_quality.json", {"verdict": "coherent"}, 1_700_000_000)
    # A label of another form.
    odd = {"product": "WHOLE_AREA", "verdict": "coherent"}
    _write(s1 / "WHOLE_AREA_40m_x_quality.json", odd, 1_700_000_000)
    # Another activation, in lower case on disk, with no activation in the file.
    other = {"product": "AOI01_GRA_MONIT01", "verdict": "coherent", "flags": None}
    _write(
        root / "emsr762" / "sentinel-1" / "AOI01_GRA_MONIT01_40m_x_quality.json",
        other,
        1_700_000_000,
    )
    # Not under sentinel-1: never read.
    _write(root / "EMSR664" / "AOI09_DEL_PRODUCT_40m_x_quality.json", other, 1_700_000_000)


# --- water over places and zones (metric coordinates) ----------------------------------------


def _blob(rng: random.Random, cx: float, cy: float, radius: float, points: int = 24) -> Polygon:
    """An irregular polygon around a centre: a star-shaped ring with random radii."""
    import math

    ring = []
    for k in range(points):
        angle = 2 * math.pi * k / points
        r = radius * rng.uniform(0.55, 1.0)
        ring.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return Polygon(ring)


def _when(day: int, hour: int) -> datetime:
    return datetime(2023, 5, day, hour, 0, tzinfo=UTC)


def water_polygons() -> list[dict[str, Any]]:
    """Water of three products around (4 400 000, 2 400 000) in EPSG:3035, as plain records."""
    rng = random.Random(713)
    x0, y0 = 4_400_000.0, 2_400_000.0
    out: list[dict[str, Any]] = []
    for k in range(40):
        product = ("EMSR664-aoi01-DEL0", "EMSR664-aoi02-DEL0", "EMSR664-aoi02-GRA0")[k % 3]
        geom: BaseGeometry = _blob(
            rng, x0 + rng.uniform(0, 12_000), y0 + rng.uniform(0, 9_000), rng.uniform(150, 900)
        )
        out.append(
            {
                "product": product,
                "geom": geom,
                "start": _when(16 + k % 2, 6),
                "observed": None if k % 7 == 0 else _when(17 + k % 4, 5 + k % 12),
            }
        )
    # A bow tie (invalid), a MultiPolygon, and one polygon far from every place.
    bow = Polygon([(x0, y0), (x0 + 800, y0 + 800), (x0 + 800, y0), (x0, y0 + 800)])
    out.append(
        {"product": "EMSR664-aoi01-DEL0", "geom": bow, "start": _when(16, 6), "observed": None}
    )
    multi = MultiPolygon(
        [
            box(x0 + 3_000, y0 + 3_000, x0 + 3_400, y0 + 3_300),
            box(x0 + 3_300, y0 + 3_200, x0 + 3_900, y0 + 3_700),
        ]
    )
    out.append(
        {
            "product": "EMSR664-aoi02-GRA0",
            "geom": multi,
            "start": _when(15, 6),
            "observed": _when(21, 9),
        }
    )
    far = box(x0 + 60_000, y0, x0 + 61_000, y0 + 1_000)
    out.append(
        {
            "product": "EMSR664-aoi01-DEL0",
            "geom": far,
            "start": _when(16, 6),
            "observed": _when(17, 5),
        }
    )
    # Exactly along the western border of the grid of places: it touches, no area inside.
    edge = box(x0 - 500, y0 + 20_000, x0, y0 + 20_500)
    out.append(
        {"product": "EMSR664-aoi01-DEL0", "geom": edge, "start": _when(16, 6), "observed": None}
    )
    return out


def places() -> list[tuple[str, BaseGeometry]]:
    """A grid of 3 km squares with ISTAT-like codes, plus a bow tie and a far square."""
    x0, y0 = 4_400_000.0, 2_400_000.0
    out: list[tuple[str, BaseGeometry]] = []
    n = 0
    for i in range(5):
        for j in range(4):
            n += 1
            # Not in order of code: the sort of the result must not depend on it.
            code = f"0370{(n * 7) % 23:02d}"
            out.append(
                (
                    code,
                    box(x0 + i * 3_000, y0 + j * 3_000, x0 + (i + 1) * 3_000, y0 + (j + 1) * 3_000),
                )
            )
    out.append(
        (
            "099001",
            Polygon([(x0, y0), (x0 + 2_000, y0 + 2_000), (x0 + 2_000, y0), (x0, y0 + 2_000)]),
        )
    )
    out.append(("099002", box(x0 + 200_000, y0, x0 + 203_000, y0 + 3_000)))
    # Beside the edge polygon: touched on the border alone.
    out.append(("099003", box(x0, y0 + 20_000, x0 + 3_000, y0 + 23_000)))
    return out


CLASSES = ("P3", "P2", "P1")

#: Parameters of the split: defaults, no tolerance, a small tile, a coarse filter.
SPLIT_CASES: list[dict[str, float]] = [
    {},
    {"tolerance_m": 0.0, "min_patch_m2": 0.0},
    {"tile_m": 700.0, "tolerance_m": 20.0, "min_patch_m2": 5_000.0},
    {"tolerance_m": 150.0, "min_patch_m2": 200_000.0, "tile_m": 5_000.0},
]


def split_water() -> list[BaseGeometry]:
    """The geometries of :func:`water_polygons`, with a line and an empty one among them."""
    from shapely.geometry import LineString

    geoms = [p["geom"] for p in water_polygons()]
    return [*geoms, LineString([(0, 0), (10, 10)]), Polygon()]


def zones() -> list[tuple[str, BaseGeometry]]:
    """Nested hazard zones in pieces: P1 contains P2 contains P3, as hazard layers do."""
    rng = random.Random(64)
    x0, y0 = 4_400_000.0, 2_400_000.0
    out: list[tuple[str, BaseGeometry]] = []
    for _ in range(9):
        cx, cy = x0 + rng.uniform(0, 12_000), y0 + rng.uniform(0, 9_000)
        outer = _blob(rng, cx, cy, rng.uniform(900, 1_800), points=36)
        out.append(("P1", outer))
        out.append(("P2", outer.buffer(-rng.uniform(100, 250))))
        out.append(("P3", outer.buffer(-rng.uniform(300, 450))))
    # A P2 with no P1 around it, and a P3 cut in two pieces along a line.
    out.append(("P2", box(x0 + 5_000, y0 + 4_000, x0 + 6_000, y0 + 4_600)))
    out.append(("P3", box(x0 + 8_000, y0 + 1_000, x0 + 8_500, y0 + 2_000)))
    out.append(("P3", box(x0 + 8_500, y0 + 1_000, x0 + 9_000, y0 + 2_000)))
    return [(c, g) for c, g in out if not g.is_empty]


# --- one shape for what both sides return ----------------------------------------------------


def iso(when: datetime | None) -> str | None:
    return None if when is None else when.isoformat()
