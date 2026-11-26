from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("rasterio")

from copernicus_ems import quality


def test_detect_thresholds_and_min_size() -> None:
    vv = np.full((10, 10), 10 ** (-1.0), dtype=np.float32)  # -10 dB: dry
    pre = np.full((10, 10), 10 ** (-1.0), dtype=np.float32)
    vv[0:2, 0:2] = 10 ** (-2.0)  # -20 dB, 4 px: kept
    vv[8, 8] = 10 ** (-2.0)  # 1 px: sieved out
    vv[5:7, 5:7] = 10 ** (-2.0)
    pre[5:7, 5:7] = 10 ** (-2.0)  # dark before: not flood
    got = quality.detect(vv, pre)
    assert got[0:2, 0:2].all() and not got[8, 8] and not got[5:7, 5:7].any()


def test_measure_counts_inside_the_mask_only() -> None:
    labels = np.full((100, 100), 255, np.uint8)
    labels[:, :50] = 0
    labels[10:20, 10:20] = 1
    detected = np.zeros((100, 100), bool)
    detected[10:20, 10:15] = True  # half confirmed
    detected[:, 60:] = True  # outside mask: ignored
    m = quality.measure(detected, labels, np.ones_like(detected), 40.0)
    assert m.coverage == 1.0
    assert m.mapped_km2 == pytest.approx(100 * 0.0016)
    assert m.confirmed_share == pytest.approx(0.5)
    assert m.unmapped_km2 == 0.0


def test_clustered_unmapped_and_extent() -> None:
    labels = np.zeros((200, 200), np.uint8)
    labels[0:10, 0:10] = 1  # water in a corner
    detected = np.zeros((200, 200), bool)
    detected[100:140, 100:140] = True  # 1600 px = 2.56 km2
    m = quality.measure(detected, labels, np.ones_like(detected), 40.0)
    assert m.mapped_extent == pytest.approx((10 / 200) ** 2)
    assert m.unmapped_clustered_km2 == pytest.approx(2.56)
    assert m.unmapped_outside_extent_km2 == pytest.approx(2.56)


def test_nothing_observed_or_no_valid_radar() -> None:
    labels = np.full((10, 10), 255, np.uint8)
    m = quality.measure(np.zeros((10, 10), bool), labels, np.ones((10, 10), bool), 40.0)
    assert m.coverage == 0.0 and m.mapped_km2 == 0.0 and m.confirmed_share == 0.0
    labels[:] = 1
    m = quality.measure(np.zeros((10, 10), bool), labels, np.zeros((10, 10), bool), 40.0)
    assert m.coverage == 0.0
