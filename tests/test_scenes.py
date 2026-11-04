from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_client import handler as ems_handler

from copernicus_ems import (
    SENTINEL_1_GRD,
    SENTINEL_2_L2A,
    STAC_SEARCH_URL,
    EmsError,
    Product,
    ProductEvents,
    RapidMapping,
    fetch_scenes,
    find_scenes,
    parse_detail,
    parse_layer,
    parse_scenes,
    passes,
    scene_search_body,
    select_features,
)
from copernicus_ems.cli import main
from copernicus_ems.geo import coverage

FIXTURES = Path(__file__).parent / "fixtures"
STAC = FIXTURES / "stac_EMSR762_AOI01.json"


def stac_handler(request: httpx.Request) -> httpx.Response:
    if str(request.url) == STAC_SEARCH_URL:
        wanted = set(json.loads(request.content)["collections"])
        page = json.loads(STAC.read_text())
        page["features"] = [f for f in page["features"] if f["collection"] in wanted]
        return httpx.Response(200, json=page)
    return ems_handler(request)


def product() -> Product:
    products = parse_detail((FIXTURES / "detail_EMSR762.json").read_bytes())
    (found,) = [p for p in products if p.label == "AOI01_DEL_PRODUCT"]
    return found


def event() -> ProductEvents:
    layer = parse_layer(
        (FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json").read_bytes()
    )
    return ProductEvents(product=product(), features=select_features(layer))


def run(body: Any, transport: Any = stac_handler) -> Any:
    async def go() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await body(client)

    return asyncio.run(go())


def test_the_search_body() -> None:
    start = datetime(2024, 9, 18, 5, 27, tzinfo=UTC)
    body = scene_search_body(
        {"type": "Point", "coordinates": [1, 2]}, start, start + timedelta(days=4)
    )
    assert body["collections"] == [SENTINEL_1_GRD]
    assert body["datetime"] == "2024-09-18T05:27:00Z/2024-09-22T05:27:00Z"
    assert body["intersects"]["type"] == "Point"
    naive = scene_search_body({}, datetime(2024, 1, 1), datetime(2024, 1, 2), collections=["x"])
    assert naive["datetime"] == "2024-01-01T00:00:00Z/2024-01-02T00:00:00Z"
    with pytest.raises(ValueError, match="before"):
        scene_search_body({}, start, start - timedelta(seconds=1))


def test_parse_a_real_stac_page() -> None:
    scenes, following = parse_scenes(STAC.read_bytes())
    assert following is None
    assert len(scenes) == 8
    radar = [s for s in scenes if s.collection == SENTINEL_1_GRD]
    assert {s.platform for s in radar} == {"sentinel-1a"}
    assert radar[0].polarizations == ("VV", "VH") and radar[0].cloud_cover is None
    assert radar[0].orbit_state in {"ascending", "descending"}
    assert radar[0].download_url and radar[0].download_url.startswith("https://")
    optical = [s for s in scenes if s.collection == SENTINEL_2_L2A]
    assert all(s.cloud_cover is not None for s in optical)
    assert len({s.datatake_id for s in optical}) == 2  # two passes, six tiles


def test_rows_without_time_or_geometry_are_skipped() -> None:
    body = {
        "features": [
            {"id": "a", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
            {"id": "b", "properties": {"datetime": "2024-01-01T00:00:00Z"}, "geometry": None},
        ],
        "links": [{"rel": "next", "href": "https://x/next"}],
    }
    scenes, following = parse_scenes(json.dumps(body))
    assert scenes == [] and following == {"rel": "next", "href": "https://x/next"}
    with pytest.raises(EmsError):
        parse_scenes('{"type": "nope"}')
    with pytest.raises(EmsError):
        parse_scenes("<html>")


def test_nearest_scenes_first_and_cloud_filter() -> None:
    matches = run(lambda c: find_scenes(c, product(), collections=[SENTINEL_1_GRD, SENTINEL_2_L2A]))
    gaps = [abs(m.gap) for m in matches]
    assert gaps == sorted(gaps)
    assert len(matches) == 8
    assert all(m.gap == m.scene.time - product().observed_time for m in matches)
    clear = run(
        lambda c: find_scenes(
            c, product(), collections=[SENTINEL_1_GRD, SENTINEL_2_L2A], max_cloud_cover=50
        )
    )
    assert {m.scene.id[:3] for m in clear} == {"S1A", "S2A"}  # the cloudy S2B pass is gone
    assert all(m.scene.cloud_cover is None or m.scene.cloud_cover <= 50 for m in clear)
    assert isinstance(clear[0].gap_hours, float)


def test_passes_group_tiles_and_cover_the_aoi() -> None:
    matches = run(lambda c: find_scenes(c, product(), collections=[SENTINEL_2_L2A]))
    grouped = passes(matches)
    # observed 20 Sep 05:27: the 19 Sep pass (4 tiles, -19 h) before the 21 Sep one (2, +28 h)
    assert [len(p.scenes) for p in grouped] == [4, 2]
    first = grouped[0]
    assert first.time == first.scenes[0].time and -20 < first.gap_hours < -19
    assert first.cloud_cover == pytest.approx(100.0)  # the cloudiest tile of the pass
    assert first.geometry["type"] == "GeometryCollection"
    whole = coverage(first.geometry, event())
    one_tile = max(coverage(s.geometry, event()) for s in first.scenes)
    assert whole >= one_tile and 0 < whole <= 1
    radar = passes(run(lambda c: find_scenes(c, product())))
    assert all(p.cloud_cover is None for p in radar)


def test_coverage_of_nothing_and_of_everything() -> None:
    far = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    world = {
        "type": "Polygon",
        "coordinates": [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]],
    }
    assert coverage(far, event()) == 0
    assert coverage(world, event()) == pytest.approx(1)


def test_products_without_time_or_aoi_cannot_be_searched() -> None:
    with pytest.raises(EmsError, match="observation time"):
        run(lambda c: find_scenes(c, replace(product(), acquisitions=())))
    with pytest.raises(EmsError, match="AOI"):
        run(lambda c: find_scenes(c, replace(product(), aoi_extent_wkt=None)))


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_pages_are_followed(method: str) -> None:
    calls: list[tuple[str, str]] = []
    one = json.loads(STAC.read_text())["features"][0]

    def paged(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if len(calls) == 1:
            link: dict[str, Any] = {
                "rel": "next",
                "href": "https://stac.example/page2",
                "method": method,
            }
            if method == "POST":
                link |= {"body": {"token": "abc"}, "merge": True}
            return httpx.Response(200, json={"features": [one], "links": [link]})
        if method == "POST":
            assert json.loads(request.content)["token"] == "abc"
            assert "collections" in json.loads(request.content)  # merged
        return httpx.Response(200, json={"features": [{**one, "id": "other"}], "links": []})

    start = datetime(2024, 9, 18, tzinfo=UTC)
    scenes = run(lambda c: fetch_scenes(c, {}, start, start + timedelta(days=1)), paged)
    assert [s.id for s in scenes] == [one["id"], "other"]
    assert calls[1] == (method, "https://stac.example/page2")


def test_a_replaced_body_on_the_next_page() -> None:
    one = json.loads(STAC.read_text())["features"][0]
    bodies: list[dict[str, Any]] = []

    def paged(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        link = {"rel": "next", "href": STAC_SEARCH_URL, "method": "POST", "body": {"page": 2}}
        return httpx.Response(
            200, json={"features": [one], "links": [link] if len(bodies) == 1 else []}
        )

    start = datetime(2024, 9, 18, tzinfo=UTC)
    run(lambda c: fetch_scenes(c, {}, start, start + timedelta(days=1)), paged)
    assert bodies[1] == {"page": 2}


def test_rapid_mapping_finds_scenes() -> None:
    matches = run(lambda c: RapidMapping(c).scenes(product(), within=timedelta(days=2)))
    assert matches and matches[0].scene.collection == SENTINEL_1_GRD


def test_the_scenes_command(capsys: pytest.CaptureFixture[str]) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(stac_handler))
    assert main(["scenes", "EMSR762", "--aoi", "1", "--optical", "--top", "2"], client=client) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("AOI01_DEL_PRODUCT") and "made from PAZ (HR+)" in out[0]
    assert "AOI " in out[1] and "sentinel" in out[1]

    client = httpx.AsyncClient(transport=httpx.MockTransport(stac_handler))
    assert main(["scenes", "EMSR762", "--aoi", "1", "--optical", "--json"], client=client) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {r["collection"] for r in rows} == {SENTINEL_1_GRD, SENTINEL_2_L2A}
    assert all(0 <= r["aoi_coverage"] <= 1 for r in rows)
    assert any(r["cloud_cover"] is not None for r in rows)
