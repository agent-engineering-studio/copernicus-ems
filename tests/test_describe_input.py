"""The input of a description (src/copernicus_ems/describe_input.py), from disk only."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_offline import service

from copernicus_ems import (
    OBSERVED_EVENT,
    ProductEvents,
    RapidMapping,
    init,
    read_products,
    select_features,
)
from copernicus_ems.describe_input import (
    Source,
    allowed_names,
    allowed_numbers,
    facts,
    read_source,
)
from copernicus_ems.geo import summary

REASON = "A new wave of severe weather hit Emilia-Romagna on 17 May 2023, with 3 deaths."


@pytest.fixture
def saved(data_dir: Path) -> Path:
    def offline(request: httpx.Request) -> httpx.Response:
        return service(request)

    async def body() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
            assert (await init(client=client)).ok

    asyncio.run(body())
    return data_dir


def add_text(saved: Path) -> None:
    path = saved / "EMSR762" / "detail.json"
    body = json.loads(path.read_bytes())
    body["results"][0].update(
        reason=REASON,
        infobulletins=[{"title": "Bulletin 1", "url": "https://example.org/1"}],
        relatedevents=["EMSR664"],
    )
    path.write_text(json.dumps(body))


def quality(
    saved: Path,
    name: str,
    product: str,
    verdict: str,
    resolution: float = 40.0,
    mtime: float = 1000.0,
) -> None:
    folder = saved / "EMSR762" / "sentinel-1"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{product}_{resolution:.0f}m_{name}_quality.json"
    path.write_text(
        json.dumps(
            {
                "activation": "EMSR762",
                "product": product,
                "verdict": verdict,
                "resolution_m": resolution,
            }
        )
    )
    os.utime(path, (mtime, mtime))


def test_source_fields(saved: Path) -> None:
    add_text(saved)
    source = read_source("EMSR762")
    assert source.reason == REASON
    assert source.bulletins == [{"title": "Bulletin 1", "url": "https://example.org/1"}]
    assert source.related == ["EMSR664"]
    assert source.as_dict()["code"] == "EMSR762"


def test_source_without_reason(saved: Path) -> None:
    path = saved / "EMSR762" / "detail.json"
    body = json.loads(path.read_bytes())
    for key in ("reason", "infobulletins", "relatedevents"):
        body["results"][0].pop(key, None)
    path.write_text(json.dumps(body))
    source = read_source("EMSR762")
    assert source.reason == ""
    assert source.bulletins == [] and source.related == []


def test_facts_match_geo_summary(saved: Path) -> None:
    found = facts("EMSR762")
    assert found["name"] == "Flood in Emilia-Romagna"
    assert found["category"] == "Flood"
    assert found["countries"] == ["Italy"]
    assert found["event_time"].startswith("2024-10-18")

    async def events() -> list[ProductEvents]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
            ems = RapidMapping(client, directory=saved)
            out = []
            for product in read_products("EMSR762"):
                layer = await ems.layer(product, OBSERVED_EVENT)
                if layer is not None and product.aoi_extent is not None:
                    out.append(ProductEvents(product, select_features(layer)))
            return out

    # the library's own path, read from the same saved layers
    expected = summary(asyncio.run(events()))
    assert expected
    for name in {row.aoi_name for row in expected}:
        rows = [r for r in expected if r.aoi_name == name]
        assert found["aois"][name]["water_km2"] == round(max(r.flooded_km2 for r in rows), 2)
        assert found["aois"][name]["observed_km2"] == round(max(r.observed_km2 for r in rows))
    first = found["aois"]["Brisighella"]
    assert first["water_km2"] == pytest.approx(1.19, abs=0.01)
    assert first["observed_km2"] == pytest.approx(1846, abs=1)
    assert first["sensors"] == ["PAZ"]
    assert first["maps"] == 1
    assert first["first_map"] == first["last_map"] == "2024-09-20"
    assert found["aois"]["Idice"]["maps"] == 2
    assert found["aois"]["Idice"]["first_map"] == "2024-09-19"
    assert found["aois"]["Idice"]["last_map"] == "2024-09-20"
    assert first["first_map"] <= first["last_map"]


def offline(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"the network was asked: {request.url}")


def test_no_network(saved: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    assert facts("EMSR762")["aois"]


def test_radar_counts(saved: Path) -> None:
    quality(saved, "a", "AOI01_DEL_PRODUCT", "coherent")
    # The newest pair wins, whatever its resolution: one rule, that of reports.newest_pair.
    quality(saved, "b", "AOI01_DEL_PRODUCT", "to check", resolution=10.0, mtime=2000.0)
    quality(saved, "c", "AOI02_DEL_PRODUCT", "to check")
    quality(saved, "d", "AOI03_DEL_PRODUCT", "not assessable")
    found = facts("EMSR762")
    totals = {"confirms": 0, "to_check": 0, "not_assessable": 0}
    for aoi in found["aois"].values():
        for k in totals:
            totals[k] += aoi["radar"][k]
    assert totals == {"confirms": 0, "to_check": 2, "not_assessable": 1}
    assert found["aois"]["Brisighella"]["radar"] == {
        "confirms": 0,
        "to_check": 1,
        "not_assessable": 0,
    }


def test_no_quality_files_counts_zero(saved: Path) -> None:
    for aoi in facts("EMSR762")["aois"].values():
        assert aoi["radar"] == {"confirms": 0, "to_check": 0, "not_assessable": 0}


def test_allowed_numbers_and_names(saved: Path) -> None:
    add_text(saved)
    found = facts("EMSR762")
    source = read_source("EMSR762")
    numbers = allowed_numbers(found, source)
    assert {
        "1.19",
        "1,19",
        "1846",
        "2024",
        "18",
        "2024-10-18",
        "2024-09-20",
        "20",
        "17",
        "2023",
        "3",
    } <= numbers
    names = allowed_names(found, source)
    assert {"Brisighella", "Idice", "PAZ", "Flood", "Emilia-Romagna", "A", "Italy"} <= names
    assert isinstance(source, Source)


def test_thousands_forms_total_and_hyphen_parts(saved: Path) -> None:
    add_text(saved)
    found = facts("EMSR762")
    source = read_source("EMSR762")
    numbers = allowed_numbers(found, source)
    assert {
        "1.846",
        "1,846",
        "1 846",
        "1" + chr(0xA0) + "846",
        "1" + chr(0x2009) + "846",
        "664",
    } <= numbers
    total = round(sum(r["water_km2"] for r in found["aois"].values()), 2)
    assert f"{total:.2f}".replace(".", ",") in numbers
    names = allowed_names(found, source)
    assert {"Emilia-Romagna", "Emilia", "Romagna", "EMSR664"} <= names


def test_bulletin_title_words_are_names(saved: Path) -> None:
    add_text(saved)
    path = saved / "EMSR762" / "detail.json"
    body = json.loads(path.read_bytes())
    body["results"][0]["infobulletins"] = [{"title": "Report on Santerno", "url": "u"}]
    path.write_text(json.dumps(body))
    assert "Santerno" in allowed_names(facts("EMSR762"), read_source("EMSR762"))


def test_newest_wins_at_equal_resolution(saved: Path) -> None:
    quality(saved, "old", "AOI01_DEL_PRODUCT", "coherent", mtime=1.0)
    quality(saved, "new", "AOI01_DEL_PRODUCT", "to check")
    radar = facts("EMSR762")["aois"]["Brisighella"]["radar"]
    assert radar["to_check"] == 1 and radar["confirms"] == 0


def test_malformed_quality_file_is_skipped(saved: Path) -> None:
    quality(saved, "ok", "AOI01_DEL_PRODUCT", "coherent")
    folder = saved / "EMSR762" / "sentinel-1"
    (folder / "bad_quality.json").write_text("{not json")
    (folder / "list_quality.json").write_text("[1]")
    assert facts("EMSR762")["aois"]["Brisighella"]["radar"]["confirms"] == 1


def test_detail_without_results_is_an_ems_error(saved: Path) -> None:
    from copernicus_ems import EmsError

    (saved / "EMSR762" / "detail.json").write_text(json.dumps({"results": []}))
    with pytest.raises(EmsError):
        read_source("EMSR762")
