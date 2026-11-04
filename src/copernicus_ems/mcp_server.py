"""Copernicus EMS Rapid Mapping as an MCP server, for LLM agents.

Needs the ``mcp`` extra (``pip install 'copernicus-ems[mcp]'``), then:

    copernicus-ems mcp                       # stdio, for Claude Desktop / Claude Code
    claude mcp add copernicus-ems -- copernicus-ems mcp

The tools answer with **facts and file paths**, never raw geometry: an
activation can hold thousands of polygons, which help no model reason.
Every number is computed here, from the published layers; the model only
chooses the calls and writes the prose. Layers are cached on disk as
everywhere else in the library, so repeated questions cost nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Iterable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypedDict

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.mcp_server needs the mcp extra: pip install 'copernicus-ems[mcp]'"
    ) from exc

import httpx

from copernicus_ems.catalogue import Activation
from copernicus_ems.client import ProductEvents, RapidMapping
from copernicus_ems.errors import EmsError
from copernicus_ems.events import FLOOD_EVENT_TYPE, FLOOD_NOTATIONS, MASS_MOVEMENT_EVENT_TYPE
from copernicus_ems.geo import ProductSummary, summarize

Hazard = Literal["flood", "landslide", "all"]

ATTRIBUTION = (
    "Contains modified Copernicus Emergency Management Service information, © European Union."
)

INSTRUCTIONS = f"""\
Copernicus EMS Rapid Mapping: satellite maps of disasters (floods, fires,
landslides, earthquakes) published within hours, from 2023 on.

How to read the data:
- An activation (code EMSR123) has areas of interest (AOI). Each AOI has
  products: a first map (DEL_PRODUCT, GRA_PRODUCT, FEP_PRODUCT), then
  monitoring updates (DEL_MONIT01, 02, ...).
- Inside an AOI, a mapped polygon is observed flooded; the rest of the AOI
  is observed dry. Outside every AOI nothing was observed: say "unknown",
  never "dry".
- observed_time is when the satellite saw the event; event_time is when the
  event started. Use observed_time for "when was the water there".
- Products of the same AOI are successive views of one place: do not add up
  their areas. Different AOIs are different places.
- Product types are not interchangeable. DEL (delineation) maps the event
  extent; its monitoring updates (DEL_MONIT01, 02, ...) show how it
  evolved. GRA (grading) assesses damage and FEP is a first estimate: their
  mapped area is not a later state of the flood. Track evolution within one
  type only (compare_products gives change_km2 against the previous product
  of the same type).
- Satellite mapping is a lower bound: short urban floods and water under
  canopy may be missing.
- Many floods are filed under category "Storm": filter by country and dates,
  then look at the observed events.

Every figure in an answer must come from a tool result. End any report
with: {ATTRIBUTION}"""

_HAZARDS: dict[str, tuple[tuple[str, ...] | None, tuple[str, ...] | None]] = {
    "flood": ((FLOOD_EVENT_TYPE,), FLOOD_NOTATIONS),
    "landslide": ((MASS_MOVEMENT_EVENT_TYPE,), None),
    "all": (None, None),
}

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)


class ActivationRow(TypedDict):
    code: str
    name: str
    category: str
    event_time: str
    countries: list[str]
    closed: bool


class ActivationList(TypedDict):
    total: int
    returned: int
    activations: list[ActivationRow]


class ProductRow(TypedDict):
    product: str
    product_type: str
    aoi_number: int
    aoi_name: str
    observed_time: str | None
    features: int
    event_km2: float
    observed_km2: float
    event_share_of_observed: float
    bbox_lon_lat: list[float] | None


class AoiRow(TypedDict):
    aoi_number: int
    aoi_name: str
    products: int
    product_types: list[str]
    max_event_km2: float
    #: The latest delineation (DEL) if the AOI has one, else the latest product.
    latest_product: str
    latest_event_km2: float


class ActivationSummary(TypedDict):
    code: str
    name: str | None
    category: str | None
    event_time: str | None
    hazard: str
    products: list[ProductRow]
    aois: list[AoiRow]
    attribution: str


class ProductChange(TypedDict):
    product: str
    product_type: str
    observed_time: str | None
    event_km2: float
    change_km2: float | None


class AoiTimeline(TypedDict):
    code: str
    aoi_number: int
    aoi_name: str
    hazard: str
    timeline: list[ProductChange]
    attribution: str


class Export(TypedDict):
    code: str
    hazard: str
    features: int
    products: int
    events_path: str
    masks_path: str | None
    attribution: str


async def list_activations(
    ems: RapidMapping,
    *,
    country: str | None = None,
    categories: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> ActivationList:
    """Activations, newest first, at most ``limit``."""
    found = await ems.activations(
        country=country,
        categories=categories,
        since=_date(since, "since"),
        until=_date(until, "until"),
    )
    newest = list(reversed(found))
    rows = [_activation_row(a) for a in newest[: max(limit, 0)]]
    return {"total": len(found), "returned": len(rows), "activations": rows}


async def activation_summary(
    ems: RapidMapping, code: str, *, hazard: Hazard = "flood"
) -> ActivationSummary:
    """Per product and per AOI: what was observed, how large, when."""
    code = _code(code)
    events, listed = await asyncio.gather(_events(ems, code, hazard), ems.activations())
    meta = next((a for a in listed if a.code == code), None)
    summaries = await asyncio.to_thread(lambda: [summarize(e) for e in events])
    rows = [_product_row(e, s) for e, s in zip(events, summaries, strict=True)]
    return {
        "code": code,
        "name": meta.name if meta else None,
        "category": meta.category if meta else None,
        "event_time": meta.event_time.isoformat() if meta else None,
        "hazard": hazard,
        "products": rows,
        "aois": _aois(rows),
        "attribution": ATTRIBUTION,
    }


async def compare_products(
    ems: RapidMapping, code: str, aoi_number: int, *, hazard: Hazard = "flood"
) -> AoiTimeline:
    """The products of one AOI in time order, with the change from one to the next."""
    code = _code(code)
    events = [e for e in await _events(ems, code, hazard) if e.product.aoi_number == aoi_number]
    if not events:
        raise ToolError(f"{code} has no {hazard} events in AOI {aoi_number}")
    summaries = await asyncio.to_thread(lambda: [summarize(e) for e in events])
    ordered = sorted(
        zip(events, summaries, strict=True),
        key=lambda pair: (pair[0].product.observed_time is None, pair[0].product.observed_time),
    )
    timeline: list[ProductChange] = []
    previous: dict[str, float] = {}  # by product type: only like is compared with like
    for event, s in ordered:
        km2 = round(s.flooded_km2, 3)
        kind = event.product.product_type
        before = previous.get(kind)
        timeline.append(
            {
                "product": s.product,
                "product_type": kind,
                "observed_time": _iso(event),
                "event_km2": km2,
                "change_km2": None if before is None else round(km2 - before, 3),
            }
        )
        previous[kind] = km2
    return {
        "code": code,
        "aoi_number": aoi_number,
        "aoi_name": events[0].product.aoi_name,
        "hazard": hazard,
        "timeline": timeline,
        "attribution": ATTRIBUTION,
    }


async def export_observed_events(
    ems: RapidMapping,
    code: str,
    *,
    hazard: Hazard = "flood",
    with_masks: bool = True,
    directory: Path | None = None,
) -> Export:
    """Write the events (and the observation masks) of an activation as GeoJSON files."""
    code = _code(code)
    events = await _events(ems, code, hazard)
    out = directory or ems.directory / "exports"
    out.mkdir(parents=True, exist_ok=True)
    features: list[dict[str, Any]] = []
    masks: list[dict[str, Any]] = []
    for event in events:
        tags = {
            "activation": code,
            "product": event.product.label,
            "aoi_name": event.product.aoi_name,
            "observed_time": _iso(event),
        }
        features.extend(
            {**f, "properties": {**(f.get("properties") or {}), **tags}} for f in event.features
        )
        if (geometry := event.product.aoi_extent) is not None:
            masks.append({"type": "Feature", "geometry": geometry, "properties": tags})
    events_path = out / f"{code}_{hazard}.geojson"
    _write(events_path, features)
    masks_path = None
    if with_masks:
        masks_path = out / f"{code}_{hazard}_masks.geojson"
        _write(masks_path, masks)
    return {
        "code": code,
        "hazard": hazard,
        "features": len(features),
        "products": len(events),
        "events_path": str(events_path.resolve()),
        "masks_path": str(masks_path.resolve()) if masks_path else None,
        "attribution": ATTRIBUTION,
    }


def build_server(
    client: httpx.AsyncClient | None = None, *, directory: Path | None = None
) -> MCPServer[Any]:
    """The MCP server; ``client`` and ``directory`` as for :class:`RapidMapping`."""
    ems = RapidMapping(client, directory=directory)

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await ems.aclose()

    server: MCPServer[Any] = MCPServer(
        "copernicus-ems",
        title="Copernicus EMS Rapid Mapping",
        instructions=INSTRUCTIONS,
        website_url="https://agentengineering.it/copernicus-ems/",
        lifespan=lifespan,
    )

    @server.tool(name="list_activations", annotations=_READ_ONLY)
    async def _list(
        country: str | None = None,
        categories: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
    ) -> ActivationList:
        """Public Rapid Mapping activations, newest first.

        country: e.g. "Italy". categories: e.g. ["Flood", "Storm"] (floods are
        often filed as Storm). since / until: YYYY-MM-DD on the event time,
        both included. limit: maximum rows returned (total is always given).
        """
        return await _guard(
            list_activations(
                ems, country=country, categories=categories, since=since, until=until, limit=limit
            )
        )

    @server.tool(name="activation_summary", annotations=_READ_ONLY)
    async def _summary(code: str, hazard: Hazard = "flood") -> ActivationSummary:
        """What one activation observed: per product and per AOI, the event area in km²,
        the observed (AOI) area, when the satellite saw it, and a bounding box.

        Products of the same AOI are successive views of one place: compare
        them, do not add them up. hazard: flood (default), landslide or all.
        """
        return await _guard(activation_summary(ems, code, hazard=hazard))

    @server.tool(name="compare_products", annotations=_READ_ONLY)
    async def _compare(code: str, aoi_number: int, hazard: Hazard = "flood") -> AoiTimeline:
        """How the observed extent of one AOI changed across its products, in time order.

        change_km2 compares each product with the previous one of the same type
        (DEL monitoring series); GRA and FEP are not later states of a DEL map.
        """
        return await _guard(compare_products(ems, code, aoi_number, hazard=hazard))

    @server.tool(
        name="export_observed_events",
        annotations=ToolAnnotations(
            read_only_hint=False, open_world_hint=True, idempotent_hint=True
        ),
    )
    async def _export(code: str, hazard: Hazard = "flood", with_masks: bool = True) -> Export:
        """Write the observed events of an activation (and its observation masks) as
        GeoJSON files on disk, for GIS tools; returns the paths, not the geometry."""
        return await _guard(export_observed_events(ems, code, hazard=hazard, with_masks=with_masks))

    @server.prompt(name="situation_report", title="Situation report")
    def _report(code: str) -> str:
        """A factual report on one activation, every figure from the tools."""
        return (
            f"Write a short situation report on Copernicus EMS activation {code}.\n"
            "1. Call activation_summary to get the products and AOIs.\n"
            "2. For each AOI with more than one product, call compare_products.\n"
            "3. Report: what happened and where, when the satellite saw it "
            "(observed_time), the observed extent per AOI (largest and latest), and "
            "how it evolved. Use a table for the numbers.\n"
            "Every number must come from a tool result; do not add areas of products "
            "of the same AOI; outside the AOIs the situation is unknown, not dry. "
            "State that satellite mapping is a lower bound. "
            f"End with: {ATTRIBUTION}"
        )

    return server


def run(directory: Path | None = None) -> None:  # pragma: no cover - blocks on stdio
    """Serve on standard input and output."""
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request otherwise
    build_server(directory=directory).run("stdio")


async def _guard[T](call: Awaitable[T]) -> T:
    """Turn the failures an agent can act on into tool errors."""
    try:
        result = await call
    except (EmsError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(f"Rapid Mapping answered {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"cannot reach Rapid Mapping: {exc}") from exc
    return result


async def _events(ems: RapidMapping, code: str, hazard: str) -> list[ProductEvents]:
    if hazard not in _HAZARDS:
        raise ToolError(f"hazard must be one of {', '.join(_HAZARDS)}")
    types, notations = _HAZARDS[hazard]
    return await ems.observed_events(code, event_types=types, notations=notations)


def _code(code: str) -> str:
    clean = code.strip().upper()
    if not (clean.startswith("EMSR") and clean[4:].isdigit()):
        raise ToolError(f"not an activation code: {code!r} (expected e.g. EMSR762)")
    return clean


def _date(value: str | None, name: str) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolError(f"{name} must be YYYY-MM-DD, got {value!r}") from exc


def _activation_row(a: Activation) -> ActivationRow:
    return {
        "code": a.code,
        "name": a.name,
        "category": a.category,
        "event_time": a.event_time.isoformat(),
        "countries": list(a.countries),
        "closed": a.closed,
    }


def _product_row(event: ProductEvents, s: ProductSummary) -> ProductRow:
    return {
        "product": s.product,
        "product_type": event.product.product_type,
        "aoi_number": event.product.aoi_number,
        "aoi_name": s.aoi_name,
        "observed_time": _iso(event),
        "features": s.features,
        "event_km2": round(s.flooded_km2, 3),
        "observed_km2": round(s.observed_km2, 1),
        "event_share_of_observed": round(s.flooded_share, 4),
        "bbox_lon_lat": [round(v, 5) for v in s.bbox] if s.bbox else None,
    }


def _aois(rows: Iterable[ProductRow]) -> list[AoiRow]:
    by_aoi: dict[int, list[ProductRow]] = {}
    for row in rows:
        by_aoi.setdefault(row["aoi_number"], []).append(row)
    out: list[AoiRow] = []
    for number, group in sorted(by_aoi.items()):
        delineations = [r for r in group if r["product_type"] == "DEL"]
        latest = max(delineations or group, key=lambda r: r["observed_time"] or "")
        out.append(
            {
                "aoi_number": number,
                "aoi_name": group[0]["aoi_name"],
                "products": len(group),
                "product_types": sorted({r["product_type"] for r in group}),
                "max_event_km2": max(r["event_km2"] for r in group),
                "latest_product": latest["product"],
                "latest_event_km2": latest["event_km2"],
            }
        )
    return out


def _iso(event: ProductEvents) -> str | None:
    observed = event.product.observed_time
    return observed.isoformat() if observed else None


def _write(path: Path, features: list[dict[str, Any]]) -> None:
    partial = path.with_suffix(".part")
    partial.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    partial.replace(path)
