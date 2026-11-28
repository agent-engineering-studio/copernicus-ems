"""Stable identifiers of products and of their polygons, as plain strings.

A product is ``EMSR664-aoi01-DEL0``: activation, AOI, type and monitoring
number (0 for the first issue). A polygon of its observed layer is
``EMSR664-aoi01-DEL0-12-0``: the product, the position of the feature **in
the whole layer** (before any filter) and the part of a MultiPolygon. The
position does not change when the layer is read again, so a store keyed on
these ids can be written twice with the same result.

No dependency: the ids are made and read with string functions.
"""

from __future__ import annotations

import json
import re
from typing import Any

from copernicus_ems.catalogue import Product
from copernicus_ems.errors import EmsError

_LABEL = re.compile(r"^AOI(\d+)_([A-Z]+)_(?:PRODUCT|MONIT(\d+))$")
_POLYGON_TAIL = re.compile(r"-\d+-\d+$")
_TYPE = re.compile(r"(?P<type>[A-Z]+)(?P<number>\d+)$")


def product_id(product: Product) -> str:
    """``EMSR664-aoi01-DEL0`` (first issue), ``EMSR664-aoi01-DEL1`` (first monitoring)."""
    return (
        f"{product.activation_code}-aoi{product.aoi_number:02d}-{product.product_type}"
        f"{product.monitoring_number}"
    )


def label_product_id(activation_code: str, label: str) -> str | None:
    """The :func:`product_id` from a product label (:attr:`Product.label`).

    ``("emsr664", "AOI01_GRA_PRODUCT")`` gives ``EMSR664-aoi01-GRA0``,
    ``AOI01_DEL_MONIT03`` gives ``…-DEL3``. ``None`` if the label has another form.
    """
    match = _LABEL.match(label.strip().upper())
    if match is None:
        return None
    aoi, kind, monitoring = match.groups()
    return f"{activation_code.upper()}-aoi{int(aoi):02d}-{kind}{int(monitoring or 0)}"


def aoi_label(label: str) -> str:
    """``AOI01`` from ``AOI01_DEL_PRODUCT``."""
    return label.split("_", 1)[0].upper()


def polygon_id(product: Product, position: int, part: int = 0) -> str:
    """``EMSR664-aoi01-DEL0-12-0``: feature ``position`` of the layer, ``part`` of its geometry."""
    return f"{product_id(product)}-{position}-{part}"


def polygon_product_id(polygon: str) -> str:
    """The product of a :func:`polygon_id`: ``EMSR664-aoi01-DEL1-12-0`` gives ``…-DEL1``."""
    return _POLYGON_TAIL.sub("", polygon)


def is_monitoring(product: str) -> bool:
    """``DEL1``, ``DEL2``… yes; ``DEL0``, ``GRA0``, ``FEP0`` no (``product`` is a product id).

    Monitoring products draw the same place again on a later day: adding them
    to the first issue counts the same water twice. An id that cannot be read
    counts as monitoring: better to lose a product than to count water twice.
    """
    match = _TYPE.search(product)
    return match is None or int(match["number"]) > 0


def delivered_product_ids(body: bytes | str) -> set[str]:
    """The ids of the **delivered** products of an activation detail.

    Delivered: the detail lists at least one layer, of any format.
    :attr:`Product.layers` keeps only the vectors with GeoJSON, so a product
    of orthophotos alone looks there like one never delivered; the raw detail
    tells them apart.
    """
    try:
        data: Any = json.loads(body)
    except ValueError as exc:
        raise EmsError(f"unexpected activation detail: {str(body)[:200]!r}") from exc
    if not isinstance(data, dict):
        raise EmsError(f"unexpected activation detail: {str(body)[:200]!r}")
    out: set[str] = set()
    for activation in data.get("results") or []:
        code = str(activation.get("code") or "")
        for aoi in activation.get("aois") or []:
            for product in aoi.get("products") or []:
                if product.get("layers"):
                    out.add(
                        f"{code}-aoi{int(aoi.get('number') or 0):02d}-"
                        f"{str(product.get('type') or '').upper()}"
                        f"{int(product.get('monitoringNumber') or 0)}"
                    )
    return out
