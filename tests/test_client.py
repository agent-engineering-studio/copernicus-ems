from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx

from copernicus_ems import fetch_activations, fetch_observed_events, fetch_products

FIXTURES = Path(__file__).parent / "fixtures"
LAYER = "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json"


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "public-activations-info" in url:
        if "offset" in url:
            return httpx.Response(
                200,
                json={
                    "count": 4,
                    "next": None,
                    "results": [
                        {
                            "code": "EMSR1",
                            "eventTime": "2023-01-01T00:00:00",
                            "countries": ["Italy"],
                        }
                    ],
                },
            )
        return httpx.Response(200, content=(FIXTURES / "activations_page.json").read_bytes())
    if "public-activations/" in url:
        return httpx.Response(200, content=(FIXTURES / "detail_EMSR762.json").read_bytes())
    if url.endswith(LAYER):
        return httpx.Response(302, headers={"location": "https://s3.example/layer.json"})
    if url == "https://s3.example/layer.json":
        return httpx.Response(200, content=(FIXTURES / LAYER).read_bytes())
    if url.endswith(".json"):
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
    raise AssertionError(url)


def run(body: Callable[[httpx.AsyncClient], Coroutine[Any, Any, Any]]) -> Any:
    async def main() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await body(client)

    return asyncio.run(main())


def test_activations_follow_the_pages() -> None:
    acts = run(lambda c: fetch_activations(c, page_size=3))
    assert [a.code for a in acts][-1] == "EMSR1"
    assert len(acts) == 4


def test_observed_floods_come_from_the_geojson_layers() -> None:
    events = run(lambda c: fetch_observed_events(c, "EMSR762"))
    assert len(events) == 1  # the other layers are empty in this fake
    (only,) = events
    assert only.product.label == "AOI01_DEL_PRODUCT"
    assert len(only.features) == 43


def test_products_of_an_activation() -> None:
    products = run(lambda c: fetch_products(c, "EMSR762"))
    without = [p for p in products if "observedEventA" not in p.layers]
    events = run(lambda c: fetch_observed_events(c, "EMSR762", event_types=None, notations=None))
    assert len(events) == 1
    assert len(products) > len(without)
