"""Async functions over :mod:`copernicus_ems.catalogue` and :mod:`copernicus_ems.events`.

They take your ``httpx.AsyncClient``: timeouts, retries and pooling stay
yours, and HTTP errors propagate. :class:`RapidMapping` wraps them for
callers who would rather not bring one: it opens (and closes) its own,
which retries network errors and 429/502/503/504 (the dashboard API
answers 502 now and then).

Layers are saved as ``.geojson`` in a directory, which is also the cache:
a layer's URL carries its version (``_v1``), so a file already there is the
same layer and is read from disk, not downloaded again. The activation list
and details are always asked: new monitoring products appear in them. Files
are written to ``.part`` and renamed, so a file that exists is whole.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

from copernicus_ems.catalogue import (
    Activation,
    Layer,
    Product,
    activations_url,
    detail_url,
    parse_activations,
    parse_detail,
    select_activations,
)
from copernicus_ems.events import (
    FLOOD_EVENT_TYPE,
    FLOOD_NOTATIONS,
    OBSERVED_EVENT,
    parse_layer,
    select_features,
)
from copernicus_ems.scenes import Scene, SceneMatch, fetch_scenes, find_scenes

_RETRY_STATUS = frozenset({429, 502, 503, 504})

#: The environment variable :func:`default_directory` reads.
ENV_DATA_DIR = "COPERNICUS_EMS_DATA_DIR"


def default_directory() -> Path:
    """Where layers are saved when no directory is given.

    ``COPERNICUS_EMS_DATA_DIR`` if set, else ``copernicus-ems`` in the user
    cache (``$XDG_CACHE_HOME``, or ``~/.cache``).
    """
    if configured := os.environ.get(ENV_DATA_DIR, "").strip():
        return Path(configured).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME", "").strip() or "~/.cache"
    return Path(cache).expanduser() / "copernicus-ems"


def layer_path(product: Product, layer: Layer, directory: Path) -> Path:
    """``directory/EMSR762/EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.geojson``."""
    name = layer.url.rsplit("/", 1)[-1].split("?", 1)[0].removesuffix(".json")
    return directory / product.activation_code / f"{name}.geojson"


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
    client: httpx.AsyncClient,
    product: Product,
    kind: str,
    *,
    directory: Path | None = None,
) -> dict[str, Any] | None:
    """One vector layer of a product as GeoJSON; ``None`` if the product has none of that kind.

    The layer is saved under ``directory`` (default :func:`default_directory`)
    and read from there on the next call.
    """
    layer = product.layers.get(kind)
    if layer is None:
        return None
    path = layer_path(product, layer, directory or default_directory())
    if path.is_file():
        return parse_layer(path.read_bytes())
    response = await client.get(layer.url, follow_redirects=True)
    response.raise_for_status()
    collection = parse_layer(response.content)  # nothing invalid reaches the cache
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    partial.write_bytes(response.content)
    partial.replace(path)
    return collection


async def fetch_observed_events(
    client: httpx.AsyncClient,
    code: str,
    *,
    event_types: Iterable[str] | None = (FLOOD_EVENT_TYPE,),
    notations: Iterable[str] | None = FLOOD_NOTATIONS,
    directory: Path | None = None,
    concurrency: int = 4,
) -> list[ProductEvents]:
    """The observed events of every product of an activation (floods by default).

    Products without an ``observedEventA`` layer, and products where no
    feature passes the filter, are left out. Layers are cached as in
    :func:`fetch_layer`, and downloaded ``concurrency`` at a time; the
    result keeps the order of the products.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    types = tuple(event_types) if event_types is not None else None
    marks = tuple(notations) if notations is not None else None
    gate = asyncio.Semaphore(concurrency)

    async def one(product: Product) -> ProductEvents | None:
        async with gate:
            collection = await fetch_layer(client, product, OBSERVED_EVENT, directory=directory)
        if collection is None:
            return None
        features = select_features(collection, event_types=types, notations=marks)
        return ProductEvents(product=product, features=features) if features else None

    products = await fetch_products(client, code)
    found = await asyncio.gather(*(one(p) for p in products))
    return [events for events in found if events is not None]


class RetryTransport(httpx.AsyncBaseTransport):
    """Retry network errors and 429/502/503/504, waiting ``backoff_s``, doubled each time."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        retries: int = 3,
        backoff_s: float = 2.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._transport = transport or httpx.AsyncHTTPTransport()
        self._retries = retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self._retries + 1):
            last = attempt == self._retries
            try:
                response = await self._transport.handle_async_request(request)
            except httpx.TransportError:
                if last:
                    raise
            else:
                if response.status_code not in _RETRY_STATUS or last:
                    return response
                await response.aclose()
            await self._sleep(self._backoff_s * 2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    async def aclose(self) -> None:
        await self._transport.aclose()


#: ``async (request) -> response``: sends one request in the caller's own way.
Send = Callable[[httpx.Request], Awaitable[httpx.Response]]


class SendTransport(httpx.AsyncBaseTransport):
    """Hands every request to ``send`` (the caller's retry, rate limit, shared client).

    No retry of its own: with ``send``, retrying is the caller's job.
    """

    def __init__(self, send: Send) -> None:
        self._send = send

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._send(request)
        await response.aread()  # the bytes, whatever stream the caller's client used
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
            extensions={k: v for k, v in response.extensions.items() if k != "network_stream"},
        )


def open_client(
    *, timeout: float, retries: int = 3, send: Send | None = None, follow_redirects: bool = True
) -> httpx.AsyncClient:
    """An HTTP client that retries (:class:`RetryTransport`), or sends through ``send``."""
    transport: httpx.AsyncBaseTransport = (
        SendTransport(send) if send is not None else RetryTransport(retries=retries)
    )
    return httpx.AsyncClient(
        timeout=timeout, follow_redirects=follow_redirects, transport=transport
    )


class RapidMapping:
    """The functions above, with a client and a directory of their own.

    Give it no ``client`` and it opens one that retries (see
    :class:`RetryTransport`); use it as an async context manager, or call
    :meth:`aclose`, to close it. ``directory`` defaults to
    :func:`default_directory`, read when a layer is fetched.

    ``send``, an ``async (httpx.Request) -> httpx.Response`` callable, routes
    every request (Rapid Mapping, layers, STAC search) through the caller
    instead, e.g. an application's shared client with its own retry policy;
    no :class:`RetryTransport` is installed then.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        directory: Path | None = None,
        retries: int = 3,
        send: Send | None = None,
    ) -> None:
        if client is not None and send is not None:
            raise ValueError("give client or send, not both")
        self._own = client is None
        self._client = client or open_client(timeout=60, retries=retries, send=send)
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory or default_directory()

    async def __aenter__(self) -> RapidMapping:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if this instance opened it."""
        if self._own:
            await self._client.aclose()

    async def activations(
        self,
        *,
        country: str | None = None,
        categories: Iterable[str] | None = None,
        since: date | datetime | None = None,
        until: date | datetime | None = None,
    ) -> list[Activation]:
        """Public activations, oldest first, filtered as :func:`select_activations` does."""
        found = await fetch_activations(self._client)
        return select_activations(
            found, country=country, categories=categories, since=since, until=until
        )

    async def products(self, code: str) -> list[Product]:
        """The products of one activation (``EMSR762``)."""
        return await fetch_products(self._client, code)

    async def scenes(
        self,
        product: Product,
        *,
        collections: Iterable[str] = ("sentinel-1-grd",),
        within: timedelta = timedelta(days=3),
        max_cloud_cover: float | None = None,
    ) -> list[SceneMatch]:
        """Open Sentinel scenes near the product, nearest first: see :func:`find_scenes`."""
        return await find_scenes(
            self._client,
            product,
            collections=collections,
            within=within,
            max_cloud_cover=max_cloud_cover,
        )

    async def search_scenes(
        self,
        geometry: dict[str, Any],
        start: datetime,
        end: datetime,
        *,
        collections: Iterable[str] = ("sentinel-1-grd",),
    ) -> list[Scene]:
        """Open scenes over ``geometry`` from ``start`` to ``end``: see :func:`fetch_scenes`."""
        return await fetch_scenes(self._client, geometry, start, end, collections=collections)

    async def layer(self, product: Product, kind: str) -> dict[str, Any] | None:
        """One vector layer as GeoJSON, cached; ``None`` if the product has none of that kind."""
        return await fetch_layer(self._client, product, kind, directory=self.directory)

    async def observed_events(
        self,
        code: str,
        *,
        event_types: Iterable[str] | None = (FLOOD_EVENT_TYPE,),
        notations: Iterable[str] | None = FLOOD_NOTATIONS,
        concurrency: int = 4,
    ) -> list[ProductEvents]:
        """The observed events of every product of an activation (floods by default)."""
        return await fetch_observed_events(
            self._client,
            code,
            event_types=event_types,
            notations=notations,
            directory=self.directory,
            concurrency=concurrency,
        )
