"""Observed event layers: what the satellite actually saw under water.

The flooded extent is the ``observedEventA`` layer, not the AOI. On EMSR762
AOI01 the AOI covers ~1,850 km² and the observed flood 1.19 km²: taking the
AOI as the event would call hundreds of dry square kilometres flooded. The
AOI is the **observation mask** instead: inside it, the absence of a flood
polygon is an observed "not flooded"; outside it, nothing was observed.

A flood activation also maps the landslides the same rain triggered
(``event_type`` ``6-Mass Movement``). Keep ``5-Flood``, with the notations
"Flooded area" and "Flood trace" (ground the water left marks on).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from copernicus_ems.errors import EmsError

OBSERVED_EVENT = "observedEventA"
FLOOD_EVENT_TYPE = "5-Flood"
FLOOD_NOTATIONS = ("Flooded area", "Flood trace")


def parse_layer(body: bytes | str) -> dict[str, Any]:
    """A GeoJSON FeatureCollection (CRS84, i.e. lon/lat WGS84)."""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise EmsError(f"layer is not GeoJSON: {str(body)[:200]!r}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise EmsError(f"layer is not a FeatureCollection: {str(body)[:200]!r}")
    return data


def select_features(
    collection: dict[str, Any],
    *,
    event_types: Iterable[str] | None = (FLOOD_EVENT_TYPE,),
    notations: Iterable[str] | None = FLOOD_NOTATIONS,
) -> list[dict[str, Any]]:
    """The features of the given event types and notations (``None`` keeps all)."""
    types = set(event_types) if event_types is not None else None
    marks = set(notations) if notations is not None else None
    out = []
    for feature in collection["features"]:
        props = feature.get("properties") or {}
        if feature.get("geometry") is None:
            continue
        if types is not None and props.get("event_type") not in types:
            continue
        if marks is not None and props.get("notation") not in marks:
            continue
        out.append(feature)
    return out
