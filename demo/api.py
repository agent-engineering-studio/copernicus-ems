"""The API the demo page asks: copernicus-ems behind a few URLs (Starlette).

    uvicorn --app-dir demo api:app --port 8000

Everything is read from the data directory first (``$DATA_ROOT/copernicus-ems``,
filled by ``copernicus-ems init``), and from Rapid Mapping when it is not
there yet; a layer read live is saved, so the next visit finds it on disk.

    GET /api/health                       {"status": "ok", ...}
    GET /api/activations                  the activations of the country, with a point each
    GET /api/activation/{code}            what one activation saw, product by product
    GET /api/activation/{code}/map        its observation masks and observed water, as GeoJSON
    GET /api/activation/{code}/radar      what the radar checks saved beside its pairs say
    GET /api/activation/{code}/radar/map  their agreement polygons (?product=P for one)
    GET /api/activation/{code}/description  the file ``copernicus-ems describe`` wrote

The radar files (``copernicus-ems quality`` and ``radar``) and the description are
only read, never written: the demo never calls a language model.

The numbers of an activation are those of :func:`copernicus_ems.geo.summarize`,
which joins the polygons of each product: on the largest activations (EMSR664,
1.7 million vertices) that takes half a minute. So it is done once, in a
thread, and kept: in memory, and in ``<data directory>/demo/`` under a key
made of the layers' versions, so a new monitoring product is seen. At start,
every activation ``init`` saved is prepared in the background.

``COPERNICUS_EMS_COUNTRY`` (default ``Italy``) is the country the demo shows.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from copernicus_ems import (
    OBSERVED_EVENT,
    Activation,
    EmsError,
    Product,
    ProductEvents,
    RapidMapping,
    __version__,
    activations_path,
    default_directory,
    detail_path,
    read_activations,
    read_products,
    select_features,
)
from copernicus_ems.geo import area_km2, flooded, mask
from copernicus_ems.offline import INIT_CATEGORIES

log = logging.getLogger("copernicus-ems-demo")

COUNTRY = os.environ.get("COPERNICUS_EMS_COUNTRY", "").strip() or "Italy"
#: How long the list of activations is kept before it is asked again.
LIST_TTL_S = 3600.0
#: About 10 m: invisible at the zoom of an area, a third of the bytes.
SIMPLIFY_DEG = 0.0001
#: Bump when what :func:`build` writes changes: the files of the old one are ignored.
FORMAT = 1
_CODE = re.compile(r"^EMSR\d{1,5}$")


def python_for(code: str) -> str:
    """The lines of Python that give the numbers of the card."""
    return (
        "from copernicus_ems import RapidMapping\n"
        "from copernicus_ems.geo import summary\n"
        "\n"
        "async with RapidMapping() as ems:\n"
        f'    events = await ems.observed_events("{code}")\n'
        "for s in summary(events):\n"
        '    print(s.product, f"{s.flooded_km2:.1f} km² of {s.observed_km2:.0f} km² observed")'
    )


@dataclass(frozen=True)
class Built:
    """What the page shows of one activation."""

    card: dict[str, Any]
    map: dict[str, Any]


def build(activation: Activation, products: list[Product], events: list[ProductEvents]) -> Built:
    """The card and the map of an activation: slow (the joins), and without I/O."""
    rows: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    for event in events:
        product = event.product
        if product.aoi_extent is None:
            continue
        looked = mask(event)
        water = flooded(event) if event.features else shapely.Polygon()
        tags = {
            "product": product.label,
            "aoi": product.aoi_name,
            "observed_time": _iso(product.observed_time),
        }
        rows.append(
            {
                **tags,
                "type": product.product_type,
                "sensors": sorted({i.sensor_name for i in product.images if i.sensor_name}),
                "features": len(event.features),
                "flooded_km2": round(area_km2(water), 2),
                "observed_km2": round(area_km2(looked), 1),
            }
        )
        features.append(_feature(looked, {"kind": "mask", **tags}))
        if not water.is_empty:
            km2 = round(area_km2(water), 2)
            features.append(_feature(water, {"kind": "flood", "km2": km2, **tags}))
    rows.sort(key=lambda r: (r["aoi"], r["observed_time"] or ""))
    # Products of one AOI see the same place at different times: the largest
    # water of each AOI, summed, so that no place is counted twice.
    largest: dict[str, float] = {}
    looked_at: dict[str, float] = {}
    for r in rows:
        largest[r["aoi"]] = max(largest.get(r["aoi"], 0.0), r["flooded_km2"])
        looked_at[r["aoi"]] = max(looked_at.get(r["aoi"], 0.0), r["observed_km2"])
    card = {
        "code": activation.code,
        "name": activation.name,
        "category": activation.category,
        "event_time": activation.event_time.isoformat(),
        "countries": list(activation.countries),
        "closed": activation.closed,
        "products": rows,
        "without_layer": len(products) - len(events),
        "aois": len(largest),
        "flooded_km2": round(sum(largest.values()), 1),
        "observed_km2": round(sum(looked_at.values()), 0),
        "link": f"https://rapidmapping.emergency.copernicus.eu/{activation.code}",
        "python": python_for(activation.code),
    }
    return Built(card, {"type": "FeatureCollection", "features": features})


def _feature(geometry: BaseGeometry, properties: dict[str, Any]) -> dict[str, Any]:
    light: BaseGeometry = shapely.set_precision(
        shapely.simplify(geometry, SIMPLIFY_DEG, preserve_topology=True), SIMPLIFY_DEG
    )
    return {"type": "Feature", "geometry": mapping(light), "properties": properties}


def _iso(when: datetime | None) -> str | None:
    return when.isoformat() if when else None


def _key(products: list[Product]) -> str:
    """Changes when a product, or a layer's version, does."""
    urls = sorted(layer.url for p in products for layer in p.layers.values())
    return hashlib.sha256(json.dumps([FORMAT, urls]).encode()).hexdigest()[:16]


class Store:
    """The library, its answers kept, one computation at a time per activation."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self._list: tuple[float, list[dict[str, Any]], str] | None = None
        self._built: dict[str, Built] = {}
        self._products: dict[str, list[Product]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ems: RapidMapping | None = None

    @property
    def directory(self) -> Path:
        return self._directory or default_directory()

    @property
    def ems(self) -> RapidMapping:
        if self._ems is None:
            self._ems = RapidMapping(directory=self._directory)
        return self._ems

    async def aclose(self) -> None:
        if self._ems is not None:
            await self._ems.aclose()

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def offline(self) -> bool:
        return activations_path(self.directory).is_file()

    async def activations(self) -> tuple[list[Activation], str]:
        """The activations of the country, from disk if ``init`` ran, else live."""
        if self.offline():
            found = read_activations(self.directory, country=COUNTRY, categories=INIT_CATEGORIES)
            return found, "disk"
        found = await self.ems.activations(country=COUNTRY, categories=INIT_CATEGORIES)
        return found, "live"

    async def products(self, code: str) -> list[Product]:
        if code not in self._products:
            saved = detail_path(code, self.directory).is_file()
            self._products[code] = (
                read_products(code, self.directory) if saved else await self.ems.products(code)
            )
        return self._products[code]

    async def listing(self) -> dict[str, Any]:
        async with self._lock("list"):
            if self._list is None or time.monotonic() - self._list[0] > LIST_TTL_S:
                found, source = await self.activations()
                rows = await asyncio.gather(*(self._row(a) for a in found))
                self._list = (time.monotonic(), list(rows), source)
            _, rows, source = self._list
        newest = sorted(rows, key=lambda r: str(r["event_time"]), reverse=True)
        return {"country": COUNTRY, "source": source, "activations": newest}

    async def _row(self, a: Activation) -> dict[str, Any]:
        point: list[float] | None = None
        try:
            extents = [p.aoi_extent for p in await self.products(a.code) if p.aoi_extent]
        except Exception:  # a detail that fails leaves one dot off the map, not the list
            log.warning("no detail for %s", a.code, exc_info=True)
            extents = []
        if extents:
            centre = shapely.union_all([shape(e) for e in extents]).envelope.centroid
            point = [round(centre.x, 4), round(centre.y, 4)]
        return {
            "code": a.code,
            "name": a.name,
            "category": a.category,
            "event_time": a.event_time.isoformat(),
            "closed": a.closed,
            "point": point,
            "saved": detail_path(a.code, self.directory).is_file(),
        }

    async def built(self, code: str) -> Built:
        async with self._lock(code):
            if code in self._built:
                return self._built[code]
            found, _ = await self.activations()
            activation = next((a for a in found if a.code == code), None)
            if activation is None:
                raise LookupError(code)
            products = await self.products(code)
            path = self.directory / "demo" / f"{code}-{_key(products)}.json"
            if path.is_file():
                saved = json.loads(path.read_bytes())
                done = Built(saved["card"], saved["map"])
            else:
                events = []
                for product in products:
                    layer = await self.ems.layer(product, OBSERVED_EVENT)
                    if layer is not None:
                        events.append(ProductEvents(product, select_features(layer)))
                done = await asyncio.to_thread(build, activation, products, events)
                path.parent.mkdir(parents=True, exist_ok=True)
                partial = path.with_name(f"{path.name}.part")
                partial.write_text(json.dumps({"card": done.card, "map": done.map}))
                partial.replace(path)
            self._built[code] = done
            return done

    async def labels(self, code: str) -> list[str]:
        """The products the card lists, without building it (the radar marks ask per row).

        Built already: its rows. Else the products with an AOI and an observed-event
        layer, as ``build`` keeps them; from disk after ``init``, nothing downloaded.
        An unknown activation is a ``LookupError``.
        """
        done = self._built.get(code)
        if done is not None:
            return [row["product"] for row in done.card["products"]]
        found, _ = await self.activations()
        if not any(a.code == code for a in found):
            raise LookupError(code)
        return [
            p.label
            for p in await self.products(code)
            if p.aoi_extent is not None and OBSERVED_EVENT in p.layers
        ]

    async def card(self, code: str) -> dict[str, Any]:
        card = (await self.built(code)).card
        return {**card, "saved": detail_path(code, self.directory).is_file()}

    async def map(self, code: str) -> dict[str, Any]:
        return (await self.built(code)).map

    async def warm(self) -> None:
        """Prepare, one at a time, every activation ``init`` saved."""
        if not self.offline():
            return
        found, _ = await self.activations()
        for a in found:
            if detail_path(a.code, self.directory).is_file():
                try:
                    await self.built(a.code)
                except Exception:  # the page will ask again, and say what went wrong
                    log.warning("could not prepare %s", a.code, exc_info=True)


Report = tuple[Path, dict[str, Any]]


def _radar_files(directory: Path, code: str, kind: str) -> dict[str, list[Report]]:
    """The readable ``*_<kind>.json`` of an activation's radar folder, by product.

    A file that cannot be read, or has no product, is logged and left out.
    """
    found: dict[str, list[Report]] = {}
    for path in sorted((directory / code / "sentinel-1").glob(f"*_{kind}.json")):
        try:
            body = json.loads(path.read_bytes())
            product = body["product"]
            if not isinstance(body, dict) or not isinstance(product, str):
                raise ValueError("no product")
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("unreadable radar file %s", path, exc_info=True)
            continue
        found.setdefault(product, []).append((path, body))
    return found


def _preferred(candidates: list[Report]) -> Report:
    """The 40 m report first (what the thresholds were calibrated at), then the newest."""

    def rank(item: Report) -> tuple[bool, float]:
        resolution = item[1].get("resolution_m")
        at_40 = isinstance(resolution, int | float) and abs(resolution - 40.0) <= 0.5
        return at_40, item[0].stat().st_mtime

    return max(candidates, key=rank)


def radar_results(directory: Path, code: str) -> dict[str, dict[str, Any]]:
    """What the radar checks say of each product: verdict, flags, areas and scores."""
    qualities = _radar_files(directory, code, "quality")
    agreements = _radar_files(directory, code, "agreement")
    results: dict[str, dict[str, Any]] = {}
    for product in sorted(qualities.keys() | agreements.keys()):
        files: list[str] = []
        quality: dict[str, Any] | None = None
        agreement: dict[str, Any] | None = None
        if product in qualities:
            path, quality = _preferred(qualities[product])
            files.append(path.name)
        if product in agreements:
            path, agreement = _preferred(agreements[product])
            files.append(path.name)
        if quality is not None:
            verdict, reason = quality.get("verdict"), quality.get("reason")
            flags = quality.get("flags") or []
        else:
            # No quality report: the agreement says why it could not be computed, if so.
            only = agreement or {}
            verdict, reason, flags = only.get("quality_verdict"), only.get("reason"), []
        independent = (agreement or {}).get("independent")
        if independent is None and quality is not None and "same_image" in quality:
            independent = not quality["same_image"]
        source = quality if quality is not None else agreement or {}
        results[product] = {
            "verdict": verdict,
            "reason": reason,
            "flags": flags,
            "independent": independent,
            "gap_hours": (quality or agreement or {}).get("gap_hours"),
            "km2": (agreement or {}).get("km2"),
            "scores": (agreement or {}).get("scores"),
            "computed": (agreement or {}).get("computed"),
            "files": sorted(files),
            "resolution_m": source.get("resolution_m"),
        }
    return results


def radar_shapes(directory: Path, code: str, product: str | None) -> dict[str, Any]:
    """The agreement polygons (confirmed, map_only, candidate) of one or every product."""
    features: list[dict[str, Any]] = []
    for name, candidates in sorted(_radar_files(directory, code, "agreement").items()):
        if product is not None and name != product:
            continue
        path, body = _preferred(candidates)
        try:
            shapes = json.loads(path.with_suffix(".geojson").read_bytes())
            listed = shapes["features"]
        except FileNotFoundError:
            continue
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("unreadable radar shapes %s", path, exc_info=True)
            continue
        for f in listed:
            # one shape without a usable geometry is left out, not the whole map
            try:
                klass = f["properties"]["class"]
                geometry = shape(f["geometry"])
                if geometry.is_empty:
                    raise ValueError("empty geometry")
                feature = _feature(
                    geometry,
                    {
                        "class": klass,
                        "km2": f["properties"].get("km2"),
                        "product": name,
                        "independent": body.get("independent"),
                    },
                )
            except Exception:
                log.warning("unreadable radar shape in %s", path, exc_info=True)
                continue
            features.append(feature)
    return {"type": "FeatureCollection", "features": features}


DESCRIPTION_KEYS = frozenset(
    {
        "activation",
        "generated_at",
        "provider",
        "model",
        "prompt_version",
        "inputs_hash",
        "source",
        "facts",
        "description",
        "reason",
        "places",
        "rivers",
        "related_events",
        "casualties",
        "dropped",
    }
)
"""The keys of ``description.json``: those of :class:`copernicus_ems.describe.Description`."""


def read_description(directory: Path, code: str) -> dict[str, Any]:
    """``<directory>/<CODE>/description.json`` as it is, or ``{"available": false}``.

    Read here, not with :mod:`copernicus_ems.describe`, which needs the ``[ai]``
    extra. A file that cannot be read, or that is not a whole description (every
    key of :data:`DESCRIPTION_KEYS`, ``source`` an object: a partial or foreign
    file), is logged and treated as absent.
    """
    path = directory / code / "description.json"
    try:
        body = json.loads(path.read_bytes())
        if not isinstance(body, dict):
            raise ValueError("not an object")
        if set(body) != DESCRIPTION_KEYS or not isinstance(body["source"], dict):
            raise ValueError("not a description")
    except FileNotFoundError:
        return {"available": False}
    except (OSError, ValueError):
        log.warning("unreadable description %s", path, exc_info=True)
        return {"available": False}
    return body


store = Store()

Handler = Callable[[Request], Awaitable[JSONResponse]]


def guarded(answer: Callable[[Request], Awaitable[dict[str, Any]]]) -> Handler:
    """Turn what goes wrong into an answer the page can show."""

    async def endpoint(request: Request) -> JSONResponse:
        try:
            return JSONResponse(await answer(request))
        except LookupError as error:
            return JSONResponse({"error": f"{error} is not an activation of {COUNTRY}"}, 404)
        except Exception as error:  # the service or the network: say it, keep serving
            log.warning("%s failed", request.url.path, exc_info=True)
            message = str(error) if isinstance(error, EmsError | OSError) else repr(error)
            return JSONResponse({"error": f"Rapid Mapping did not answer: {message}"}, 502)

    return endpoint


def _code(request: Request) -> str:
    code = str(request.path_params["code"]).upper()
    if not _CODE.match(code):
        raise LookupError(code)
    return code


async def health(_: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "country": COUNTRY,
        "directory": str(store.directory),
        "offline": store.offline(),
    }


async def activations(_: Request) -> dict[str, Any]:
    return await store.listing()


async def activation(request: Request) -> dict[str, Any]:
    return await store.card(_code(request))


async def activation_map(request: Request) -> dict[str, Any]:
    return await store.map(_code(request))


async def radar(request: Request) -> dict[str, Any]:
    code = _code(request)
    results = radar_results(store.directory, code)
    labels = await store.labels(code)  # cheap: the list asks this once per row
    return {
        "checked": len(results.keys() & set(labels)),
        "products": len(labels),
        "command": f"make radar CODE={code}",
        "results": results,
    }


async def radar_map(request: Request) -> dict[str, Any]:
    code = _code(request)
    await store.labels(code)  # an unknown activation is a 404, not an empty map
    return radar_shapes(store.directory, code, request.query_params.get("product"))


async def description(request: Request) -> dict[str, Any]:
    code = _code(request)
    await store.labels(code)  # an unknown activation is a 404; cheap, no card built
    return read_description(store.directory, code)


@asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    warming = asyncio.create_task(store.warm())
    yield
    warming.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await warming
    await store.aclose()


app = Starlette(
    lifespan=lifespan,
    middleware=[Middleware(GZipMiddleware, minimum_size=1000)],
    routes=[
        Route("/api/health", guarded(health)),
        Route("/api/activations", guarded(activations)),
        Route("/api/activation/{code}", guarded(activation)),
        Route("/api/activation/{code}/map", guarded(activation_map)),
        Route("/api/activation/{code}/radar", guarded(radar)),
        Route("/api/activation/{code}/radar/map", guarded(radar_map)),
        Route("/api/activation/{code}/description", guarded(description)),
    ],
)
