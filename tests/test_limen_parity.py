"""Parity with Limen: the functions moved from it return what Limen's code returned.

``tests/fixtures/limen_parity.json`` holds the answers of Limen's own
implementation on the inputs of ``parity_inputs.py`` (written by
``scripts/limen_parity.py``, in Limen's environment). Here the same inputs go
through this library. Numbers computed through PROJ are compared to nine
digits: the two environments may carry different PROJ versions.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import parity_inputs as inputs
import pytest
import shapely

from copernicus_ems import (
    CATALOGUE_FROM_YEAR,
    OBSERVED_EVENT,
    attribution,
    layer_path,
    parse_detail,
)
from copernicus_ems.ids import (
    aoi_label,
    delivered_product_ids,
    is_monitoring,
    label_product_id,
    polygon_product_id,
)
from copernicus_ems.overlay import WaterPolygon, places_hit, split_by_zones
from copernicus_ems.reports import collect_reports
from copernicus_ems.truth import DETAIL, ActivationTruth, fetch_activation_truth

GOLDEN: dict[str, Any] = json.loads(inputs.GOLDEN.read_text())


def close(value: float) -> Any:
    return pytest.approx(value, rel=1e-9, abs=1e-9)


def test_ids() -> None:
    golden = GOLDEN["ids"]
    assert [label_product_id(code, label) for code, label in inputs.LABELS] == golden["labels"]
    assert [aoi_label(label) for _, label in inputs.LABELS] == golden["aoi_labels"]
    assert [polygon_product_id(p) for p in inputs.POLYGON_IDS] == golden["polygon_products"]
    assert [is_monitoring(p) for p in inputs.PRODUCT_IDS] == golden["monitoring"]
    assert attribution(2023) == golden["attribution"]
    assert golden["catalogue_from_year"] == CATALOGUE_FROM_YEAR


def test_delivered_products() -> None:
    assert sorted(delivered_product_ids(inputs.DETAIL.read_bytes())) == GOLDEN["delivered"]


def _truth(scenario: str, directory: Path) -> ActivationTruth:
    def handler(request: httpx.Request) -> httpx.Response:
        if scenario == "detail-down":
            raise httpx.ConnectError("no route")
        status, body = inputs.respond(str(request.url), scenario)
        return httpx.Response(status, content=body)

    async def run() -> ActivationTruth:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # EPSG:3035 is the CRS Limen measured the polygons in.
            return await fetch_activation_truth(
                client, inputs.activation(), directory=directory, area_crs=3035
            )

    return asyncio.run(run())


@pytest.mark.parametrize("scenario", ["layers", "corrupt-cache", "detail-down"])
def test_activation_truth(scenario: str, tmp_path: Path) -> None:
    golden = GOLDEN["truth"][scenario]
    damaged: Path | None = None
    if scenario == "corrupt-cache":
        product = parse_detail(inputs.DETAIL.read_bytes())[0]
        damaged = layer_path(product, product.layers[OBSERVED_EVENT], tmp_path)
        inputs.damage_cache(damaged)

    truth = _truth(scenario, tmp_path)

    assert [f.id for f in truth.floods] == [g["id"] for g in golden["floods"]]
    for flood, g in zip(truth.floods, golden["floods"], strict=True):
        assert flood.area_km2 == close(g["area_km2"])
        if "wkt" not in g:
            continue
        assert flood.geom.equals(shapely.from_wkt(g["wkt"]))
        assert {
            "activation_code": flood.activation_code,
            "aoi_label": flood.aoi_label,
            "event_time": inputs.iso(flood.event_time),
            "observed_time": inputs.iso(flood.observed_time),
            "event_type": flood.event_type,
            "detection_method": flood.detection_method,
            "attributes": flood.attributes,
        } == {k: v for k, v in g.items() if k not in ("id", "area_km2", "wkt")}
    assert [
        {
            "id": m.id,
            "activation_code": m.activation_code,
            "aoi_label": m.aoi_label,
            "product_type": m.product_type,
            "observed_time": inputs.iso(m.observed_time),
            "wkt": m.geom.wkt,
            "area_km2": m.area_km2,
        }
        for m in truth.masks
    ] == golden["masks"]
    assert truth.absent_masks == golden["absent_masks"]
    assert truth.read_products == golden["read_products"]
    if damaged is not None:
        assert json.loads(damaged.read_text())["features"]  # downloaded again, whole


def test_activation_truth_says_what_limen_logged(tmp_path: Path) -> None:
    truth = _truth("layers", tmp_path)
    # Limen logged these as ems.layer.unavailable; here they come back as data.
    assert sorted(truth.errors) == ["EMSR762-aoi02-DEL1", "EMSR762-aoi03-DEL0"]
    assert "500" in truth.errors["EMSR762-aoi02-DEL1"]
    assert truth.delivered_without_event == []
    assert list(_truth("detail-down", tmp_path).errors) == [DETAIL]


def test_reports(tmp_path: Path) -> None:
    inputs.write_reports(tmp_path)
    scan = collect_reports(tmp_path)
    assert [
        {
            "activation_code": r.activation_code,
            "product": r.product,
            "product_id": r.product_id,
            "stem": r.stem,
            "verdict": r.verdict,
            "independent": r.independent,
            "quality": None
            if r.quality is None
            else {**asdict(r.quality), "flag_codes": list(r.quality.flag_codes)},
            "agreement": None if r.agreement is None else asdict(r.agreement),
            "classes": {
                name: {"name": c.name, "area_km2": c.area_km2, "wkt": c.geom.wkt}
                for name, c in r.classes.items()
            },
        }
        for r in scan.reports
    ] == GOLDEN["reports"]
    # One note for each line Limen logged on these files.
    assert sorted((n.kind, n.product) for n in scan.notes) == [
        ("several_pairs", "AOI01_DEL_PRODUCT"),
        ("several_pairs", "AOI02_DEL_MONIT03"),
        ("unknown_product", "WHOLE_AREA"),
        ("unreadable_file", "AOI06_DEL_PRODUCT"),
        ("unreadable_file", "AOI07_DEL_PRODUCT"),
        ("unreadable_file", "AOI08_DEL_PRODUCT"),
        ("unreadable_geojson", "AOI04_FEP_PRODUCT"),
        ("unreadable_geojson", "AOI05_DEL_PRODUCT"),
    ]


def test_places_hit() -> None:
    water = [WaterPolygon(**p) for p in inputs.water_polygons()]
    hits = places_hit(water, inputs.places())
    golden = GOLDEN["places"]
    assert [h.key for h in hits] == [g["key"] for g in golden]
    for hit, g in zip(hits, golden, strict=True):
        assert hit.water_km2 == close(g["water_km2"])
        assert inputs.iso(hit.start) == g["start"]
        assert inputs.iso(hit.observed_from) == g["observed_from"]
        assert inputs.iso(hit.observed_to) == g["observed_to"]
        assert list(hit.products) == g["products"]


@pytest.mark.parametrize("index", range(len(inputs.SPLIT_CASES) + 1))
def test_split_by_zones(index: int) -> None:
    golden = GOLDEN["split"][index]
    if index < len(inputs.SPLIT_CASES):
        split = split_by_zones(
            inputs.split_water(),
            inputs.zones(),
            classes=inputs.CLASSES,
            **inputs.SPLIT_CASES[index],
        )
    else:
        split = split_by_zones([], inputs.zones(), classes=inputs.CLASSES)
    assert split.water_m2 == close(golden["water_m2"])
    assert split.by_class_m2 == {k: close(v) for k, v in golden["by_class_m2"].items()}
    assert list(split.by_class_m2) == list(inputs.CLASSES)
    assert split.outside_m2 == close(golden["outside_m2"])
    assert split.outside_filtered_m2 == close(golden["outside_filtered_m2"])
    assert split.outside_patches == golden["outside_patches"]
    share = split.share(split.outside_m2)
    assert share == (None if golden["share_outside"] is None else close(golden["share_outside"]))
