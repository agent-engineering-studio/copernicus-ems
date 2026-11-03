"""Async functions over :mod:`copernicus_ems.catalogue` and :mod:`copernicus_ems.events`.

They take your ``httpx.AsyncClient``: timeouts, retries and pooling stay
yours, and HTTP errors propagate.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from copernicus_ems.catalogue import (
    Activation,
    Product,
    activations_url,
    detail_url,
    parse_activations,
    parse_detail,
)
from copernicus_ems.events import (
    FLOOD_EVENT_TYPE,
    FLOOD_NOTATIONS,
    OBSERVED_EVENT,
    parse_layer,
    select_features,
)

if TYPE_CHECKING:
    import httpx


@dataclass(frozen=True, slots=True)
class ProductEvents:
    """The selected features of one product's layer, with the product they come from."""

    product: Product
    features: list[dict[str, Any]]


async def fetch_activations(client: httpx.AsyncClient, *, page_size: int = 500) -> list[Activation]:
    """Every public activation, following the pages."""
    out: list[Activation] = []
    url: str | None = activations_url(page_size=page_size)
    while url:
        response = await client.get(url)
        response.raise_for_status()
        page = parse_activations(response.content)
        out.extend(page.activations)
        url = page.next_url
    return out


async def fetch_products(client: httpx.AsyncClient, code: str) -> list[Product]:
    """The products of one activation (``EMSR762``)."""
    response = await client.get(detail_url(code))
    response.raise_for_status()
    return parse_detail(response.content)


async def fetch_layer(
    client: httpx.AsyncClient, product: Product, kind: str
) -> dict[str, Any] | None:
    """One vector layer of a product as GeoJSON; ``None`` if the product has none of that kind."""
    layer = product.layers.get(kind)
    if layer is None:
        return None
    response = await client.get(layer.url, follow_redirects=True)
    response.raise_for_status()
    return parse_layer(response.content)


async def fetch_observed_events(
    client: httpx.AsyncClient,
    code: str,
    *,
    event_types: Iterable[str] | None = (FLOOD_EVENT_TYPE,),
    notations: Iterable[str] | None = FLOOD_NOTATIONS,
) -> list[ProductEvents]:
    """The observed events of every product of an activation (floods by default).

    Products without an ``observedEventA`` layer, and products where no
    feature passes the filter, are left out.
    """
    types = tuple(event_types) if event_types is not None else None
    marks = tuple(notations) if notations is not None else None
    out: list[ProductEvents] = []
    for product in await fetch_products(client, code):
        collection = await fetch_layer(client, product, OBSERVED_EVENT)
        if collection is None:
            continue
        features = select_features(collection, event_types=types, notations=marks)
        if features:
            out.append(ProductEvents(product=product, features=features))
    return out
