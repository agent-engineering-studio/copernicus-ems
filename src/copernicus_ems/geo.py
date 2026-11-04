"""Geometry over observed events: flooded, observed dry, areas in km².

Needs the ``geo`` extra (``pip install copernicus-ems[geo]``: shapely and
pyproj). Areas are geodesic, on the WGS84 ellipsoid, so they do not depend
on a projection. A product splits the map in three:

* inside a feature: **observed flooded** (:func:`flooded`);
* inside the AOI, outside every feature: **observed dry** (:func:`observed_dry`);
* outside the AOI: **not observed**.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    import shapely
    from pyproj import Geod
    from shapely.geometry import shape
    from shapely.geometry.base import BaseGeometry
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.geo needs the geo extra: pip install 'copernicus-ems[geo]'"
    ) from exc

from copernicus_ems.client import ProductEvents
from copernicus_ems.errors import EmsError

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class ProductSummary:
    """The numbers of one product: what was looked at, and what was found."""

    activation_code: str
    product: str
    aoi_name: str
    observed_time: datetime | None
    features: int
    flooded_km2: float
    observed_km2: float
    bbox: tuple[float, float, float, float] | None

    @property
    def flooded_share(self) -> float:
        """Flooded fraction of the observed area (0 when nothing was observed)."""
        return self.flooded_km2 / self.observed_km2 if self.observed_km2 else 0.0


def mask(event: ProductEvents) -> BaseGeometry:
    """The observation mask (the AOI) of the event's product."""
    geometry = event.product.aoi_extent
    if geometry is None:
        raise EmsError(f"{event.product.label} has no AOI extent")
    return shape(geometry)


def flooded(event: ProductEvents) -> BaseGeometry:
    """The union of the event's features, clipped to the observation mask."""
    union = shapely.union_all([shape(f["geometry"]) for f in event.features])
    return shapely.intersection(shapely.make_valid(union), mask(event))


def observed_dry(event: ProductEvents) -> BaseGeometry:
    """Inside the AOI, outside every feature: where "not flooded" was observed."""
    return shapely.difference(mask(event), flooded(event))


def area_km2(geometry: BaseGeometry | dict[str, Any]) -> float:
    """Geodesic area of a lon/lat geometry (or GeoJSON geometry), in km²."""
    geom = shape(geometry) if isinstance(geometry, dict) else geometry
    if geom.is_empty:
        return 0.0
    area, _ = _GEOD.geometry_area_perimeter(geom)
    return abs(area) / 1e6


def coverage(footprint: dict[str, Any] | BaseGeometry, event: ProductEvents) -> float:
    """Share of the event's AOI inside ``footprint``, from 0 to 1.

    ``footprint`` is lon/lat: ``Scene.geometry``, or ``ScenePass.geometry``
    for every slice of one pass together.
    """
    aoi = mask(event)
    total = area_km2(aoi)
    if not total:
        return 0.0
    other = shape(footprint) if isinstance(footprint, dict) else footprint
    seen = shapely.union_all(shapely.get_parts(shapely.make_valid(other)))
    return min(area_km2(shapely.intersection(aoi, seen)) / total, 1.0)


def summarize(event: ProductEvents) -> ProductSummary:
    """The :class:`ProductSummary` of one product."""
    found = flooded(event)
    observed = event.product.observed_time
    return ProductSummary(
        activation_code=event.product.activation_code,
        product=event.product.label,
        aoi_name=event.product.aoi_name,
        observed_time=observed,
        features=len(event.features),
        flooded_km2=area_km2(found),
        observed_km2=area_km2(mask(event)),
        bbox=None if found.is_empty else tuple(found.bounds),  # type: ignore[arg-type]
    )


def summary(events: Iterable[ProductEvents]) -> list[ProductSummary]:
    """One :class:`ProductSummary` per product, in the order given."""
    return [summarize(e) for e in events]
