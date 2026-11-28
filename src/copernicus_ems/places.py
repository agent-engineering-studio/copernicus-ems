"""The water an activation observed over one place: a municipality, a district, any polygon.

Needs the ``geo`` extra (``pip install copernicus-ems[geo]``). The place is
the caller's: a GeoJSON geometry, Feature or FeatureCollection in lon/lat, or
a shapely geometry. The library knows no boundary by name.

For one place an activation answers three different things, and
:class:`PlaceWater` keeps them apart:

* **observed** (``observed_km2``): the part of the place inside the
  observation mask of a product. Only there "no water" means *seen dry*;
* **water** (``water_km2``): the part of the place inside an observed flood
  polygon. A lower bound: short urban floods and water under canopy may be
  missing;
* **not observed**: the rest of the place. Nobody looked: say "unknown",
  never "dry".

The water is the **union** of the first issues (``DEL_PRODUCT``,
``GRA_PRODUCT``, ``FEP_PRODUCT``): a monitoring product draws the same place
again on a later day. Those that cover the place are listed in
``later_products``, not added. Areas are geodesic, in km².

It is water observed afterwards, from satellite: never an alert, never a
forecast, never a hazard. :func:`place_water` is pure;
:func:`water_over_place` reads the copy saved by :func:`copernicus_ems.init`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import shapely
    from shapely.geometry import shape
    from shapely.geometry.base import BaseGeometry
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.places needs the geo extra: pip install 'copernicus-ems[geo]'"
    ) from exc

from copernicus_ems.catalogue import Activation, attribution
from copernicus_ems.client import ProductEvents, default_directory, layer_path
from copernicus_ems.errors import EmsError
from copernicus_ems.events import OBSERVED_EVENT, parse_layer, select_features
from copernicus_ems.geo import area_km2, flooded, mask
from copernicus_ems.ids import is_monitoring, product_id
from copernicus_ems.offline import read_activations, read_products
from copernicus_ems.reports import activation_reports

#: What a figure of this module is, to quote beside it.
CAVEAT = (
    "Water observed from satellite after the event (Copernicus EMS Rapid Mapping): "
    "a lower bound of what was flooded, not an alert and not a forecast."
)


@dataclass(frozen=True, slots=True)
class PlaceWater:
    """What one activation observed over one place."""

    activation_code: str
    name: str
    event_time: datetime
    place_km2: float
    #: The part of the place inside the mask of a first-issue product.
    observed_km2: float
    #: The part of the place inside an observed flood polygon of those products.
    water_km2: float
    observed_from: datetime | None
    observed_to: datetime | None
    #: First-issue products whose mask covers part of the place (product ids).
    products: tuple[str, ...]
    #: Those of them with water in the place.
    wet_products: tuple[str, ...]
    #: Monitoring products that cover the place: later views, not added to the water.
    later_products: tuple[str, ...] = ()
    #: The radar screening of each product, when a report is saved (product id to verdict).
    #: A screening: "to check" means "look again", never "wrong".
    verdicts: dict[str, str] = field(default_factory=dict)

    @property
    def observed_share(self) -> float:
        """The fraction of the place that was observed (0 to 1)."""
        return min(self.observed_km2 / self.place_km2, 1.0) if self.place_km2 else 0.0

    @property
    def water_share(self) -> float | None:
        """The fraction of the **observed** part found flooded; ``None`` if nothing was observed."""
        return self.water_km2 / self.observed_km2 if self.observed_km2 else None

    @property
    def attribution(self) -> str:
        """The sentence the CEMS licence asks for, with the year of the event."""
        return attribution(self.event_time.year)

    def as_dict(self) -> dict[str, Any]:
        """Plain values, for JSON: times as ISO strings, the shares and the attribution added."""
        out = asdict(self)
        for key in ("event_time", "observed_from", "observed_to"):
            out[key] = None if out[key] is None else out[key].isoformat()
        for key in ("products", "wet_products", "later_products"):
            out[key] = list(out[key])
        out["observed_share"] = self.observed_share
        out["water_share"] = self.water_share
        out["attribution"] = self.attribution
        out["caveat"] = CAVEAT
        return out


def place_geometry(place: BaseGeometry | Mapping[str, Any]) -> BaseGeometry:
    """A valid shapely geometry from a GeoJSON geometry, Feature or FeatureCollection (lon/lat).

    A place with no area (a point, a line, nothing) is an :class:`EmsError`.
    """
    if isinstance(place, BaseGeometry):
        geom = place
    else:
        try:
            kind = place.get("type")
            if kind == "FeatureCollection":
                geom = shapely.union_all([shape(f["geometry"]) for f in place["features"]])
            elif kind == "Feature":
                geom = shape(place["geometry"])
            else:
                geom = shape(dict(place))
        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
            shapely.errors.ShapelyError,
        ) as exc:
            raise EmsError(f"the place is not a GeoJSON geometry: {exc}") from exc
    geom = shapely.make_valid(geom)
    if geom.is_empty or geom.area <= 0:
        raise EmsError("the place has no area: give a polygon")
    return geom


def place_water(
    place: BaseGeometry | Mapping[str, Any],
    activation: Activation,
    events: Iterable[ProductEvents],
    *,
    verdicts: Mapping[str, str] | None = None,
) -> PlaceWater | None:
    """What ``events`` (the products of ``activation``) observed over ``place``.

    ``None`` when no first-issue product covers any part of the place: the
    activation did not look there, which is not "no water". ``verdicts`` maps
    a product id to its radar verdict; only those of the products used are kept.
    """
    geom = place_geometry(place)
    masks: list[BaseGeometry] = []
    water: list[BaseGeometry] = []
    used: list[str] = []
    wet: list[str] = []
    later: list[str] = []
    times: list[datetime] = []
    for event in events:
        key = product_id(event.product)
        try:
            seen = shapely.intersection(geom, shapely.make_valid(mask(event)))
        except EmsError:
            continue  # no AOI extent: where it looked is not known
        if seen.is_empty or seen.area <= 0:
            continue
        if is_monitoring(key):
            later.append(key)
            continue
        used.append(key)
        masks.append(seen)
        if event.product.observed_time is not None:
            times.append(event.product.observed_time)
        found = shapely.intersection(geom, flooded(event))
        if not found.is_empty and found.area > 0:
            wet.append(key)
            water.append(found)
    if not used:
        return None
    known = verdicts or {}
    return PlaceWater(
        activation_code=activation.code,
        name=activation.name,
        event_time=activation.event_time,
        place_km2=area_km2(geom),
        observed_km2=area_km2(shapely.union_all(masks)),
        water_km2=area_km2(shapely.union_all(water)) if water else 0.0,
        observed_from=min(times, default=None),
        observed_to=max(times, default=None),
        products=tuple(sorted(used)),
        wet_products=tuple(sorted(wet)),
        later_products=tuple(sorted(later)),
        verdicts={k: known[k] for k in sorted(used) if k in known},
    )


def saved_events(
    code: str, directory: Path | None = None, *, over: BaseGeometry | None = None
) -> list[ProductEvents]:
    """The observed flood layers of an activation that :func:`copernicus_ems.init` saved.

    A product whose layer is not on disk is left out: nothing is downloaded.
    With ``over``, only the products whose mask touches it are read: a layer
    can be tens of megabytes, and most activations are elsewhere.
    """
    where = directory or default_directory()
    out: list[ProductEvents] = []
    for product in read_products(code, where):
        layer = product.layers.get(OBSERVED_EVENT)
        if layer is None:
            continue
        extent = product.aoi_extent
        if over is not None and (extent is None or not shape(extent).intersects(over)):
            continue
        path = layer_path(product, layer, where)
        if path.is_file():
            out.append(ProductEvents(product, select_features(parse_layer(path.read_bytes()))))
    return out


def saved_verdicts(code: str, directory: Path | None = None) -> dict[str, str]:
    """Product id to radar verdict, from the quality files saved for an activation."""
    scan = activation_reports(directory or default_directory(), code, classes=())
    return {r.product_id: r.quality.verdict for r in scan.reports if r.quality is not None}


def water_over_place(
    place: BaseGeometry | Mapping[str, Any],
    directory: Path | None = None,
    *,
    codes: Sequence[str] | None = None,
    country: str | None = None,
) -> list[PlaceWater]:
    """Every saved activation that observed ``place``, the newest first.

    Reads the copy of :func:`copernicus_ems.init` under ``directory`` and
    asks nothing to the network. ``codes`` limits the activations; ``country``
    filters the list as :func:`copernicus_ems.select_activations` does. An
    activation whose detail is not saved is left out. Raises
    ``FileNotFoundError`` if ``init`` has not run.
    """
    geom = place_geometry(place)
    wanted = {c.upper() for c in codes} if codes is not None else None
    out: list[PlaceWater] = []
    for activation in read_activations(directory, country=country):
        if wanted is not None and activation.code not in wanted:
            continue
        try:
            events = saved_events(activation.code, directory, over=geom)
        except (FileNotFoundError, EmsError):
            continue
        if not events:
            continue
        found = place_water(
            geom, activation, events, verdicts=saved_verdicts(activation.code, directory)
        )
        if found is not None:
            out.append(found)
    return sorted(out, key=lambda r: r.event_time, reverse=True)
