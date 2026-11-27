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


# --- pair I/O and report ---------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import rasterio  # noqa: E402
from pairs import add_reference, write_pair  # noqa: E402

from copernicus_ems.agreement import (  # noqa: E402
    agree_pair,
    agree_pairs,
    agreement_paths,
    write_agreement,
)
from copernicus_ems.cli import main  # noqa: E402
from copernicus_ems.dataset import Pair, find_pairs  # noqa: E402
from copernicus_ems.ml import Grid, LabelRaster  # noqa: E402


def _pair40(
    root: Path,
    product: str = "AOI01_DEL_PRODUCT",
    *,
    res: float = 40.0,
    reference: bool = True,
    dark: bool = True,
    **kw: object,
) -> Pair:
    grid = Grid("EPSG:32633", 500000.0, 4600000.0, res, 100, 100)
    arr = np.zeros((100, 100), np.uint8)
    arr[20:60, 20:60] = 1
    manifest = write_pair(root, "EMSR1", product, LabelRaster(arr, grid, {}), gap_hours=5, **kw)  # type: ignore[arg-type]
    meta = json.loads(manifest.read_text())
    if reference:
        add_reference(manifest, 0.1)
        if dark:
            with rasterio.open(manifest.parent / meta["image"]) as src:
                profile, image = src.profile, src.read()
            image[:2] = 0.1
            image[:2, 20:60, 20:60] = 0.01
            image[:2, 80:95, 80:95] = 0.01  # 15x15 px unmapped, 0.36 km2: scattered
            with rasterio.open(manifest.parent / meta["image"], "w", **profile) as dst:
                dst.write(image)
    return next(p for p in find_pairs(root, None) if p.product == product)


def test_agree_pair_writes_three_files(tmp_path: Path) -> None:
    pair = _pair40(tmp_path)
    report, classes = agree_pair(pair)
    assert classes is not None and report.computed
    report = write_agreement(pair, report, classes)
    paths = agreement_paths(pair)
    with rasterio.open(paths["tif"]) as src, rasterio.open(pair.labels) as lab:
        assert src.count == 1 and src.dtypes[0] == "uint8" and src.nodata == 255
        assert src.shape == lab.shape and src.transform == lab.transform
        assert "confirmed=1" in src.descriptions[0]
        assert src.tags()["activation"] == "EMSR1"
        assert src.tags()["product"] == "AOI01_DEL_PRODUCT"
    features = json.loads(paths["geojson"].read_text())["features"]
    assert features
    for f in features:
        assert f["properties"]["class"] in {"confirmed", "map_only", "candidate"}
        assert f["properties"]["km2"] > 0
    assert json.loads(paths["json"].read_text()) == report.as_dict()
    assert report.files == {
        "tif": paths["tif"].name,
        "geojson": paths["geojson"].name,
        "json": paths["json"].name,
    }


def test_geojson_is_lon_lat(tmp_path: Path) -> None:
    pair = _pair40(tmp_path)
    report, classes = agree_pair(pair)
    write_agreement(pair, report, classes)
    data = json.loads(agreement_paths(pair)["geojson"].read_text())

    def walk(c: object) -> list[tuple[float, float]]:
        if isinstance(c, list) and c and isinstance(c[0], (int, float)):
            return [(c[0], c[1])]
        return [pt for x in c for pt in walk(x)]  # type: ignore[attr-defined]

    pts = [pt for f in data["features"] for pt in walk(f["geometry"]["coordinates"])]
    assert pts
    for lon, lat in pts:
        assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert abs(lon - 15.0) < 1 and abs(lat - 41.5) < 1


def test_same_image_not_independent(tmp_path: Path) -> None:
    pair = _pair40(tmp_path, made_from=[{"sensor": "Sentinel-1"}])
    report, classes = agree_pair(pair)
    assert report.computed and classes is not None
    assert report.independent is False
    out = write_agreement(pair, report, classes)
    paths = agreement_paths(pair)
    with rasterio.open(paths["tif"]) as src:
        assert src.tags()["independent"] == "false"
    features = json.loads(paths["geojson"].read_text())["features"]
    assert features and all(f["properties"]["independent"] is False for f in features)
    assert out.independent is False


def test_skip_removes_stale_layers(tmp_path: Path) -> None:
    pair = _pair40(tmp_path)
    report, classes = agree_pair(pair)
    write_agreement(pair, report, classes)
    paths = agreement_paths(pair)
    assert paths["tif"].exists() and paths["geojson"].exists()
    meta = json.loads(pair.manifest.read_text())
    del meta["reference"]
    pair.manifest.write_text(json.dumps(meta))
    pair = next(p for p in find_pairs(tmp_path, None) if p.product == pair.product)
    report, classes = agree_pair(pair)
    assert classes is None
    write_agreement(pair, report, classes)
    assert not paths["tif"].exists() and not paths["geojson"].exists()
    assert json.loads(paths["json"].read_text())["computed"] is False


def test_skip_without_reference(tmp_path: Path) -> None:
    pair = _pair40(tmp_path, reference=False)
    report, classes = agree_pair(pair)
    assert classes is None and not report.computed
    assert report.reason == "no pre-event reference (pairs --reference)"
    assert set(report.km2) == set(CLASS_NAMES.values()) and not any(report.km2.values())
    assert report.scores == {}
    out = write_agreement(pair, report, classes)
    paths = agreement_paths(pair)
    assert paths["json"].exists() and not paths["tif"].exists() and not paths["geojson"].exists()
    assert out.files == {"json": paths["json"].name}


def test_skip_not_40m(tmp_path: Path) -> None:
    pair = _pair40(tmp_path, res=20.0, dark=False)
    report, classes = agree_pair(pair)
    assert classes is None and not report.computed
    assert report.reason == "thresholds calibrated at 40 m"
    write_agreement(pair, report, classes)
    paths = agreement_paths(pair)
    assert paths["json"].exists() and not paths["tif"].exists() and not paths["geojson"].exists()


def test_all_radar_invalid(tmp_path: Path) -> None:
    pair = _pair40(tmp_path, blind_rows=100)
    report, classes = agree_pair(pair)
    assert report.computed and classes is not None
    assert not any(report.km2.values())
    out = write_agreement(pair, report, classes)

    def refuse(name: str) -> None:
        raise AssertionError(name)

    text = agreement_paths(pair)["json"].read_text()
    assert json.loads(text, parse_constant=refuse)["files"] == out.files


def test_quality_verdict_carried(tmp_path: Path) -> None:
    pair = _pair40(tmp_path)
    report, _ = agree_pair(pair)
    assert report.quality_verdict == quality.check_pair(pair).verdict


def test_agree_pairs_select_and_rewrite(tmp_path: Path) -> None:
    a = _pair40(tmp_path, "AOI01_DEL_PRODUCT")
    b = _pair40(tmp_path, "AOI02_DEL_PRODUCT")
    before = a.labels.read_bytes()
    reports = agree_pairs(tmp_path, select=lambda p: p.product == "AOI01_DEL_PRODUCT")
    assert [r.product for r in reports] == ["AOI01_DEL_PRODUCT"]
    assert agreement_paths(a)["json"].exists() and not agreement_paths(b)["json"].exists()
    agree_pairs(tmp_path, select=lambda p: p.product == "AOI01_DEL_PRODUCT")
    assert agreement_paths(a)["tif"].exists()
    assert a.labels.read_bytes() == before
    assert len(agree_pairs(tmp_path, write=False)) == 2


# --- radar command ---------------------------------------------------------


def test_radar_command_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _pair40(tmp_path, "AOI01_DEL_PRODUCT")
    _pair40(tmp_path, "AOI02_DEL_PRODUCT", made_from=[{"sensor": "Sentinel-1"}])
    assert main(["--dir", str(tmp_path), "radar", "EMSR1"]) == 0
    out = capsys.readouterr()
    one, two = out.out.splitlines()
    cols = one.split("\t")
    assert cols[0] == "AOI01_DEL_PRODUCT" and cols[1].startswith("confirmed ")
    assert cols[1].endswith(" km²") and cols[2].startswith("map only ")
    assert cols[3].startswith("candidate ") and cols[4].startswith("CSI ")
    assert "not independent" not in one and two.endswith("\tnot independent")
    assert "2 products, " in out.err
    assert main(["--dir", str(tmp_path), "radar", "EMSR1", "--aoi", "2"]) == 0
    assert capsys.readouterr().out.startswith("AOI02_DEL_PRODUCT")
    assert main(["--dir", str(tmp_path), "radar", "EMSR1", "--product", "AOI01_DEL_PRODUCT"]) == 0
    assert capsys.readouterr().out.startswith("AOI01_DEL_PRODUCT")


def test_radar_command_skipped_and_json(
    tmp_path: Path, labels: LabelRaster, capsys: pytest.CaptureFixture[str]
) -> None:
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    assert main(["--dir", str(tmp_path), "radar", "EMSR1"]) == 0
    assert "AOI01_DEL_PRODUCT\tskipped: " in capsys.readouterr().out
    assert main(["--dir", str(tmp_path), "radar", "EMSR1", "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 1 and rows[0]["computed"] is False and rows[0]["reason"]


def test_radar_command_json_equals_as_dict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pair40(tmp_path)
    assert main(["--dir", str(tmp_path), "radar", "EMSR1", "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    expected = [r.as_dict() for r in agree_pairs(tmp_path, write=False)]
    for row in rows:
        row.pop("files")
    for exp in expected:
        exp.pop("files")
    assert rows == expected


def test_radar_command_no_pairs_and_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--dir", str(tmp_path), "radar", "EMSR9"]) == 1
    assert "no pairs for EMSR9: run copernicus-ems quality EMSR9 --fetch" in capsys.readouterr().err
    _pair40(tmp_path)
    assert main(["--dir", str(tmp_path), "radar", "EMSR1", "--aoi", "7"]) == 1
    assert "matching --aoi 7" in capsys.readouterr().err


def test_radar_prefers_40m_pair() -> None:
    from types import SimpleNamespace

    from copernicus_ems.cli import _prefer_40

    low, high = SimpleNamespace(resolution_m=20.0), SimpleNamespace(resolution_m=40.0)
    assert _prefer_40({"a": [low, high], "b": [low]}) == [high, low]
