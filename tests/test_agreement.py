from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("rasterio")

from copernicus_ems import quality
from copernicus_ems.agreement import (
    CANDIDATE,
    CLASS_NAMES,
    CONFIRMED,
    DRY,
    MAP_ONLY,
    NOT_OBSERVED,
    RADAR_SCATTERED,
    class_km2,
    classify,
)


def _scene() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    labels = np.zeros((60, 60), np.uint8)
    labels[0, :] = 255
    labels[5:15, 5:15] = 1  # mapped block
    detected = np.zeros((60, 60), bool)
    detected[5:15, 5:10] = True  # half of the mapped block
    detected[30:56, 30:56] = True  # 26x26 = 676 px >= 1 km2, far away
    detected[5:7, 17:19] = True  # 4 px patch next to the mapped block
    return detected, labels, np.ones((60, 60), bool)


def test_classify_each_class() -> None:
    detected, labels, valid = _scene()
    got = classify(detected, labels, valid, 40.0)
    assert (got[5:15, 5:10] == CONFIRMED).all()
    assert (got[5:15, 10:15] == MAP_ONLY).all()
    assert (got[30:56, 30:56] == CANDIDATE).all()
    assert (got[5:7, 17:19] == RADAR_SCATTERED).all()
    assert (got[0, :] == NOT_OBSERVED).all()
    assert got[20, 20] == DRY


def test_invalid_radar_is_not_observed() -> None:
    detected, labels, valid = _scene()
    valid[:, 7] = False
    got = classify(detected, labels, valid, 40.0)
    assert (got[:, 7] == NOT_OBSERVED).all()


def test_candidate_matches_quality_clustered() -> None:
    detected, labels, valid = _scene()
    got = classify(detected, labels, valid, 40.0)
    m = quality.measure(detected, labels, valid, 40.0)
    assert class_km2(got, 40.0)["candidate"] == pytest.approx(m.unmapped_clustered_km2)


def test_no_mapped_water() -> None:
    detected, labels, valid = _scene()
    labels[labels == 1] = 0
    out = class_km2(classify(detected, labels, valid, 40.0), 40.0)
    assert out["candidate"] > 0
    assert out["confirmed"] == 0 and out["map_only"] == 0


def test_class_km2_names_and_values() -> None:
    classes = np.zeros((10, 10), np.uint8)
    classes[:2, :5] = CONFIRMED
    out = class_km2(classes, 40.0)
    assert (
        set(out)
        == set(CLASS_NAMES.values())
        == {"dry", "confirmed", "map_only", "candidate", "radar_scattered"}
    )
    assert out["confirmed"] == pytest.approx(10 * 0.04**2)
