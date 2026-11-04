from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from test_client import handler

from copernicus_ems.cli import main


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_list_filters_by_country_category_and_date(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", "--category", "flood", "--since", "2026-10-01"], client=client()) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("\t")[0] for line in lines] == ["EMSR933", "EMSR935"]

    assert main(["list", "--country", "Spain", "--json"], client=client()) == 0
    (row,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert row["code"] == "EMSR935"
    assert row["countries"] == ["Spain"]


def test_fetch_saves_the_layers(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["fetch", "EMSR762"], client=client()) == 0
    assert "EMSR762: 43 features in 1 products" in capsys.readouterr().err
    assert list((data_dir / "EMSR762").glob("*.geojson"))


def test_fetch_into_an_explicit_directory(tmp_path: Path) -> None:
    assert main(["--dir", str(tmp_path / "mine"), "fetch", "EMSR762"], client=client()) == 0
    assert list((tmp_path / "mine" / "EMSR762").glob("*.geojson"))


def test_export_tags_features_and_writes_masks(tmp_path: Path) -> None:
    out, masks = tmp_path / "out" / "floods.geojson", tmp_path / "masks.geojson"
    status = main(
        ["export", "EMSR762", "-o", str(out), "--masks", str(masks), "--all-events"],
        client=client(),
    )
    assert status == 0
    features = json.loads(out.read_text())["features"]
    assert len(features) == 43
    assert features[0]["properties"]["activation"] == "EMSR762"
    assert features[0]["properties"]["product"] == "AOI01_DEL_PRODUCT"
    assert features[0]["properties"]["observed_time"].startswith("20")
    assert features[0]["properties"]["event_type"] == "5-Flood"  # original attributes kept
    (mask,) = json.loads(masks.read_text())["features"]
    assert mask["geometry"]["type"] == "Polygon"
    assert mask["properties"]["product"] == "AOI01_DEL_PRODUCT"


def test_export_by_country(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "spain.geojson"
    assert main(["export", "--country", "Spain", "-o", str(out)], client=client()) == 0
    assert "EMSR935" in capsys.readouterr().err  # the fake detail answers EMSR762's products
    assert len(json.loads(out.read_text())["features"]) == 43


@pytest.mark.parametrize(
    "argv",
    [
        ["export", "-o", "x.geojson"],
        ["export", "EMSR762", "--country", "Italy", "-o", "x.geojson"],
    ],
)
def test_export_needs_codes_or_a_country(argv: list[str]) -> None:
    assert main(argv, client=client()) == 2


def test_service_errors_exit_with_one(capsys: pytest.CaptureFixture[str]) -> None:
    broken = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
    )
    assert main(["fetch", "EMSR762"], client=broken) == 1
    assert "did not return JSON" in capsys.readouterr().err
