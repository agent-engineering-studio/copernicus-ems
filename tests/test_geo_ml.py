from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from copernicus_ems import EmsError, ProductEvents, parse_detail, parse_layer, select_features
from copernicus_ems.geo import area_km2, flooded, observed_dry, summary
from copernicus_ems.ml import (
    DRY,
    FLOODED,
    IGNORE,
    LabelRaster,
    Scores,
    burn,
    chips,
    evaluate,
    evaluate_geometry,
    grid_for,
    rasterize,
    utm_crs,
)

FIXTURES = Path(__file__).parent / "fixtures"
LAYER = FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json"


@pytest.fixture(scope="module")
def event() -> ProductEvents:
    products = parse_detail((FIXTURES / "detail_EMSR762.json").read_bytes())
    (product,) = [p for p in products if p.label == "AOI01_DEL_PRODUCT"]
    return ProductEvents(product=product, features=select_features(parse_layer(LAYER.read_bytes())))


@pytest.fixture(scope="module")
def truth(event: ProductEvents) -> LabelRaster:
    return rasterize(event, 50)


def published_km2(event: ProductEvents) -> float:
    # Rapid Mapping's own ``area`` attribute is in hectares.
    return float(sum(f["properties"]["area"] for f in event.features)) / 100


def test_geodesic_areas_match_the_published_ones(event: ProductEvents) -> None:
    (s,) = summary([event])
    assert s.features == 43
    assert s.flooded_km2 == pytest.approx(published_km2(event), rel=0.01)  # 1.19 km²
    assert 1700 < s.observed_km2 < 2000  # the ~1,850 km² AOI
    assert 0 < s.flooded_share < 0.001
    assert s.bbox is not None and 11 < s.bbox[0] < s.bbox[2] < 13
    assert area_km2(observed_dry(event)) == pytest.approx(s.observed_km2 - s.flooded_km2, rel=1e-6)
    assert area_km2(event.product.aoi_extent or {}) == pytest.approx(s.observed_km2)


def test_an_empty_event_has_no_bbox(event: ProductEvents) -> None:
    (s,) = summary([replace(event, features=[])])
    assert s.flooded_km2 == 0 and s.bbox is None and s.flooded_share == 0


def test_a_product_without_aoi_cannot_be_measured(event: ProductEvents) -> None:
    blind = replace(event, product=replace(event.product, aoi_extent_wkt=None))
    with pytest.raises(EmsError, match="no AOI"):
        flooded(blind)


def test_utm_zones() -> None:
    assert utm_crs(11.9, 44.3) == "EPSG:32632"
    assert utm_crs(-70.6, -33.4) == "EPSG:32719"
    assert utm_crs(180.0, 0.0) == "EPSG:32660"


def test_the_label_raster_has_three_states(event: ProductEvents, truth: LabelRaster) -> None:
    labels = truth.labels
    assert truth.grid.crs == "EPSG:32633"  # the AOI centre is just east of 12°E
    assert set(np.unique(labels)) == {DRY, FLOODED, IGNORE}
    px = truth.grid.pixel_area_km2
    assert np.count_nonzero(labels == FLOODED) * px == pytest.approx(published_km2(event), rel=0.15)
    assert 1700 < np.count_nonzero(labels != IGNORE) * px < 2000
    assert truth.meta["product"] == "AOI01_DEL_PRODUCT"
    assert "European Union" in truth.meta["source"]
    assert truth.grid.transform[0] == 50 and truth.grid.transform[4] == -50


def test_a_perfect_prediction(truth: LabelRaster) -> None:
    s = evaluate(truth.labels == FLOODED, truth)
    assert (s.csi, s.iou, s.pod, s.far, s.f1, s.bias) == (1, 1, 1, 0, 1, 1)
    assert s.unobserved_predicted == 0


def test_predicting_water_everywhere(truth: LabelRaster) -> None:
    s = evaluate(np.ones_like(truth.labels), truth)
    labels = truth.labels
    assert s.hits == np.count_nonzero(labels == FLOODED)
    assert s.false_alarms == np.count_nonzero(labels == DRY)
    # outside the AOI it is reported, not counted as a false alarm
    assert s.unobserved_predicted == np.count_nonzero(labels == IGNORE) > 0
    assert s.unobserved_predicted_km2 == pytest.approx(s.unobserved_predicted * 0.0025)
    assert s.pod == 1 and s.far > 0.99 and s.correct_negatives == 0
    assert set(s.as_dict()) >= {"csi", "pod", "far", "unobserved_predicted_km2"}


def test_predicting_nothing(truth: LabelRaster) -> None:
    s = evaluate(np.zeros_like(truth.labels), truth)
    assert s.pod == 0 and s.csi == 0 and math.isnan(s.far)


def test_a_prediction_outside_the_aoi_is_not_a_false_alarm(event: ProductEvents) -> None:
    far_away = {"type": "Polygon", "coordinates": [[[0, 0], [0.1, 0], [0.1, 0.1], [0, 0]]]}
    s = evaluate_geometry(far_away, event, 100)
    assert s.false_alarms == 0 and s.hits == 0 and s.unobserved_predicted == 0


def test_scoring_polygons(event: ProductEvents) -> None:
    layer = json.loads(LAYER.read_text())
    s = evaluate_geometry(layer, event, 50)  # the truth itself, as a FeatureCollection
    assert s.csi == 1
    s = evaluate_geometry(event.features[0], event, 50)  # one Feature of 43
    assert 0 < s.pod < 1 and s.far == 0
    s = evaluate_geometry(event.product.aoi_extent or {}, event, 50)  # the AOI as the flood
    assert s.pod == 1 and s.far > 0.99


def test_scores_pool_and_refuse_mixed_resolutions() -> None:
    a = Scores(1, 2, 3, 4, 5, 0.0004)
    total = sum([a, a], Scores.zero())
    assert total == Scores(2, 4, 6, 8, 10, 0.0004)
    with pytest.raises(ValueError, match="resolutions"):
        a + Scores(1, 1, 1, 1, 1, 0.01)


def test_bad_inputs(event: ProductEvents, truth: LabelRaster) -> None:
    with pytest.raises(ValueError, match="resolution"):
        grid_for(event, 0)
    with pytest.raises(ValueError, match="shape"):
        evaluate(np.zeros((2, 2)), truth)
    with pytest.raises(ValueError, match="size"):
        next(chips(truth, 0))


def test_burning_nothing_or_off_grid(truth: LabelRaster) -> None:
    from shapely.geometry import Polygon

    assert not burn(Polygon(), truth.grid).any()
    assert not burn({"type": "Point", "coordinates": [0, 0]}, truth.grid).any()


def test_chips_cover_the_observed_area(truth: LabelRaster, tmp_path: Path) -> None:
    every = list(chips(truth, 128, min_observed=0.0))
    assert all(c.labels.shape == (128, 128) for c in every)
    assert sum(np.count_nonzero(c.labels == FLOODED) for c in every) == np.count_nonzero(
        truth.labels == FLOODED
    )
    wet = list(chips(truth, 128, min_flooded=0.0001))
    assert 0 < len(wet) < len(every)
    first = wet[0]
    assert first.grid.x0 == truth.grid.x0 + first.col * 50
    path = first.save(tmp_path / "chips")
    assert np.array_equal(np.load(path), first.labels)
    sidecar = json.loads(path.with_suffix(".json").read_text())
    assert sidecar["crs"] == "EPSG:32633"
    assert sidecar["values"] == {"flooded": 1, "dry": 0, "ignore": 255}
    assert sidecar["activation"] == "EMSR762"
    overlapping = list(chips(truth, 128, stride=64, min_observed=0.0))
    assert len(overlapping) > len(every)
