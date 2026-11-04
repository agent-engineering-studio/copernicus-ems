"""Contract tests against the live services, weekly in CI (``.github/workflows/live.yml``).

The Rapid Mapping dashboard API is undocumented, and the recorded fixtures
the other tests use cannot notice when it changes. These tests ask the real
services the same questions and check the shape and the facts the library
relies on. They are skipped unless ``COPERNICUS_EMS_LIVE=1``:

    COPERNICUS_EMS_LIVE=1 uv run pytest -m live --no-cov
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from copernicus_ems import (
    SENTINEL_1_GRD,
    SENTINEL_2_L2A,
    Product,
    RapidMapping,
    detail_url,
    find_scenes,
    select_features,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("COPERNICUS_EMS_LIVE") != "1", reason="set COPERNICUS_EMS_LIVE=1"
    ),
]

FIXTURES = Path(__file__).parent / "fixtures"
CODE = "EMSR762"  # the activation of the fixtures: Emilia-Romagna, September 2024


def run(body: Any) -> Any:
    async def go() -> Any:
        async with RapidMapping() as ems:
            return await body(ems)

    return asyncio.run(go())


def aoi01(products: list[Product]) -> Product:
    (found,) = [p for p in products if p.label == "AOI01_DEL_PRODUCT"]
    return found


def test_the_activation_list() -> None:
    activations = run(lambda ems: ems.activations())
    assert len(activations) > 250
    assert all(re.fullmatch(r"EMSR\d+", a.code) for a in activations)
    assert all(a.event_time.tzinfo is not None for a in activations)
    by_code = {a.code: a for a in activations}
    for code in (CODE, "EMSR664"):
        assert "Italy" in by_code[code].countries
    assert by_code["EMSR664"].event_time.date().isoformat() == "2023-05-16"


def test_the_detail_still_has_the_fields_the_parser_reads() -> None:
    """Missing keys would not fail the parser: they would silently empty the results."""
    live = httpx.get(detail_url(CODE), timeout=120, follow_redirects=True).json()
    recorded = json.loads((FIXTURES / "detail_EMSR762.json").read_text())

    def keys(data: dict[str, Any]) -> dict[str, set[str]]:
        activation = data["results"][0]
        aoi = activation["aois"][0]
        product = aoi["products"][0]
        return {
            "activation": set(activation),
            "aoi": set(aoi),
            "product": set(product),
            "image": set(product["images"][0]),
            "layer": set(product["layers"][0]),
        }

    used = {
        "activation": {"code", "aois"},
        "aoi": {"number", "name", "extent", "products"},
        "product": {"type", "monitoringNumber", "version", "images", "layers"},
        "image": {"acquisitionTime", "sensorName", "sensorType", "resolutionClass"},
        "layer": {"name", "format", "json"},
    }
    now, then = keys(live), keys(recorded)
    for level, fields in used.items():
        assert fields <= now[level], f"{level} lost {sorted(fields - now[level])}"
        assert fields <= then[level]


def test_products_layers_masks_and_images() -> None:
    products = run(lambda ems: ems.products(CODE))
    assert len(products) >= 7
    first = aoi01(products)
    assert first.observed_time is not None
    assert first.aoi_extent is not None and first.aoi_extent["type"] in {"Polygon", "MultiPolygon"}
    assert first.images and first.images[0].sensor_name
    assert "observedEventA" in first.layers


def test_the_observed_flood_layer_matches_the_fixture() -> None:
    async def body(ems: RapidMapping) -> tuple[Product, dict[str, Any] | None]:
        product = aoi01(await ems.products(CODE))
        return product, await ems.layer(product, "observedEventA")

    product, layer = run(body)
    assert layer is not None
    floods = select_features(layer)
    assert floods, "no 5-Flood feature: has event_type or notation changed?"
    if product.layers["observedEventA"].version == 1:  # published versions never change
        recorded = json.loads(
            (FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json").read_text()
        )
        assert len(floods) == len(select_features(recorded)) == 43


def test_the_sentinel_catalogue() -> None:
    async def body(ems: RapidMapping) -> Any:
        product = aoi01(await ems.products(CODE))
        async with httpx.AsyncClient(timeout=120) as http:
            return await find_scenes(
                http,
                product,
                collections=[SENTINEL_1_GRD, SENTINEL_2_L2A],
                within=timedelta(days=2),
            )

    matches = run(body)
    recorded = {
        f["id"] for f in json.loads((FIXTURES / "stac_EMSR762_AOI01.json").read_text())["features"]
    }
    live = {m.scene.id for m in matches}
    assert recorded <= live, f"scenes gone from the catalogue: {sorted(recorded - live)}"
    radar = [m.scene for m in matches if m.scene.collection == SENTINEL_1_GRD]
    assert radar and radar[0].download_url and radar[0].datatake_id


@pytest.mark.skipif(
    not (os.environ.get("CDSE_CLIENT_ID") or os.environ.get("CDSE_CREDENTIALS_FILE")),
    reason="needs CDSE credentials (CDSE_CLIENT_ID / CDSE_CLIENT_SECRET)",
)
def test_sentinel1_backscatter_lands_on_the_label_grid(tmp_path: Path) -> None:
    """A small real request (~1 PU): the image must be on exactly the labels' grid."""
    import rasterio

    from copernicus_ems.ml import Grid
    from copernicus_ems.sentinelhub import SentinelHub, make_pair, plan_pair

    async def body(ems: RapidMapping) -> Any:
        events = await ems.observed_events("EMSR664", event_types=None, notations=None)
        (small,) = [e for e in events if e.product.label == "AOI04_GRA_PRODUCT"]  # Faenza
        plan = await plan_pair(ems, small, resolution_m=40, directory=tmp_path)
        assert plan.scene_pass is not None, plan.reason
        assert plan.pu < 2
        async with SentinelHub() as hub:
            return await make_pair(hub, plan)

    image, labels = run(body)
    with rasterio.open(image.path) as img, rasterio.open(labels) as lab:
        assert Grid.from_transform(img.crs, img.transform, img.width, img.height) == (
            Grid.from_transform(lab.crs, lab.transform, lab.width, lab.height)
        )
        vv, _vh, mask = img.read()
    assert mask.mean() > 0.9, "most of the AOI should have data"
    assert 0 < float(vv[mask > 0].mean()) < 1, "linear backscatter is mostly below 1"
