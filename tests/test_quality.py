from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("rasterio")

import rasterio
from pairs import add_reference, write_pair

from copernicus_ems import EmsError, quality
from copernicus_ems.cli import main
from copernicus_ems.dataset import find_pairs
from copernicus_ems.ml import Grid, LabelRaster


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
    report = _assess(m=replace(GOOD, confirmed_share=0.005))
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
    report = _assess(m=replace(GOOD, confirmed_share=0.005))
    data = json.loads(json.dumps(report.as_dict()))
    assert data["verdict"] == "to check"
    assert data["reason"] is None
    assert data["flags"][0]["code"] == "little_confirmed"
    assert data["measurements"]["coverage"] == 1.0
    assert data["detector"]["vv_db"] == quality.VV_DB


# --- check_pair on disk ---


def test_check_pair_with_reference(tmp_path: Path, labels: LabelRaster) -> None:
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    add_reference(tmp_path / "EMSR1/sentinel-1/AOI01_DEL_PRODUCT_pair.json", 0.1)
    (report,) = quality.check_pairs(tmp_path)
    assert report.verdict == "not assessable"
    assert "calibrated at 40 m" in (report.reason or "")
    assert report.resolution_m == 100
    assert report.inputs["reference"] == "AOI01_DEL_PRODUCT_pre.tif"
    written = tmp_path / "EMSR1/sentinel-1/AOI01_DEL_PRODUCT_quality.json"
    assert json.loads(written.read_text())["verdict"] == "not assessable"


def test_check_pair_at_40m_coherent(tmp_path: Path) -> None:
    grid = Grid("EPSG:32633", 500000.0, 4600000.0, 40.0, 100, 100)
    arr = np.zeros((100, 100), np.uint8)
    arr[20:60, 20:60] = 1
    raster = LabelRaster(arr, grid, {})
    manifest = write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", raster, gap_hours=5)
    folder = manifest.parent
    image = np.full((3, 100, 100), 0.1, np.float32)
    image[2] = 1
    pre = image.copy()
    image[:2, 20:60, 20:60] = 0.01
    meta = json.loads(manifest.read_text())
    with rasterio.open(folder / meta["image"]) as src:
        profile = src.profile
    with rasterio.open(folder / meta["image"], "w", **profile) as dst:
        dst.write(image)
    with rasterio.open(folder / "AOI01_DEL_PRODUCT_pre.tif", "w", **profile) as dst:
        dst.write(pre)
    meta["reference"] = {"image": "AOI01_DEL_PRODUCT_pre.tif"}
    manifest.write_text(json.dumps(meta))
    (pair,) = find_pairs(tmp_path, None)
    report = quality.check_pair(pair)
    assert report.reason is None
    assert report.verdict == "coherent"
    assert report.resolution_m == 40.0


def test_check_pair_without_reference(tmp_path: Path) -> None:
    grid = Grid("EPSG:32633", 500000.0, 4600000.0, 40.0, 50, 50)
    raster = LabelRaster(np.zeros((50, 50), np.uint8), grid, {})
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", raster, gap_hours=5)
    (pair,) = find_pairs(tmp_path, None)
    report = quality.check_pair(pair)
    assert report.verdict == "not assessable"
    assert (report.reason or "").startswith("no pre-event reference")
    assert "reference" not in report.inputs


def test_reference_on_another_grid(tmp_path: Path, labels: LabelRaster) -> None:
    manifest = write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    pre = add_reference(manifest, 0.1)
    with rasterio.open(pre) as src:
        profile, data = src.profile, src.read()
    profile.update(height=profile["height"] - 1)
    with rasterio.open(pre, "w", **profile) as dst:
        dst.write(data[:, :-1])
    (pair,) = find_pairs(tmp_path, None)
    with pytest.raises(EmsError, match=r"_pre\.tif"):
        quality.check_pair(pair)


def test_check_pairs_writes_and_rewrites(tmp_path: Path, labels: LabelRaster) -> None:
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    write_pair(tmp_path, "EMSR2", "AOI02_GRA_PRODUCT", labels)
    quality.check_pairs(tmp_path)
    files = sorted(tmp_path.rglob("*_quality.json"))
    first = [f.read_text() for f in files]
    quality.check_pairs(tmp_path)
    assert len(files) == 2
    assert [f.read_text() for f in files] == first


def test_quality_command_text(
    tmp_path: Path, labels: LabelRaster, capsys: pytest.CaptureFixture[str]
) -> None:
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    write_pair(tmp_path, "EMSR1", "AOI02_DEL_PRODUCT", labels)
    assert main(["--dir", str(tmp_path), "quality", "EMSR1"]) == 0
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert len(lines) == 2 and lines[0].split("\t")[0] == "AOI01_DEL_PRODUCT"
    assert " coherent, " in out.err and " not assessable" in out.err
    assert main(["--dir", str(tmp_path), "quality", "EMSR1", "--aoi", "2"]) == 0
    assert capsys.readouterr().out.startswith("AOI02_DEL_PRODUCT")
    assert main(["--dir", str(tmp_path), "quality", "EMSR1", "--product", "AOI01_DEL_PRODUCT"]) == 0
    assert capsys.readouterr().out.startswith("AOI01_DEL_PRODUCT")


def test_quality_command_json(
    tmp_path: Path, labels: LabelRaster, capsys: pytest.CaptureFixture[str]
) -> None:
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    assert main(["--dir", str(tmp_path), "quality", "EMSR1", "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 1 and "verdict" in rows[0]


def test_quality_command_no_pairs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--dir", str(tmp_path), "quality", "EMSR9"]) == 1
    assert "run copernicus-ems quality EMSR9 --fetch" in capsys.readouterr().err


def test_quality_command_twice_same_file(tmp_path: Path, labels: LabelRaster) -> None:
    manifest = write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    argv = ["--dir", str(tmp_path), "quality", "EMSR1"]
    assert main(argv) == 0
    written = manifest.parent / "AOI01_DEL_PRODUCT_quality.json"
    first = written.read_text()
    assert main(argv) == 0
    assert written.read_text() == first


def test_quality_command_fetch(
    tmp_path: Path,
    labels: LabelRaster,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copernicus_ems import cli

    seen: list[Any] = []

    async def fake(ems: object, args: object) -> int:
        seen.append(args)
        return 0

    monkeypatch.setattr(cli, "_pairs", fake)
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    argv = ["--dir", str(tmp_path), "quality", "EMSR1", "--fetch"]
    assert main([*argv, "--dry-run"]) == 0
    assert capsys.readouterr().out == "" and len(seen) == 1
    ns = seen[0]
    assert ns.resolution == 40.0 and ns.reference is True and ns.dry_run is True
    assert main(argv) == 0
    assert "AOI01_DEL_PRODUCT" in capsys.readouterr().out and len(seen) == 2
    monkeypatch.setattr(cli, "_pairs", lambda ems, args: _failing())
    assert main(argv) == 1


async def _failing() -> int:
    return 1


# --- filters applied before checking ---


def _broken_reference(tmp_path: Path, labels: LabelRaster, product: str) -> Path:
    manifest = write_pair(tmp_path, "EMSR1", product, labels)
    pre = add_reference(manifest, 0.1)
    with rasterio.open(pre) as src:
        profile, data = src.profile, src.read()
    profile.update(height=profile["height"] - 1)
    with rasterio.open(pre, "w", **profile) as dst:
        dst.write(data[:, :-1])
    return manifest


def test_quality_filter_ignores_an_unrelated_broken_pair(
    tmp_path: Path, labels: LabelRaster, capsys: pytest.CaptureFixture[str]
) -> None:
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    broken = _broken_reference(tmp_path, labels, "AOI02_DEL_PRODUCT")
    argv = ["--dir", str(tmp_path), "quality", "EMSR1"]
    assert main([*argv, "--product", "AOI01_DEL_PRODUCT"]) == 0
    assert capsys.readouterr().out.startswith("AOI01_DEL_PRODUCT")
    assert main([*argv, "--aoi", "1"]) == 0
    assert not (broken.parent / "AOI02_DEL_PRODUCT_quality.json").exists()
    assert main(argv) == 1  # unfiltered, the broken pair still stops the run


def test_quality_empty_filter_names_the_filter(
    tmp_path: Path, labels: LabelRaster, capsys: pytest.CaptureFixture[str]
) -> None:
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    assert main(["--dir", str(tmp_path), "quality", "EMSR1", "--aoi", "9"]) == 1
    err = capsys.readouterr().err
    assert "no pairs for EMSR1 matching --aoi 9" in err
    assert "quality EMSR1 --fetch --dry-run --aoi 9" in err
    assert main(["--dir", str(tmp_path), "quality", "EMSR1", "--product", "X"]) == 1
    err = capsys.readouterr().err
    assert "--product X" in err and "--fetch --dry-run --product X" in err


def test_quality_prefers_the_40m_pair(
    tmp_path: Path, labels: LabelRaster, capsys: pytest.CaptureFixture[str]
) -> None:
    # the 100 m pair sorts first; the 40 m one must still be the one printed
    coarse = write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT_b", labels)
    meta = json.loads(coarse.read_text())
    meta["product"] = "AOI01_DEL_PRODUCT"
    coarse.write_text(json.dumps(meta))
    grid = Grid("EPSG:32633", 500000.0, 4600000.0, 40.0, 50, 50)
    raster = LabelRaster(np.zeros((50, 50), np.uint8), grid, {})
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", raster, gap_hours=5)
    assert main(["--dir", str(tmp_path), "quality", "EMSR1", "--json"]) == 0
    (row,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert row["resolution_m"] == 40.0


# --- unreadable radar ---


def test_reference_with_one_band_is_an_ems_error(tmp_path: Path, labels: LabelRaster) -> None:
    manifest = write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    pre = add_reference(manifest, 0.1)
    with rasterio.open(pre) as src:
        profile, data = src.profile, src.read(1)
    profile.update(count=1)
    with rasterio.open(pre, "w", **profile) as dst:
        dst.write(data, 1)
    (pair,) = find_pairs(tmp_path, None)
    with pytest.raises(EmsError, match=r"_pre\.tif.*3 bands"):
        quality.check_pair(pair)


def test_unreadable_radar_is_an_ems_error(tmp_path: Path, labels: LabelRaster) -> None:
    manifest = write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    pre = add_reference(manifest, 0.1)
    pre.write_bytes(b"not a tiff")
    (pair,) = find_pairs(tmp_path, None)
    with pytest.raises(EmsError, match=r"_pre\.tif"):
        quality.check_pair(pair)


# --- non-finite and thresholds ---


@pytest.mark.parametrize("gap", [float("nan"), float("inf")])
def test_non_finite_gap_is_no_pass_time(gap: float) -> None:
    report = _assess(meta={"gap_hours": gap})
    assert report.reason == "no pass time"


def test_nan_resolution_is_not_calibrated() -> None:
    report = _assess(resolution_m=float("nan"))
    assert (report.reason or "").startswith("thresholds calibrated")


def test_report_records_the_thresholds() -> None:
    data = _assess().as_dict()
    assert data["thresholds"]["PARTIAL_EXTENT"] == quality.PARTIAL_EXTENT
    assert set(data["thresholds"]) == {
        "PARTIAL_EXTENT",
        "PARTIAL_RATIO",
        "UNMAPPED_RATIO",
        "LITTLE_CONFIRMED",
        "MAX_GAP_HOURS",
        "MIN_COVERAGE",
        "MIN_MAPPED_KM2",
    }
