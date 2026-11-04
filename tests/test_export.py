from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import shapely
from test_cli import client

from copernicus_ems import ProductEvents, parse_detail, parse_layer, select_features
from copernicus_ems.cli import main
from copernicus_ems.export import (
    GEOPARQUET_VERSION,
    mask_features,
    tagged_features,
    write_features,
    write_geoparquet,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def events() -> list[ProductEvents]:
    products = parse_detail((FIXTURES / "detail_EMSR762.json").read_bytes())
    (product,) = [p for p in products if p.label == "AOI01_DEL_PRODUCT"]
    layer = parse_layer(
        (FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json").read_bytes()
    )
    return [ProductEvents(product=product, features=select_features(layer))]


def test_features_are_tagged_without_touching_the_originals(events: list[ProductEvents]) -> None:
    features = tagged_features(events)
    assert len(features) == 43
    assert features[0]["properties"]["activation"] == "EMSR762"
    assert features[0]["properties"]["event_type"] == "5-Flood"
    assert "activation" not in events[0].features[0]["properties"]
    (mask,) = mask_features(events)
    assert mask["geometry"]["type"] == "Polygon"
    assert mask["properties"]["product"] == "AOI01_DEL_PRODUCT"


def test_geoparquet_follows_the_spec(events: list[ProductEvents], tmp_path: Path) -> None:
    path = write_features(tmp_path / "out" / "floods.parquet", tagged_features(events))
    table = pq.read_table(path)
    geo = json.loads(table.schema.metadata[b"geo"])
    assert geo["version"] == GEOPARQUET_VERSION and geo["primary_column"] == "geometry"
    column = geo["columns"]["geometry"]
    assert column["encoding"] == "WKB" and column["geometry_types"] == ["Polygon"]
    minx, miny, maxx, maxy = column["bbox"]
    assert 11 < minx < maxx < 13 and 44 < miny < maxy < 45
    assert table.num_rows == 43
    assert str(table.schema.field("observed_time").type) == "timestamp[us, tz=UTC]"
    assert str(table.schema.field("area").type) == "double"
    shapes = shapely.from_wkb(table.column("geometry").to_pylist())
    assert all(s.is_valid and s.geom_type == "Polygon" for s in shapes)
    assert table.column("activation").to_pylist()[0] == "EMSR762"
    assert not list(tmp_path.rglob("*.part"))


def test_mixed_property_types_become_text(tmp_path: Path) -> None:
    point = {"type": "Point", "coordinates": [1.0, 2.0]}
    features = [
        {"type": "Feature", "geometry": point, "properties": {"x": 1, "y": {"a": 1}}},
        {"type": "Feature", "geometry": point, "properties": {"x": "one", "z": None}},
    ]
    table = pq.read_table(write_geoparquet(tmp_path / "mixed.parquet", features))
    assert table.column("x").to_pylist() == ["1", "one"]
    assert table.column("y").to_pylist()[0] == {"a": 1}  # a struct when types agree
    assert table.column("z").to_pylist() == [None, None]
    assert json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]["geometry_types"] == [
        "Point"
    ]


def test_an_empty_export(tmp_path: Path) -> None:
    table = pq.read_table(write_geoparquet(tmp_path / "empty.geoparquet", []))
    assert table.num_rows == 0
    assert "bbox" not in json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]
    assert json.loads(write_features(tmp_path / "empty.geojson", []).read_text())["features"] == []


def test_the_export_command_writes_geoparquet(tmp_path: Path) -> None:
    out, masks = tmp_path / "e.parquet", tmp_path / "m.parquet"
    assert main(["export", "EMSR762", "-o", str(out), "--masks", str(masks)], client=client()) == 0
    assert pq.read_table(out).num_rows == 43
    assert pq.read_table(masks).column("product").to_pylist() == ["AOI01_DEL_PRODUCT"]
