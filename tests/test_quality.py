from __future__ import annotations

import json
from dataclasses import replace

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


def test_sieve_never_fills_holes() -> None:
    vv = np.full((10, 10), 10 ** (-2.0), dtype=np.float32)  # all wet now
    pre = np.full((10, 10), 10 ** (-1.0), dtype=np.float32)
    vv[5, 5] = 10 ** (-1.0)  # dry speckle inside the flood
    assert not quality.detect(vv, pre)[5, 5]

    labels = np.zeros((100, 100), np.uint8)
    labels[0:2, 0:2] = 1
    detected = np.zeros((100, 100), bool)
    detected[50:90, 50:90] = True  # 1600 px
    detected[70, 70] = False  # hole
    m = quality.measure(detected, labels, np.ones_like(detected), 40.0)
    assert m.unmapped_clustered_km2 == pytest.approx(1599 * 0.0016)


GOOD = quality.Measurements(
    coverage=1.0,
    mapped_km2=10.0,
    confirmed_share=0.4,
    unmapped_km2=5.0,
    unmapped_clustered_km2=2.0,
    mapped_extent=0.5,
    unmapped_outside_extent_km2=1.0,
)
META: dict[str, object] = {
    "activation": "EMSR1",
    "product": "AOI01_DEL_PRODUCT",
    "gap_hours": 5.0,
    "made_from": [{"sensor": "Pleiades-1A", "time": "2024-01-01T00:00:00+00:00"}],
}


def _assess(
    meta: dict[str, object] | None = None,
    m: quality.Measurements | None = GOOD,
    *,
    resolution_m: float | None = 40,
    has_reference: bool = True,
) -> quality.QualityReport:
    return quality.assess(
        META if meta is None else meta, m, resolution_m=resolution_m, has_reference=has_reference
    )


def test_coherent() -> None:
    report = _assess()
    assert report.verdict == quality.COHERENT
    assert report.flags == ()
    assert report.reason is None
    assert report.activation == "EMSR1"
    assert report.gap_hours == 5.0


def test_possibly_partial() -> None:
    m = replace(GOOD, mapped_extent=0.01, unmapped_outside_extent_km2=30.0)
    report = _assess(m=m)
    assert report.verdict == quality.TO_CHECK
    assert [f.code for f in report.flags] == ["possibly_partial"]


def test_much_unmapped() -> None:
    report = _assess(m=replace(GOOD, unmapped_clustered_km2=40.0))
    assert report.verdict == quality.TO_CHECK
    assert [f.code for f in report.flags] == ["much_unmapped"]


def test_little_confirmed() -> None:
    report = _assess(m=replace(GOOD, confirmed_share=0.05))
    assert report.verdict == quality.TO_CHECK
    assert [f.code for f in report.flags] == ["little_confirmed"]


@pytest.mark.parametrize(
    "change, reason_start",
    [
        (
            {"made_from": [{"sensor": "Sentinel-1A", "time": "x"}]},
            "the map was made from Sentinel-1",
        ),
        ({"gap_hours": 30.0}, "radar pass +30.0 h"),
        ({"gap_hours": None}, "no pass time"),
    ],
)
def test_not_assessable_from_the_manifest(change: dict[str, object], reason_start: str) -> None:
    report = _assess({**META, **change})
    assert report.verdict == quality.NOT_ASSESSABLE
    assert report.reason is not None and report.reason.startswith(reason_start)
    assert report.flags == ()


def test_not_assessable_without_reference_resolution_coverage_or_water() -> None:
    cases = [
        (_assess(has_reference=False), "no pre-event reference"),
        (_assess(resolution_m=20), "thresholds calibrated at 40 m"),
        (_assess(m=replace(GOOD, coverage=0.5)), "radar covers 50% of the observed area"),
        (_assess(m=replace(GOOD, mapped_km2=0.05)), "too little mapped water"),
    ]
    for report, reason_start in cases:
        assert report.verdict == quality.NOT_ASSESSABLE
        assert report.reason is not None and report.reason.startswith(reason_start)
        assert report.flags == ()


def test_missing_made_from_is_not_same_image() -> None:
    meta = {k: v for k, v in META.items() if k != "made_from"}
    assert quality.same_image(meta) is False
    assert _assess(meta).verdict == quality.COHERENT


def test_report_as_dict_is_json() -> None:
    report = _assess(m=replace(GOOD, confirmed_share=0.05))
    data = json.loads(json.dumps(report.as_dict()))
    assert data["verdict"] == "to check"
    assert data["reason"] is None
    assert data["flags"][0]["code"] == "little_confirmed"
    assert data["measurements"]["coverage"] == 1.0
    assert data["detector"]["vv_db"] == quality.VV_DB
