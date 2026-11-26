"""Download once what working offline needs, and read it back without the network.

:func:`init` is the step a server or an installer runs before its first
request. Into the data directory (:func:`~copernicus_ems.default_directory`)
it writes::

    activations.json                  every public activation (the whole list)
    EMSR762/detail.json               the detail of each selected activation
    EMSR762/EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.geojson
                                      its layers (``observedEventA`` by default)

The layers are where :func:`~copernicus_ems.fetch_layer` and
:class:`~copernicus_ems.RapidMapping` look first, so they are read from disk
afterwards; :func:`read_activations` and :func:`read_products` read the list
and the details. The Sentinel-1 backscatter (``EMSR762/sentinel-1/``) is not
downloaded here: it needs a CDSE account and costs processing units.

A layer's URL carries its version, so a layer already on disk and valid is
skipped. The list and the details change (new activations, new monitoring
products): they are asked every time, and rewritten only when different.
Every file goes to a ``.part`` file and is renamed, so a file that exists is
whole. A file that fails is recorded in the report and the rest goes on (run
it again to resume); a disk that cannot be written raises.

``client`` is used for every request and left open. ``retries`` is how many
times a network error or a 429/502/503/504 is retried; ``retries=0`` leaves
the retrying to the caller.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from copernicus_ems.catalogue import (
    Activation,
    Product,
    activations_url,
    detail_url,
    parse_activations,
    parse_detail,
    select_activations,
)
from copernicus_ems.client import _RETRY_STATUS, default_directory, layer_path
from copernicus_ems.errors import EmsError
from copernicus_ems.events import OBSERVED_EVENT, parse_layer

#: The activation list, at the top of the data directory.
ACTIVATIONS_FILE = "activations.json"
#: The detail of an activation, in the activation's folder.
DETAIL_FILE = "detail.json"
#: Many floods are filed as ``Storm``: the same default as ``copernicus-ems export``.
INIT_CATEGORIES = ("Flood", "Storm")


@dataclass(slots=True)
class InitReport:
    """What :func:`init` did, file by file (paths relative to ``directory``)."""

    directory: str
    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    #: Bytes written to disk by this run.
    bytes: int = 0

    @property
    def ok(self) -> bool:
        """No file failed."""
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return {**asdict(self), "ok": self.ok}


def activations_path(directory: Path | None = None) -> Path:
    """``<data directory>/activations.json``."""
    return (directory or default_directory()) / ACTIVATIONS_FILE


def detail_path(code: str, directory: Path | None = None) -> Path:
    """``<data directory>/EMSR762/detail.json``."""
    return (directory or default_directory()) / code / DETAIL_FILE


def read_activations(
    directory: Path | None = None,
    *,
    country: str | None = None,
    categories: Iterable[str] | None = None,
    since: date | datetime | None = None,
    until: date | datetime | None = None,
) -> list[Activation]:
    """The activation list :func:`init` saved, filtered as :func:`select_activations` does.

    Raises ``FileNotFoundError`` if :func:`init` has not run.
    """
    page = parse_activations(activations_path(directory).read_bytes())
    return select_activations(
        page.activations, country=country, categories=categories, since=since, until=until
    )


def read_products(code: str, directory: Path | None = None) -> list[Product]:
    """The products of one activation, from the detail :func:`init` saved."""
    return parse_detail(detail_path(code, directory).read_bytes())


async def init(
    directory: Path | None = None,
    *,
    country: str | None = "Italy",
    categories: Iterable[str] | None = INIT_CATEGORIES,
    since: date | datetime | None = None,
    until: date | datetime | None = None,
    kinds: Iterable[str] = (OBSERVED_EVENT,),
    client: httpx.AsyncClient | None = None,
    retries: int = 3,
    concurrency: int = 4,
    backoff_s: float = 2.0,
) -> InitReport:
    """The activation list, and the details and layers of the selected activations.

    Activations are selected by ``country`` (``None``: every country),
    ``categories`` (``None``: all), ``since`` and ``until``, as in
    :func:`~copernicus_ems.select_activations`; ``kinds`` are the layers kept
    of each product. Layers are downloaded ``concurrency`` at a time. See the
    module for what is skipped, retried and raised.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if retries < 0:
        raise ValueError("retries must be at least 0")
    folder = directory or default_directory()
    report = InitReport(directory=str(folder))
    own = client is None
    http = client or httpx.AsyncClient(timeout=120, follow_redirects=True)
    fetcher = _Fetcher(http, folder, report, retries=retries, backoff_s=backoff_s)
    try:
        activations = await fetcher.activations()
        if activations is None:
            return report
        selected = select_activations(
            activations, country=country, categories=categories, since=since, until=until
        )
        wanted = tuple(kinds)
        gate = asyncio.Semaphore(concurrency)

        async def layer(product: Product, kind: str) -> None:
            async with gate:
                await fetcher.layer(product, kind)

        for activation in selected:
            products = await fetcher.detail(activation.code)
            await asyncio.gather(
                *(layer(p, k) for p in products or [] for k in wanted if k in p.layers)
            )
    finally:
        if own:
            await http.aclose()
    return report


class _Fetcher:
    """The requests of one :func:`init` run, and what they did to its report."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        folder: Path,
        report: InitReport,
        *,
        retries: int,
        backoff_s: float,
    ) -> None:
        self._client = client
        self._folder = folder
        self._report = report
        self._retries = retries
        self._backoff_s = backoff_s

    async def activations(self) -> list[Activation] | None:
        """Every page of the list, saved as one; the copy on disk if the service fails."""
        path = activations_path(self._folder)
        rows: list[Any] = []
        out: list[Activation] = []
        url: str | None = activations_url()
        try:
            while url:
                body = await self._get(url)
                page = parse_activations(body)  # nothing invalid reaches the disk
                rows.extend(json.loads(body)["results"] or [])
                out.extend(page.activations)
                url = page.next_url
        except (httpx.HTTPError, EmsError) as error:
            self._fail(path, error)
            return self._saved(path, lambda b: parse_activations(b).activations)
        merged = {"count": len(rows), "next": None, "previous": None, "results": rows}
        self._write(path, json.dumps(merged, ensure_ascii=False).encode())
        return out

    async def detail(self, code: str) -> list[Product] | None:
        """The products of an activation, saved; the copy on disk if the service fails."""
        path = detail_path(code, self._folder)
        try:
            body = await self._get(detail_url(code))
            products = parse_detail(body)
        except (httpx.HTTPError, EmsError) as error:
            self._fail(path, error)
            return self._saved(path, parse_detail)
        self._write(path, body)
        return products

    async def layer(self, product: Product, kind: str) -> None:
        """One layer, unless a valid copy is already there (its URL carries its version)."""
        layer = product.layers[kind]
        path = layer_path(product, layer, self._folder)
        if self._saved(path, parse_layer) is not None:
            self._report.skipped.append(self._name(path))
            return
        try:
            body = await self._get(layer.url)
            parse_layer(body)
        except (httpx.HTTPError, EmsError) as error:
            self._fail(path, error)
            return
        self._write(path, body)

    async def _get(self, url: str) -> bytes:
        for attempt in range(self._retries + 1):
            last = attempt == self._retries
            try:
                response = await self._client.get(url, follow_redirects=True)
            except httpx.TransportError:
                if last:
                    raise
            else:
                if response.status_code not in _RETRY_STATUS or last:
                    response.raise_for_status()
                    return response.content
            await asyncio.sleep(self._backoff_s * 2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    def _saved[T](self, path: Path, parse: Callable[[bytes], T]) -> T | None:
        """What ``parse`` makes of the file on disk; ``None`` if missing or invalid."""
        try:
            return parse(path.read_bytes())
        except (OSError, EmsError):
            return None

    def _write(self, path: Path, body: bytes) -> None:
        """Write ``body`` through a ``.part`` file; skipped if the same bytes are there."""
        name = self._name(path)
        if path.is_file() and path.read_bytes() == body:
            self._report.skipped.append(name)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.part")
        try:
            partial.write_bytes(body)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
        self._report.downloaded.append(name)
        self._report.bytes += len(body)

    def _fail(self, path: Path, error: Exception) -> None:
        self._report.failed[self._name(path)] = str(error) or type(error).__name__

    def _name(self, path: Path) -> str:
        return path.relative_to(self._folder).as_posix()
