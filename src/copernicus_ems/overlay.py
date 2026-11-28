"""Observed water laid over other polygons: places it touched, zones it fell in.

Needs the ``geo`` extra (shapely). Everything here is planar: geometries
come in **already in one metric CRS** (EPSG:3035 for Europe), and areas go
out in m² or km² of that CRS. The other polygons are the caller's: any
places with a key (:func:`places_hit`), any zones with a class
(:func:`split_by_zones`). This module knows no layer by name.

The water of an activation is the **union** of the products that are not
monitoring ones (:func:`copernicus_ems.ids.is_monitoring`): a later product
draws the same place on another day, and a sum would count one flood twice.
It is water **observed afterwards**, from satellite: a lower bound of what
happened, never an alert or a forecast.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime

try:
    import shapely
    from shapely.geometry import box
    from shapely.geometry.base import BaseGeometry
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.overlay needs the geo extra: pip install 'copernicus-ems[geo]'"
    ) from exc

#: Defaults of :func:`split_by_zones`: those the reference numbers were taken with.
TOLERANCE_M = 50.0
MIN_PATCH_M2 = 50_000.0
#: Side of the tiles the water is cut into. One activation can be a single
#: patch of hundreds of km² with hundreds of thousands of vertices; whole,
#: against every piece of zone, it costs minutes.
TILE_M = 2_000.0


@dataclass(frozen=True, slots=True)
class WaterPolygon:
    """One polygon of observed water, in a metric CRS."""

    product: str
    geom: BaseGeometry
    start: datetime
    observed: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlaceHit:
    """One place touched by the water."""

    key: str
    water_km2: float
    start: datetime
    observed_from: datetime | None
    observed_to: datetime | None
    products: tuple[str, ...]


def places_hit(
    water: Sequence[WaterPolygon], places: Sequence[tuple[str, BaseGeometry]]
) -> list[PlaceHit]:
    """One record per place touched by the water, the most flooded first.

    ``places`` are pairs (key, polygon) in the CRS of the water: municipalities
    by their code, or any other areas. Per place the pieces of water are
    **united**, not summed: two products of different AOIs can draw the same
    field. A place touched on its border alone is left out. Equal areas come
    in the order of the key, then in the order given.
    """
    if not water or not places:
        return []
    water_geoms = shapely.make_valid([p.geom for p in water])
    tree = shapely.STRtree(water_geoms)
    out: list[PlaceHit] = []
    for key, geom in places:
        border = shapely.make_valid(geom)
        found = tree.query(border, predicate="intersects")
        if not len(found):
            continue
        touched = [water[i] for i in found]
        pieces = shapely.intersection(border, shapely.union_all(water_geoms[found]))
        if pieces.area <= 0:
            continue
        observed = [p.observed for p in touched if p.observed is not None]
        out.append(
            PlaceHit(
                key=key,
                water_km2=float(pieces.area) / 1e6,
                start=min(p.start for p in touched),
                observed_from=min(observed, default=None),
                observed_to=max(observed, default=None),
                products=tuple(sorted({p.product for p in touched})),
            )
        )
    return sorted(out, key=lambda r: (-r.water_km2, r.key))


@dataclass(frozen=True, slots=True)
class ZoneSplit:
    """Square metres of observed water per class, and outside every zone."""

    water_m2: float
    by_class_m2: dict[str, float]
    outside_m2: float
    outside_filtered_m2: float
    outside_patches: int

    def share(self, m2: float) -> float | None:
        """``m2`` as a fraction of the water; ``None`` when there is no water."""
        return m2 / self.water_m2 if self.water_m2 > 0 else None


def _tiles(parts: Sequence[BaseGeometry], side: float) -> Iterator[BaseGeometry]:
    tree = shapely.STRtree(parts)
    minx, miny, maxx, maxy = shapely.total_bounds(parts)
    x = math.floor(minx / side) * side
    while x < maxx:
        y = math.floor(miny / side) * side
        while y < maxy:
            square = box(x, y, x + side, y + side)
            inside = [parts[i] for i in tree.query(square, predicate="intersects")]
            if inside:
                piece = shapely.intersection(shapely.union_all(inside), square)
                if not piece.is_empty and piece.area > 0:
                    yield piece
            y += side
        x += side


def _area_inside(water: BaseGeometry, zones: list[BaseGeometry]) -> float:
    if not zones:
        return 0.0
    return float(shapely.intersection(water, shapely.union_all(zones)).area)


def split_by_zones(
    water: Sequence[BaseGeometry],
    zones: Sequence[tuple[str, BaseGeometry]],
    *,
    classes: Sequence[str],
    tolerance_m: float = TOLERANCE_M,
    min_patch_m2: float = MIN_PATCH_M2,
    tile_m: float = TILE_M,
) -> ZoneSplit:
    """The water (the union of ``water``) split among the classes of ``zones`` and outside.

    ``zones`` are pairs (class, polygon), best cut into small pieces
    beforehand; ``classes`` names the classes **from the highest**. Classes
    are exclusive and the highest wins: when the zones of a lower class
    contain those of a higher one (hazard classes usually do), water is
    counted once, in the highest. A zone of a class not in ``classes`` is a
    ``ValueError``.

    The part **outside** every zone is given twice. Raw, it is inflated by
    thin strips along the borders of the zones, where two perimeters do not
    match to the metre. Filtered, it counts only water further than
    ``tolerance_m`` from every zone, in patches of at least ``min_patch_m2``.
    """
    ranked = tuple(classes)
    unknown = sorted({c for c, _ in zones} - set(ranked))
    if unknown:
        raise ValueError(f"zones of classes not in classes: {', '.join(unknown)}")
    parts = [
        g
        for g in shapely.get_parts(shapely.make_valid(list(water)))
        if g.geom_type in ("Polygon", "MultiPolygon") and g.area > 0
    ]
    if not parts:
        return ZoneSplit(0.0, dict.fromkeys(ranked, 0.0), 0.0, 0.0, 0)
    names = [c for c, _ in zones]
    geoms = [g for _, g in zones]
    tree = shapely.STRtree(geoms)
    total = 0.0
    cumulated = dict.fromkeys(ranked, 0.0)
    rests: list[BaseGeometry] = []
    for piece in _tiles(parts, tile_m):
        total += piece.area
        near = tree.query(piece, predicate="dwithin", distance=tolerance_m)
        for k, name in enumerate(ranked):
            allowed = ranked[: k + 1]
            cumulated[name] += _area_inside(piece, [geoms[i] for i in near if names[i] in allowed])
        rest = piece
        if len(near):
            close = shapely.union_all([geoms[i] for i in near])
            rest = shapely.difference(piece, shapely.buffer(close, tolerance_m))
        if not rest.is_empty:
            rests.append(rest)
    by_class: dict[str, float] = {}
    previous = 0.0
    for name in ranked:
        by_class[name] = max(cumulated[name] - previous, 0.0)
        previous = cumulated[name]
    patches = [
        m
        for m in shapely.get_parts(shapely.union_all(rests) if rests else shapely.Polygon())
        if m.area >= min_patch_m2
    ]
    return ZoneSplit(
        water_m2=total,
        by_class_m2=by_class,
        outside_m2=max(total - previous, 0.0),
        outside_filtered_m2=sum(m.area for m in patches),
        outside_patches=len(patches),
    )
