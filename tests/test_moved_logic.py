"""What the parity tests do not reach: ids, the pure truth, edges of reports and overlay."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import parity_inputs as inputs
import pytest
from shapely.geometry import box

from copernicus_ems import OBSERVED_EVENT, EmsError, layer_path, parse_detail
from copernicus_ems.geo import area_km2
from copernicus_ems.ids import delivered_product_ids, is_monitoring, polygon_id, product_id
from copernicus_ems.overlay import places_hit, split_by_zones
from copernicus_ems.reports import (
    CLASS_CONFIRMED,
    TO_CHECK,
    VERDICTS,
    collect_reports,
    read_classes,
    read_quality,
)
from copernicus_ems.truth import (
    activation_truth,
    area_measure,
    fetch_activation_truth,
    observation_mask,
)

PRODUCTS = parse_detail(inputs.DETAIL.read_bytes())
FIRST = PRODUCTS[0]


def test_ids_of_a_product() -> None:
    assert product_id(FIRST) == "EMSR762-aoi01-DEL0"
    assert polygon_id(FIRST, 12) == "EMSR762-aoi01-DEL0-12-0"
    assert polygon_id(FIRST, 12, 3) == "EMSR762-aoi01-DEL0-12-3"
    assert [is_monitoring(product_id(p)) for p in PRODUCTS[:2]] == [False, True]


@pytest.mark.parametrize("body", [b"<html>", b"[]"])
def test_delivered_products_of_a_bad_detail(body: bytes) -> None:
    with pytest.raises(EmsError):
        delivered_product_ids(body)


def test_pure_truth_measures_on_the_ellipsoid_by_default() -> None:
    layers = {product_id(FIRST): json.loads(inputs.LAYER.read_bytes())}
    truth = activation_truth(inputs.activation(), PRODUCTS, layers)
    assert truth.read_products == ["EMSR762-aoi01-DEL0", "EMSR762-aoi05-GRA0"]
    assert [m.id for m in truth.masks] == ["EMSR762-aoi01-DEL0"]
    assert truth.floods and all(f.area_km2 == area_km2(f.geom) for f in truth.floods)
    # Two ways of measuring the same polygons: they agree to a part in a thousand.
    planar = area_measure(3035)
    for flood in truth.floods:
        assert planar(flood.geom) == pytest.approx(flood.area_km2, rel=1e-3)


def test_delivered_without_an_observed_layer_is_neither_read_nor_absent() -> None:
    orthophotos = replace(FIRST, layers={})
    key = product_id(orthophotos)
    truth = activation_truth(inputs.activation(), [orthophotos], {}, delivered={key})
    assert truth.delivered_without_event == [key]
    assert truth.absent_masks == [] and truth.read_products == [] and truth.masks == []
    never = activation_truth(inputs.activation(), [orthophotos], {})
    assert never.absent_masks == [key] and never.read_products == [key]


def test_no_mask_without_extent_image_or_readable_wkt() -> None:
    assert observation_mask(FIRST) is not None
    assert observation_mask(replace(FIRST, aoi_extent_wkt=None)) is None
    assert observation_mask(replace(FIRST, acquisitions=())) is None
    assert observation_mask(replace(FIRST, aoi_extent_wkt="POLYGON ((1 2, nonsense")) is None


def test_a_damaged_cache_that_cannot_be_replaced_is_an_error(tmp_path: Path) -> None:
    path = layer_path(FIRST, FIRST.layers[OBSERVED_EVENT], tmp_path)
    inputs.damage_cache(path)

    def handler(request: httpx.Request) -> httpx.Response:
        if "public-activations/" in str(request.url):
            return httpx.Response(200, content=inputs.DETAIL.read_bytes())
        return httpx.Response(503)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            truth = await fetch_activation_truth(client, inputs.activation(), directory=tmp_path)
        assert truth.floods == [] and truth.masks == []
        assert "503" in truth.errors["EMSR762-aoi01-DEL0"]
        assert truth.read_products == ["EMSR762-aoi05-GRA0"]

    asyncio.run(run())
    assert not path.exists()


def test_verdicts_are_those_the_quality_check_writes() -> None:
    from copernicus_ems import quality

    assert (quality.COHERENT, quality.TO_CHECK, quality.NOT_ASSESSABLE) == VERDICTS
    assert TO_CHECK == quality.TO_CHECK


def test_reports_without_geometry_and_with_one_class(tmp_path: Path) -> None:
    inputs.write_reports(tmp_path)
    scan = collect_reports(tmp_path, classes=())
    assert all(r.classes == {} for r in scan.reports)
    assert not [n for n in scan.notes if n.kind == "unreadable_geojson"]
    geojson = next((tmp_path / "EMSR664" / "sentinel-1").glob("*20230520*_agreement.geojson"))
    assert list(read_classes(geojson, {CLASS_CONFIRMED})) == [CLASS_CONFIRMED]
    assert list(read_classes(geojson, {"map_only"})) == ["map_only"]
    with pytest.raises(ValueError, match="unknown verdict"):
        read_quality(tmp_path / "EMSR664" / "sentinel-1" / "AOI06_DEL_PRODUCT_40m_x_quality.json")


def test_reports_and_ids_need_no_extra() -> None:
    code = (
        "import sys, copernicus_ems, copernicus_ems.reports, copernicus_ems.ids;"
        "print([m for m in ('shapely', 'pyproj', 'numpy') if m in sys.modules])"
    )
    out = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
    assert out.stdout.strip() == "[]"


def test_nothing_in_nothing_out() -> None:
    assert places_hit([], inputs.places()) == []
    split = split_by_zones([box(0, 0, 1000, 1000)], [], classes=("high", "low"))
    assert split.water_m2 == 1e6 and split.outside_m2 == 1e6
    assert split.by_class_m2 == {"high": 0.0, "low": 0.0}
    assert split.outside_filtered_m2 == 1e6 and split.outside_patches == 1


def test_any_classes_and_the_highest_wins() -> None:
    water = [box(0, 0, 1000, 1000)]
    zones = [("low", box(0, 0, 600, 1000)), ("high", box(0, 0, 200, 1000))]
    split = split_by_zones(water, zones, classes=("high", "low"), tolerance_m=0, min_patch_m2=0)
    assert split.by_class_m2 == pytest.approx({"high": 200_000.0, "low": 400_000.0})
    assert split.outside_m2 == pytest.approx(400_000.0)
    assert split.share(split.outside_m2) == pytest.approx(0.4)
    with pytest.raises(ValueError, match="other"):
        split_by_zones(water, [("other", box(0, 0, 1, 1))], classes=("high", "low"))
