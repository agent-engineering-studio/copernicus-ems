"""The demo's API (demo/api.py), over data saved by init: no network."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import api  # demo/api.py: pythonpath in pyproject.toml
import httpx
import pytest
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
