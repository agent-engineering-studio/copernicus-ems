"""The truth set of one activation: observed flood polygons and observation masks.

Needs the ``geo`` extra (``pip install copernicus-ems[geo]``: shapely and pyproj).

A mask says "the satellite looked here": inside it, no polygon means
*observed not flooded*. So a mask is given **only for the products whose
observed layer was read**. With the layer lost (403, 5xx, unreadable body) a
mask would call dry a place nobody knows about, and a score would count false
alarms where the data is missing.

Three kinds of product, kept apart in :class:`ActivationTruth`:

* layer read: its polygons and its mask are in ``floods`` and ``masks``, its
  id in ``read_products``;
* never delivered (the detail lists no layer at all): its id is in
  ``absent_masks`` and in ``read_products``: what a store holds of it can go;
* layer listed but not read, or delivered without an observed layer: neither.
  Nothing is known now, and what a store holds stays as it is.

:func:`activation_truth` is pure (products and layers in, records out);
:func:`fetch_activation_truth` asks the service with your ``httpx.AsyncClient``.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

try:
    import numpy as np  # shapely's own dependency
    import shapely
    from pyproj import Transformer
    from shapely import wkt
    from shapely.geometry import MultiPolygon, shape
    from shapely.geometry.base import BaseGeometry
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.truth needs the geo extra: pip install 'copernicus-ems[geo]'"
    ) from exc

from copernicus_ems.catalogue import Activation, Product, detail_url, parse_detail
from copernicus_ems.client import default_directory, fetch_layer, layer_path
from copernicus_ems.errors import EmsError
from copernicus_ems.events import FLOOD_EVENT_TYPE, FLOOD_NOTATIONS, OBSERVED_EVENT
from copernicus_ems.geo import area_km2
from copernicus_ems.ids import delivered_product_ids, polygon_id, product_id

#: The key of :attr:`ActivationTruth.errors` for an activation detail that did not answer.
DETAIL = "detail"


@dataclass(frozen=True, slots=True)
class ObservedFlood:
    """One polygon of observed flood (lon/lat), with its two times."""

    id: str
    activation_code: str
    aoi_label: str
    event_time: datetime
    geom: BaseGeometry
    area_km2: float
    observed_time: datetime | None = None
    event_type: str | None = None
    detection_method: str | None = None
    attributes: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ObservationMask:
    """Where the satellite looked (lon/lat), and when."""

    id: str
    activation_code: str
    aoi_label: str
    product_type: str
    observed_time: datetime
    geom: BaseGeometry
    area_km2: float | None = None


@dataclass(frozen=True, slots=True)
class ActivationTruth:
    """Flood polygons and masks of one activation, from the same detail."""

    floods: list[ObservedFlood]
    masks: list[ObservationMask]
    #: Products the detail lists **with no layer at all** (never delivered).
    absent_masks: list[str] = field(default_factory=list)
    #: Products everything is known of now: layer read, or never delivered.
    #: Only for these a polygon that is gone was removed at the source.
    read_products: list[str] = field(default_factory=list)
    #: Products delivered without an observed layer (orthophotos alone): it may
    #: mean "no water seen" or not. Neither written nor removed.
    delivered_without_event: list[str] = field(default_factory=list)
    #: What could not be read, and why: product id (or :data:`DETAIL`) to error.
    errors: dict[str, str] = field(default_factory=dict)


def observation_mask(product: Product) -> ObservationMask | None:
    """The mask of a product; ``None`` without an extent, an image, or a readable WKT."""
    if product.aoi_extent_wkt is None or product.observed_time is None:
        return None
    try:
        geom = wkt.loads(product.aoi_extent_wkt)
    except Exception:
        return None
    return ObservationMask(
        id=product_id(product),
        activation_code=product.activation_code,
        aoi_label=f"AOI{product.aoi_number:02d}",
        product_type=product.product_type,
        observed_time=product.observed_time,
        geom=geom,
    )


def area_measure(area_crs: int | str | None = None) -> Callable[[BaseGeometry], float]:
    """A function giving the km² of a lon/lat geometry.

    Geodesic on the WGS84 ellipsoid when ``area_crs`` is ``None`` (it holds
    anywhere); else the planar area in that CRS (``3035`` for Europe). Build
    it once per loop: making the transformer costs far more than using it.
    """
    if area_crs is None:
        return area_km2
    transformer = Transformer.from_crs(4326, area_crs, always_xy=True)

    def project(lonlat: Any) -> Any:
        return np.column_stack(transformer.transform(lonlat[:, 0], lonlat[:, 1]))

    return lambda geom: float(shapely.transform(geom, project).area) / 1e6


def _parts(geom: BaseGeometry) -> Iterator[BaseGeometry]:
    if isinstance(geom, MultiPolygon):
        yield from geom.geoms
    else:
        yield geom


def observed_floods(
    activation: Activation,
    product: Product,
    collection: Mapping[str, Any],
    *,
    measure: Callable[[BaseGeometry], float] = area_km2,
) -> list[ObservedFlood]:
    """The flood polygons of one observed layer, one record per polygon part.

    The area is of the part, not of the feature: a MultiPolygon becomes
    several records, and the area of the group on each would count the same
    flood several times in a total.
    """
    layer = product.layers.get(OBSERVED_EVENT)
    source = layer.url.rsplit("/", 1)[-1] if layer is not None else None
    out: list[ObservedFlood] = []
    for position, feature in enumerate(collection["features"]):
        props = feature.get("properties") or {}
        if props.get("event_type") != FLOOD_EVENT_TYPE:
            continue
        notation = str(props.get("notation") or "")
        if notation not in FLOOD_NOTATIONS or feature.get("geometry") is None:
            continue
        geom = shape(feature["geometry"])
        if geom.is_empty:
            continue
        for part_index, part in enumerate(_parts(geom)):
            out.append(
                ObservedFlood(
                    id=polygon_id(product, position, part_index),
                    activation_code=activation.code,
                    aoi_label=f"AOI{product.aoi_number:02d}",
                    event_time=activation.event_time,
                    observed_time=product.observed_time,
                    event_type=str(props.get("obj_desc") or "") or None,
                    detection_method=str(props.get("det_method") or "") or None,
                    area_km2=measure(part),
                    geom=part,
                    attributes={
                        "notation": notation,
                        "activation_name": activation.name,
                        "category": activation.category,
                        "sub_category": activation.sub_category,
                        "product": source,
                    },
                )
            )
    return out


def activation_truth(
    activation: Activation,
    products: Collection[Product],
    layers: Mapping[str, Mapping[str, Any] | None],
    *,
    delivered: Collection[str] = (),
    area_crs: int | str | None = None,
    errors: Mapping[str, str] | None = None,
) -> ActivationTruth:
    """The truth of an activation from what was read. No network, no files.

    ``layers`` maps a product id (:func:`copernicus_ems.ids.product_id`) to
    its observed layer as GeoJSON; a product missing from it, or mapped to
    ``None``, was not read. ``delivered`` is
    :func:`copernicus_ems.ids.delivered_product_ids` of the detail.
    """
    measure = area_measure(area_crs)
    floods: list[ObservedFlood] = []
    masks: list[ObservationMask] = []
    absent: list[str] = []
    read: list[str] = []
    without_event: list[str] = []
    for product in products:
        key = product_id(product)
        if OBSERVED_EVENT not in product.layers:
            (without_event if key in delivered else absent).append(key)
            continue
        collection = layers.get(key)
        if collection is None:
            continue
        read.append(key)
        mask = observation_mask(product)
        if mask is not None:
            masks.append(mask)
        floods.extend(observed_floods(activation, product, collection, measure=measure))
    return ActivationTruth(
        floods=floods,
        masks=masks,
        absent_masks=absent,
        read_products=[*read, *absent],
        delivered_without_event=without_event,
        errors=dict(errors or {}),
    )


async def _layer(
    client: httpx.AsyncClient, product: Product, directory: Path
) -> tuple[dict[str, Any] | None, str | None]:
    """The observed layer, from the cache or the network; ``(None, error)`` if unreadable.

    A damaged cache file is removed and downloaded again; a layer that cannot
    be read does not stop the other products.
    """
    path = layer_path(product, product.layers[OBSERVED_EVENT], directory)
    cached = path.is_file()
    try:
        return await fetch_layer(client, product, OBSERVED_EVENT, directory=directory), None
    except EmsError as exc:
        if not cached:
            return None, str(exc)[:200]
        path.unlink()
    except Exception as exc:
        return None, str(exc)[:200]
    try:
        return await fetch_layer(client, product, OBSERVED_EVENT, directory=directory), None
    except Exception as exc:
        return None, str(exc)[:200]


async def fetch_activation_truth(
    client: httpx.AsyncClient,
    activation: Activation,
    *,
    directory: Path | None = None,
    area_crs: int | str | None = None,
) -> ActivationTruth:
    """The flood polygons and the observation masks of an activation.

    The detail is asked once; if it does not answer the truth is empty and
    ``errors`` says why under :data:`DETAIL`. A layer that cannot be read is
    in ``errors`` under its product id, and the other products go on. Layers
    are cached under ``directory`` as in :func:`copernicus_ems.fetch_layer`.
    """
    try:
        response = await client.get(detail_url(activation.code))
        response.raise_for_status()
        products = parse_detail(response.content)
        delivered = delivered_product_ids(response.content)
    except Exception as exc:
        return ActivationTruth(floods=[], masks=[], errors={DETAIL: str(exc)[:200]})
    where = directory or default_directory()
    layers: dict[str, Mapping[str, Any] | None] = {}
    errors: dict[str, str] = {}
    for product in products:
        if OBSERVED_EVENT not in product.layers:
            continue
        collection, error = await _layer(client, product, where)
        layers[product_id(product)] = collection
        if error is not None:
            errors[product_id(product)] = error
    return activation_truth(
        activation, products, layers, delivered=delivered, area_crs=area_crs, errors=errors
    )
