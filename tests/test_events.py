from __future__ import annotations

from pathlib import Path

import pytest

from copernicus_ems import EmsError, parse_layer, select_features

FIXTURES = Path(__file__).parent / "fixtures"
LAYER = FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json"


def test_the_observed_layer_is_geojson_in_lon_lat() -> None:
    collection = parse_layer(LAYER.read_bytes())
    assert len(collection["features"]) == 43
    lon, lat = collection["features"][0]["geometry"]["coordinates"][0][0][:2]
    assert 11 < lon < 13 and 44 < lat < 45


def test_floods_are_selected_by_event_type_and_notation() -> None:
    collection = parse_layer(LAYER.read_bytes())
    assert len(select_features(collection)) == 43
    collection["features"].extend(
        [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {"event_type": "6-Mass Movement", "notation": "Landslide"},
            },
            {
                "type": "Feature",
                "geometry": None,
                "properties": {"event_type": "5-Flood", "notation": "Flooded area"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {"event_type": "5-Flood", "notation": "Flood trace"},
            },
        ]
    )
    assert len(select_features(collection)) == 44
    assert len(select_features(collection, notations=["Flood trace"])) == 1
    assert len(select_features(collection, event_types=None, notations=None)) == 45


@pytest.mark.parametrize("body", [b"<html>", b"[]", b'{"type": "Feature"}'])
def test_a_layer_that_is_not_a_feature_collection_raises(body: bytes) -> None:
    with pytest.raises(EmsError):
        parse_layer(body)
