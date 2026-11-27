"""The demo's API (demo/api.py), over data saved by init: no network."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import api  # demo/api.py: pythonpath in pyproject.toml
import httpx
import pytest
from shapely.geometry import shape
from starlette.testclient import TestClient
from test_offline import service

from copernicus_ems import RapidMapping, init


def offline(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"the demo asked the network: {request.url}")


@pytest.fixture
def saved(data_dir: Path) -> Path:
    async def body() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
            assert (await init(client=client)).ok

    asyncio.run(body())
    return data_dir


@pytest.fixture
def client(saved: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store = api.Store(saved)
    # everything is on disk: a request to the network is a bug
    store._ems = RapidMapping(
        httpx.AsyncClient(transport=httpx.MockTransport(offline)), directory=saved
    )
    monkeypatch.setattr(api, "store", store)
    with TestClient(api.app) as test:
        yield test


def test_health(client: TestClient, saved: Path) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["offline"] is True
    assert body["directory"] == str(saved)


def test_activations_from_disk_with_a_point(client: TestClient) -> None:
    body = client.get("/api/activations").json()
    assert body["source"] == "disk"
    assert body["country"] == "Italy"
    (row,) = body["activations"]
    assert row["code"] == "EMSR762"
    assert row["saved"] is True
    lon, lat = row["point"]
    assert 11 < lon < 13 and 43.5 < lat < 45


def test_card_numbers_are_those_of_the_library(client: TestClient) -> None:
    card = client.get("/api/activation/emsr762").json()
    assert card["code"] == "EMSR762"
    assert card["saved"] is True
    assert card["without_layer"] == 1
    rows = {r["product"]: r for r in card["products"]}
    first = rows["AOI01_DEL_PRODUCT"]
    assert first["features"] == 43
    assert first["sensors"] == ["PAZ"]
    assert first["flooded_km2"] == pytest.approx(1.19, abs=0.01)
    assert first["observed_km2"] == pytest.approx(1845.8, abs=0.5)
    # the other layers of the fake service are empty: dry, but looked at
    assert card["flooded_km2"] == pytest.approx(1.2, abs=0.05)
    assert card["aois"] == len({r["aoi"] for r in card["products"]})
    assert 'observed_events("EMSR762")' in card["python"]


def test_map_masks_and_water(client: TestClient, saved: Path) -> None:
    answer = client.get("/api/activation/EMSR762/map", headers={"Accept-Encoding": "gzip"})
    assert answer.headers["content-encoding"] == "gzip"
    shapes: dict[str, Any] = answer.json()
    kinds = [f["properties"]["kind"] for f in shapes["features"]]
    assert kinds.count("flood") == 1
    assert kinds.count("mask") == len(client.get("/api/activation/EMSR762").json()["products"])
    # kept on disk, under the layers' versions
    (cached,) = (saved / "demo").glob("EMSR762-*.json")
    assert cached.stat().st_size > 0


def test_a_new_store_reads_what_was_built(
    client: TestClient, saved: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = client.get("/api/activation/EMSR762").json()

    def slow(*_: Any) -> None:
        raise AssertionError("built again")

    monkeypatch.setattr(api, "build", slow)
    again = api.Store(saved)
    monkeypatch.setattr(api, "store", again)
    assert client.get("/api/activation/EMSR762").json() == first


def test_unknown_and_malformed_codes(client: TestClient) -> None:
    missing = client.get("/api/activation/EMSR935")
    assert missing.status_code == 404
    assert "EMSR935" in missing.json()["error"]
    assert client.get("/api/activation/drop-table").status_code == 404


def test_the_service_failing_is_a_502(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    store = api.Store(tmp_path)
    store._ems = RapidMapping(
        httpx.AsyncClient(transport=httpx.MockTransport(down)), directory=tmp_path, retries=0
    )
    monkeypatch.setattr(api, "store", store)
    with TestClient(api.app) as test:
        answer = test.get("/api/activations")
    assert answer.status_code == 502
    assert "503" in answer.json()["error"]


def test_live_when_nothing_is_saved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = api.Store(tmp_path)
    store._ems = RapidMapping(
        httpx.AsyncClient(transport=httpx.MockTransport(service)), directory=tmp_path
    )
    monkeypatch.setattr(api, "store", store)
    with TestClient(api.app) as test:
        body = test.get("/api/activations").json()
        assert body["source"] == "live"
        assert [r["code"] for r in body["activations"]] == ["EMSR762"]
        assert test.get("/api/activation/EMSR762").json()["saved"] is False
    # the layers read live are now on disk
    assert list((tmp_path / "EMSR762").glob("*.geojson"))


# --- radar results: read as they are, beside the Sentinel-1 pairs -------------

PRODUCT = "AOI01_DEL_PRODUCT"


def _inside(client: TestClient) -> tuple[float, float]:
    """A point inside the AOI of the product, from the map the API gives."""
    shapes = client.get("/api/activation/EMSR762/map").json()
    mask = next(
        f
        for f in shapes["features"]
        if f["properties"]["kind"] == "mask" and f["properties"]["product"] == PRODUCT
    )
    point = shape(mask["geometry"]).representative_point()
    return point.x, point.y


def _write(
    saved: Path,
    stem: str,
    product: str = PRODUCT,
    *,
    resolution: float | None = 40.0,
    quality: dict[str, Any] | None = None,
    agreement: dict[str, Any] | None = None,
    at: tuple[float, float] | None = None,
    mtime: float | None = None,
) -> list[Path]:
    folder = saved / "EMSR762" / "sentinel-1"
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if quality is not None:
        body = {
            "activation": "EMSR762",
            "product": product,
            "verdict": "to check",
            "reason": None,
            "flags": [{"code": "much_unmapped", "text": "the radar sees more", "numbers": {}}],
            "gap_hours": 3.5,
            "same_image": False,
            "resolution_m": resolution,
            **quality,
        }
        written.append(folder / f"{stem}_quality.json")
        written[-1].write_text(json.dumps(body))
    if agreement is not None:
        body = {
            "activation": "EMSR762",
            "product": product,
            "computed": True,
            "reason": None,
            "independent": True,
            "gap_hours": 3.5,
            "resolution_m": resolution,
            "km2": {"confirmed": 0.2, "map_only": 1.0, "candidate": 3.0},
            "scores": {"csi": 0.1, "pod": 0.2, "far": 0.3},
            "quality_verdict": "usable",
            **agreement,
        }
        written.append(folder / f"{stem}_agreement.json")
        written[-1].write_text(json.dumps(body))
        if at is not None:
            x, y = at

            def box(dx: float) -> list[list[list[float]]]:
                a, b = x + dx, x + dx + 0.001
                return [[[a, y], [b, y], [b, y + 0.001], [a, y]]]

            geo = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"class": "confirmed", "km2": 0.2},
                        "geometry": {"type": "Polygon", "coordinates": box(0)},
                    },
                    {
                        "type": "Feature",
                        "properties": {"class": "candidate", "km2": 3.0},
                        "geometry": {"type": "Polygon", "coordinates": box(0.002)},
                    },
                ],
            }
            written.append(folder / f"{stem}_agreement.geojson")
            written[-1].write_text(json.dumps(geo))
    for path in written:
        if mtime is not None:
            os.utime(path, (mtime, mtime))
    return written


def test_radar_reads_results_beside_pairs(client: TestClient, saved: Path) -> None:
    _write(saved, "a", quality={}, agreement={}, at=_inside(client))
    body = client.get("/api/activation/EMSR762/radar").json()
    card = client.get("/api/activation/EMSR762").json()
    assert body["checked"] == 1
    assert body["products"] == len(card["products"])
    assert body["command"] == "make radar CODE=EMSR762"
    entry = body["results"][PRODUCT]
    assert entry["verdict"] == "to check"
    assert entry["flags"][0]["code"] == "much_unmapped"
    assert entry["independent"] is True
    assert entry["km2"]["candidate"] == 3.0
    assert entry["scores"]["csi"] == 0.1
    assert entry["computed"] is True
    assert entry["resolution_m"] == 40.0
    assert entry["files"] == ["a_agreement.json", "a_quality.json"]


def test_radar_prefers_40m_then_newest(client: TestClient, saved: Path) -> None:
    _write(saved, "r20", resolution=20.0, quality={"verdict": "usable"}, mtime=3000)
    _write(saved, "r40a", quality={"verdict": "to check"}, mtime=1000)
    got = client.get("/api/activation/EMSR762/radar").json()["results"][PRODUCT]
    assert got["verdict"] == "to check"
    _write(saved, "r40b", quality={"verdict": "unreliable"}, mtime=2000)
    got = client.get("/api/activation/EMSR762/radar").json()["results"][PRODUCT]
    assert got["verdict"] == "unreliable"


def test_radar_only_not_40m_shows_its_reason(client: TestClient, saved: Path) -> None:
    _write(
        saved,
        "r20",
        resolution=20.0,
        quality={"verdict": "not assessable", "reason": "thresholds calibrated at 40 m"},
    )
    entry = client.get("/api/activation/EMSR762/radar").json()["results"][PRODUCT]
    assert entry["verdict"] == "not assessable"
    assert entry["reason"] == "thresholds calibrated at 40 m"
    assert entry["resolution_m"] == 20.0


def test_radar_agreement_without_quality(client: TestClient, saved: Path) -> None:
    _write(saved, "a", agreement={"quality_verdict": "usable"})
    entry = client.get("/api/activation/EMSR762/radar").json()["results"][PRODUCT]
    assert entry["verdict"] == "usable"
    assert entry["flags"] == []
    assert entry["reason"] is None


def test_radar_malformed_file_is_skipped(client: TestClient, saved: Path) -> None:
    _write(saved, "good", quality={})
    folder = saved / "EMSR762" / "sentinel-1"
    (folder / "bad_quality.json").write_text("{not json")
    (folder / "odd_agreement.json").write_text('{"km2": 3}')
    answer = client.get("/api/activation/EMSR762/radar")
    assert answer.status_code == 200
    assert list(answer.json()["results"]) == [PRODUCT]
    assert answer.json()["checked"] == 1


def test_radar_none_on_disk(client: TestClient) -> None:
    body = client.get("/api/activation/EMSR762/radar").json()
    assert body["checked"] == 0
    assert body["results"] == {}
    assert client.get("/api/activation/EMSR762/radar/map").json()["features"] == []


def test_radar_map_filter(client: TestClient, saved: Path) -> None:
    x, y = _inside(client)
    _write(saved, "a", quality={}, agreement={"independent": False}, at=(x, y))
    _write(saved, "b", "AOI02_OTHER", quality={}, agreement={}, at=(x, y))
    both = client.get("/api/activation/EMSR762/radar/map").json()["features"]
    assert {f["properties"]["product"] for f in both} == {PRODUCT, "AOI02_OTHER"}
    assert {f["properties"]["class"] for f in both} == {"confirmed", "candidate"}
    one = client.get(f"/api/activation/EMSR762/radar/map?product={PRODUCT}").json()["features"]
    assert len(one) == 2
    assert all(f["properties"]["product"] == PRODUCT for f in one)
    assert all(f["properties"]["independent"] is False for f in one)
    assert {f["properties"]["km2"] for f in one} == {0.2, 3.0}
    lon, lat = one[0]["geometry"]["coordinates"][0][0]
    assert abs(lon - x) < 0.01 and abs(lat - y) < 0.01
    assert client.get("/api/activation/EMSR762/radar/map?product=nope").json()["features"] == []


def test_radar_files_untouched(client: TestClient, saved: Path) -> None:
    files = _write(saved, "a", quality={}, agreement={}, at=_inside(client))
    before = {p: p.read_bytes() for p in files}
    client.get("/api/activation/EMSR762/radar")
    client.get("/api/activation/EMSR762/radar/map")
    assert {p: p.read_bytes() for p in files} == before
    assert sorted((saved / "EMSR762" / "sentinel-1").iterdir()) == sorted(before)
