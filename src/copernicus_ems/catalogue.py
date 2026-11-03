"""Activations and products of Copernicus EMS Rapid Mapping.

The public dashboard API needs no credentials:

* ``public-activations-info/`` lists every activation, paginated
  (``count`` / ``next``), newest first;
* ``public-activations/?code=EMSR762`` details one: its areas of interest
  (AOI), and for each AOI the products (delineation ``DEL``, grading
  ``GRA``, first estimate ``FEP``; ``monitoringNumber`` 0 for the first
  product, then 1, 2, ... for each monitoring update), the satellite images
  each product was made from, and its **layers**.

Every vector layer of a product is published on its own as GeoJSON (the
``json`` URL of a ``vt`` layer), from tens of kilobytes to a few megabytes. There is no need for
the product package, which is a ~100 MB zip of zips holding the ortho
imagery, and which for some activations answers 403 (EMSR664) while its
GeoJSON layers are open.

Two things the API does not say:

* the **category** describes the cause, not the effect: many Italian floods
  are filed as ``Storm``. Filter on the features' ``event_type`` instead;
* the list gives ``countries`` as strings, the detail as ``{"name": ...}``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from copernicus_ems.errors import EmsError

API_URL = "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api"
ACTIVATIONS_URL = f"{API_URL}/public-activations-info/"
DETAIL_URL = f"{API_URL}/public-activations/"

#: ``EMSR762/AOI01/DEL_PRODUCT/EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1_VT``
_LAYER_RE = re.compile(r"_(?P<kind>[A-Za-z]+)_v(?P<version>\d+)_VT$")


@dataclass(frozen=True, slots=True)
class Activation:
    """One activation, as listed."""

    code: str
    name: str
    category: str
    event_time: datetime
    countries: tuple[str, ...]
    activation_time: datetime | None = None
    sub_category: str | None = None
    closed: bool = False


@dataclass(frozen=True, slots=True)
class ActivationsPage:
    activations: list[Activation]
    next_url: str | None
    count: int


@dataclass(frozen=True, slots=True)
class Layer:
    """A vector layer of a product, published as GeoJSON."""

    kind: str
    version: int
    url: str


@dataclass(frozen=True, slots=True)
class Product:
    """One map product of one AOI."""

    activation_code: str
    aoi_number: int
    aoi_name: str
    aoi_extent_wkt: str | None
    product_type: str
    monitoring_number: int
    version: int
    acquisitions: tuple[datetime, ...]
    layers: dict[str, Layer] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """``AOI01_DEL_PRODUCT``, ``AOI02_DEL_MONIT01``: the product's own name."""
        stage = f"MONIT{self.monitoring_number:02d}" if self.monitoring_number else "PRODUCT"
        return f"AOI{self.aoi_number:02d}_{self.product_type}_{stage}"

    @property
    def observed_time(self) -> datetime | None:
        """The earliest image the product was made from: when the satellite saw it."""
        return min(self.acquisitions, default=None)


def parse_time(raw: Any) -> datetime | None:
    """An ISO time from the API, as aware UTC (the API omits the zone)."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def activations_url(*, page_size: int = 500) -> str:
    return f"{ACTIVATIONS_URL}?limit={page_size}"


def detail_url(code: str) -> str:
    return f"{DETAIL_URL}?code={code}"


def parse_activations(body: bytes | str) -> ActivationsPage:
    """One page of the activation list. Rows without a usable event time are skipped."""
    data = _json(body)
    if not isinstance(data, dict) or "results" not in data:
        raise EmsError(f"unexpected activation list: {str(body)[:200]!r}")
    out: list[Activation] = []
    for row in data["results"] or []:
        when = parse_time(row.get("eventTime"))
        if when is None:
            continue
        out.append(
            Activation(
                code=str(row["code"]),
                name=str(row.get("name") or row["code"]).strip(),
                category=str(row.get("category") or ""),
                event_time=when,
                countries=_countries(row.get("countries")),
                activation_time=parse_time(row.get("activationTime")),
                sub_category=row.get("subCategory") or None,
                closed=bool(row.get("closed")),
            )
        )
    return ActivationsPage(
        activations=out, next_url=data.get("next") or None, count=int(data.get("count") or 0)
    )


def select_activations(
    activations: Iterable[Activation],
    *,
    country: str | None = None,
    categories: Iterable[str] | None = None,
) -> list[Activation]:
    """Activations in ``country`` and ``categories`` (case-insensitive), oldest first."""
    wanted = {c.lower() for c in categories} if categories is not None else None
    return sorted(
        (
            a
            for a in activations
            if (country is None or country in a.countries)
            and (wanted is None or a.category.lower() in wanted)
        ),
        key=lambda a: a.event_time,
    )


def parse_detail(body: bytes | str) -> list[Product]:
    """Every product of an activation, with its vector layers."""
    data = _json(body)
    if not isinstance(data, dict) or "results" not in data:
        raise EmsError(f"unexpected activation detail: {str(body)[:200]!r}")
    out: list[Product] = []
    for activation in data["results"] or []:
        code = str(activation.get("code") or "")
        for aoi in activation.get("aois") or []:
            for product in aoi.get("products") or []:
                out.append(
                    Product(
                        activation_code=code,
                        aoi_number=int(aoi.get("number") or 0),
                        aoi_name=str(aoi.get("name") or ""),
                        aoi_extent_wkt=aoi.get("extent") or None,
                        product_type=str(product.get("type") or "").upper(),
                        monitoring_number=int(product.get("monitoringNumber") or 0),
                        version=int((product.get("version") or {}).get("number") or 0),
                        acquisitions=tuple(
                            sorted(
                                t
                                for image in product.get("images") or []
                                if (t := parse_time(image.get("acquisitionTime"))) is not None
                            )
                        ),
                        layers=_layers(product.get("layers") or []),
                    )
                )
    return out


def _layers(raw: list[dict[str, Any]]) -> dict[str, Layer]:
    out: dict[str, Layer] = {}
    for layer in raw:
        match = _LAYER_RE.search(str(layer.get("name") or ""))
        if layer.get("format") != "vt" or not layer.get("json") or match is None:
            continue
        found = Layer(kind=match["kind"], version=int(match["version"]), url=str(layer["json"]))
        if found.kind not in out or found.version > out[found.kind].version:
            out[found.kind] = found
    return out


def _countries(raw: Any) -> tuple[str, ...]:
    return tuple(str(c["name"] if isinstance(c, dict) else c) for c in raw or [])


def _json(body: bytes | str) -> Any:
    try:
        return json.loads(body)
    except ValueError as exc:
        raise EmsError(f"Rapid Mapping did not return JSON: {str(body)[:200]!r}") from exc
