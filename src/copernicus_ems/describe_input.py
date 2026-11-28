"""The input of a description: the official text and the library's numbers, from disk.

Needs the ``geo`` extra (``pip install copernicus-ems[geo]``): the numbers come
from :mod:`copernicus_ems.geo`. Nothing here asks the network: the detail and
the layers are those :func:`~copernicus_ems.init` saved, and the radar results
are the ``*_quality.json`` files of ``<CODE>/sentinel-1/``.

:func:`facts` is synchronous (the saved layers are read directly, as
:func:`~copernicus_ems.fetch_layer` does when the file exists), so a sync CLI
can call it. The numbers are **per area of interest** (an AOI has several maps
over time): the most water seen, the area observed, the first and last map
date, the sensors, and how many maps the radar confirms, marks to look at or
cannot assess.

:func:`allowed_numbers` and :func:`allowed_names` list everything a model may
legitimately write: the checks of a description compare against them.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from copernicus_ems.catalogue import Product
from copernicus_ems.client import ProductEvents, default_directory, layer_path
from copernicus_ems.errors import EmsError
from copernicus_ems.events import OBSERVED_EVENT, parse_layer, select_features
from copernicus_ems.offline import detail_path, read_activations, read_products
from copernicus_ems.reports import activation_reports

try:
    from copernicus_ems.geo import area_km2, mask, summarize
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.describe_input needs the geo extra: pip install 'copernicus-ems[geo]'"
    ) from exc

_VERDICTS = {"coherent": "confirms", "to check": "to_check", "not assessable": "not_assessable"}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_CAPITALISED = re.compile(r"\b[A-ZÀ-Ý][\w'\u2019-]*")
_WORD = re.compile(r"[\w'\u2019-]+")
_PARTS = re.compile(r"[\s'\u2019-]+")


@dataclass(frozen=True, slots=True)
class Source:
    """The official text of an activation: its reason, bulletins and related events."""

    code: str
    name: str
    reason: str
    bulletins: list[dict[str, str]]
    related: list[str]

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return asdict(self)


def _detail(code: str, directory: Path | None) -> dict[str, Any]:
    body = json.loads(detail_path(code, directory).read_bytes())
    results = body.get("results") if isinstance(body, dict) else None
    first = results[0] if isinstance(results, list) and results else None
    if not isinstance(first, dict):
        raise EmsError(f"{code}: the saved detail has no activation")
    return first


def _name(code: str, directory: Path | None) -> str:
    try:
        found = read_activations(directory)
    except FileNotFoundError:
        return ""
    return next((a.name for a in found if a.code == code), "")


def read_source(code: str, directory: Path | None = None) -> Source:
    """The official text :func:`~copernicus_ems.init` saved in ``detail.json``."""
    detail = _detail(code, directory)
    bulletins = [
        {"title": str(b.get("title") or ""), "url": str(b.get("url") or "")}
        for b in detail.get("infobulletins") or []
        if isinstance(b, dict)
    ]
    related = [str(r) for r in detail.get("relatedevents") or []]
    return Source(
        code=code,
        name=_name(code, directory),
        reason=str(detail.get("reason") or ""),
        bulletins=bulletins,
        related=related,
    )


def _events(products: list[Product], directory: Path) -> list[ProductEvents]:
    """The saved observed-event layers, as ``RapidMapping(directory=...)`` reads them."""
    out = []
    for product in products:
        layer = product.layers.get(OBSERVED_EVENT)
        if layer is None:
            continue
        path = layer_path(product, layer, directory)
        if not path.is_file():
            continue
        out.append(ProductEvents(product, select_features(parse_layer(path.read_bytes()))))
    return out


def _radar(directory: Path, code: str) -> dict[str, str]:
    """Verdict of each product: the newest pair (:func:`copernicus_ems.reports.newest_pair`)."""
    scan = activation_reports(directory, code, classes=())
    return {r.product: r.quality.verdict for r in scan.reports if r.quality is not None}


def facts(code: str, directory: Path | None = None) -> dict[str, Any]:
    """The library's numbers for an activation, per area of interest (saved data only)."""
    root = directory or default_directory()
    products = read_products(code, root)
    verdicts = _radar(root, code)
    found = next((a for a in read_activations(root) if a.code == code), None)
    aois: dict[str, dict[str, Any]] = {}
    for event in _events(products, root):
        product = event.product
        if product.aoi_extent is None:
            continue
        water = summarize(event).flooded_km2 if event.features else 0.0
        observed = area_km2(mask(event))
        name = product.aoi_name or f"AOI{product.aoi_number:02d}"
        row = aois.setdefault(
            name,
            {
                "water_km2": 0.0,
                "observed_km2": 0.0,
                "first_map": None,
                "last_map": None,
                "sensors": set(),
                "maps": 0,
                "radar": {"confirms": 0, "to_check": 0, "not_assessable": 0},
            },
        )
        row["water_km2"] = max(row["water_km2"], water)
        row["observed_km2"] = max(row["observed_km2"], observed)
        row["maps"] += 1
        row["sensors"].update(i.sensor_name for i in product.images if i.sensor_name)
        if product.observed_time is not None:
            day = product.observed_time.date().isoformat()
            row["first_map"] = min(filter(None, (row["first_map"], day)))
            row["last_map"] = max(filter(None, (row["last_map"], day)))
        key = _VERDICTS.get(verdicts.get(product.label, ""))
        if key:
            row["radar"][key] += 1
    for row in aois.values():
        row["water_km2"] = round(row["water_km2"], 2)
        row["observed_km2"] = round(row["observed_km2"])
        row["sensors"] = sorted(row["sensors"])
    return {
        "code": code,
        "name": found.name if found else "",
        "category": found.category if found else "",
        "event_time": found.event_time.isoformat() if found else "",
        "countries": list(found.countries) if found else [],
        "aois": dict(sorted(aois.items())),
    }


def _decimal(value: float, places: int) -> set[str]:
    """``16.2`` as a description writes it: with and without the trailing zero, both separators."""
    shown = {f"{value:.{places}f}", f"{value:g}"}
    return shown | {s.replace(".", ",") for s in shown}


def _thousands(value: int) -> set[str]:
    """``1846`` as written with a thousands separator: ``1.846``, ``1,846``, ``1 846``."""
    plain = str(value)
    if value < 1000:
        return {plain}
    head, tail = plain[:-3], plain[-3:]
    return {plain} | {head + sep + tail for sep in (".", ",", " ", "\u2009", "\u00a0", "\u202f")}


def _dates(value: str) -> set[str]:
    """``2024-09-20`` and its parts as written: ISO, the day number, the year."""
    head = value[:10]
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", head)
    if not match:
        return set()
    year, month, day = match.groups()
    return {head, year, str(int(day)), day, str(int(month))}


def allowed_numbers(facts: dict[str, Any], source: Source) -> set[str]:
    """Every number a description may use, as the strings it would write."""
    out: set[str] = set()
    aois = facts.get("aois", {})
    out |= {str(len(aois))}
    for value in (facts.get("event_time"),):
        if isinstance(value, str):
            out |= _dates(value)
    for row in aois.values():
        out |= _decimal(row["water_km2"], 2)
        out |= _thousands(int(row["observed_km2"])) | {str(row["maps"])}
        for day in (row.get("first_map"), row.get("last_map")):
            if day:
                out |= _dates(day)
        out |= {str(n) for n in row["radar"].values()}
    totals = {
        key: sum(row["radar"][key] for row in aois.values())
        for key in ("confirms", "to_check", "not_assessable")
    }
    out |= {str(n) for n in totals.values()}
    out |= {str(sum(row["maps"] for row in aois.values()))}
    out |= _decimal(round(sum(r["water_km2"] for r in aois.values()), 2), 2)
    texts = [source.reason, source.name, facts.get("name", ""), source.code]
    texts += [b["title"] for b in source.bulletins] + list(source.related)
    for text in texts:
        out |= set(_NUMBER.findall(text))
        out |= {n.replace(",", ".") for n in _NUMBER.findall(text)}
    return out


def allowed_names(facts: dict[str, Any], source: Source) -> set[str]:
    """Every name a description may use: AOIs, sensors, the activation's words, the text's.

    Each name is kept whole and split into its words (on spaces, apostrophes and
    hyphens): ``"Spino d'Adda"`` also allows ``"Spino"`` and ``"Adda"``.
    """
    out: set[str] = set(facts.get("aois", {}))
    for row in facts.get("aois", {}).values():
        out |= set(row["sensors"])
    out |= set(_WORD.findall(facts.get("name", "") or source.name))
    out |= set(facts.get("countries", []))
    if facts.get("category"):
        out.add(facts["category"])
    texts = [source.reason, *(b["title"] for b in source.bulletins)]
    for text in texts:
        out |= set(_CAPITALISED.findall(text))
    out |= set(source.related)
    out.add(source.code)
    for name in list(out):
        out |= set(_PARTS.split(name))
    return {n for n in out if n}
