from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pairs import add_reference, write_pair

from copernicus_ems import EmsError
from copernicus_ems.cli import main
from copernicus_ems.dataset import SPLITS, Pair, build_dataset, find_pairs, split_groups
from copernicus_ems.ml import FLOODED, IGNORE, Grid, LabelRaster

FIXTURES = Path(__file__).parent / "fixtures"


def test_find_pairs(tmp_path: Path, labels: LabelRaster) -> None:
    write_pair(tmp_path, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    write_pair(tmp_path, "EMSR2", "AOI02_GRA_PRODUCT", labels)
    (tmp_path / "EMSR3" / "sentinel-1").mkdir(parents=True)
    (tmp_path / "EMSR3" / "sentinel-1" / "X_pair.json").write_text(
        json.dumps({"image": "gone.tif", "labels": "gone.tif"})
    )
    found = find_pairs(tmp_path)
    assert [p.group for p in found] == [
        "EMSR1/AOI01",
        "EMSR2/AOI02",
    ]  # the incomplete pair is left out
    assert [p.product for p in find_pairs(tmp_path, ["emsr2"])] == ["AOI02_GRA_PRODUCT"]
    (tmp_path / "EMSR3" / "sentinel-1" / "Y_pair.json").write_text("{broken")
    with pytest.raises(EmsError, match="unreadable"):
        find_pairs(tmp_path)


def test_splits_never_divide_an_area() -> None:
    sizes = {f"E{i}/AOI01": n for i, n in enumerate([50, 40, 30, 20, 10, 10, 5, 5])}
    groups = split_groups(sizes, (0.7, 0.15, 0.15), seed=1)
    assert set(groups) == set(sizes) and set(groups.values()) == set(SPLITS)
    chips = {s: sum(n for g, n in sizes.items() if groups[g] == s) for s in SPLITS}
    assert chips["train"] > chips["val"] > 0 and chips["test"] > 0
    assert split_groups(sizes, seed=1) == groups  # deterministic
    assert set(split_groups({"a": 1}).values()) == {"train"}
    assert set(split_groups(sizes, (1, 0, 0)).values()) == {"train"}
    for bad in [(1, 1), (-1, 1, 1), (0, 0, 0)]:
        with pytest.raises(ValueError):
            split_groups(sizes, bad)


def test_build_a_dataset(tmp_path: Path, labels: LabelRaster) -> None:
    data = tmp_path / "data"
    for activation, product in [
        ("EMSR1", "AOI01_DEL_PRODUCT"),
        ("EMSR1", "AOI01_DEL_MONIT01"),
        ("EMSR2", "AOI01_GRA_PRODUCT"),
        ("EMSR3", "AOI02_DEL_PRODUCT"),
    ]:
        write_pair(data, activation, product, labels, blind_rows=40)
    out = tmp_path / "ds"
    summary = build_dataset(find_pairs(data), out, size=64, min_observed=0.3)
    assert summary.pairs == 4 and summary.chips > 0
    assert sum(summary.per_split.values()) == summary.chips
    # the two products of EMSR1 AOI01 share their area: same split
    assert set(summary.groups) == {"EMSR1/AOI01", "EMSR2/AOI01", "EMSR3/AOI02"}

    rows = list(csv.DictReader((out / "index.csv").open()))
    assert len(rows) == summary.chips
    by_group: dict[str, set[str]] = {}
    for row in rows:
        by_group.setdefault(row["group"], set()).add(row["split"])
    assert all(len(s) == 1 for s in by_group.values())

    row = rows[0]
    with rasterio.open(out / row["image"]) as img, rasterio.open(out / row["label"]) as lab:
        assert img.shape == lab.shape == (64, 64) and img.count == 3
        assert img.transform == lab.transform and img.crs == lab.crs
        assert img.descriptions == ("VV", "VH", "dataMask")
        assert Grid.from_transform(lab.crs, lab.transform, 64, 64).x0 == float(row["x0"])
    meta = json.loads((out / "dataset.json").read_text())
    assert meta["label_values"] == {"flooded": 1, "dry": 0, "ignore": 255}
    assert meta["split_by"].startswith("activation + AOI")
    assert sum(s["chips"] for s in meta["splits"].values()) == summary.chips
    assert meta["credit"] == ["CEMS credit", "Sentinel credit"]
    assert {p["group"] for p in meta["pairs"]} == set(summary.groups)


def test_no_image_data_means_ignore(tmp_path: Path, labels: LabelRaster) -> None:
    data = tmp_path / "data"
    write_pair(data, "EMSR1", "AOI01_DEL_PRODUCT", labels, blind_rows=labels.grid.height // 2)
    out = tmp_path / "ds"
    build_dataset(find_pairs(data), out, size=64, min_observed=0.01, min_valid=0.0)
    for row in csv.DictReader((out / "index.csv").open()):
        with rasterio.open(out / row["image"]) as img, rasterio.open(out / row["label"]) as lab:
            mask, label = img.read(3), lab.read(1)
        assert np.all(label[mask == 0] == IGNORE)
    strict = build_dataset(
        find_pairs(data), tmp_path / "strict", size=64, min_observed=0.01, min_valid=0.99
    )
    loose = build_dataset(
        find_pairs(data), tmp_path / "loose", size=64, min_observed=0.01, min_valid=0.0
    )
    assert strict.chips < loose.chips


def test_flood_filter_and_mismatched_grids(tmp_path: Path, labels: LabelRaster) -> None:
    data = tmp_path / "data"
    write_pair(data, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    every = build_dataset(find_pairs(data), tmp_path / "a", size=64, min_observed=0.1)
    wet = build_dataset(
        find_pairs(data), tmp_path / "b", size=64, min_observed=0.1, min_flooded=0.001
    )
    assert 0 < wet.chips < every.chips
    for row in csv.DictReader((tmp_path / "b" / "index.csv").open()):
        with rasterio.open(tmp_path / "b" / row["label"]) as lab:
            assert (lab.read(1) == FLOODED).any()

    other = LabelRaster(
        labels.labels,
        Grid(
            labels.grid.crs,
            labels.grid.x0 + 100,
            labels.grid.y0,
            labels.grid.resolution_m,
            labels.grid.width,
            labels.grid.height,
        ),
    )
    (pair,) = find_pairs(data)
    other.to_geotiff(pair.labels)
    with pytest.raises(EmsError, match="not on the grid"):
        build_dataset([pair], tmp_path / "c")
    with pytest.raises(EmsError, match="no pairs"):
        build_dataset([], tmp_path / "d")


def test_the_dataset_command(
    tmp_path: Path, labels: LabelRaster, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    write_pair(data, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    write_pair(data, "EMSR2", "AOI01_DEL_PRODUCT", labels)
    argv = [
        "--dir",
        str(data),
        "dataset",
        "-o",
        str(tmp_path / "ds"),
        "--size",
        "64",
        "--min-observed",
        "0.2",
    ]
    assert main(argv) == 0
    out = capsys.readouterr()
    assert "2 pairs" in out.err and "train\t" in out.out
    assert (tmp_path / "ds" / "dataset.json").is_file()
    assert main([*argv, "--split", "a,b"]) == 2
    assert main([*argv, "EMSR9"]) == 1  # no pairs for that activation
    assert "no pairs" in capsys.readouterr().err


def test_pair_properties(tmp_path: Path) -> None:
    pair = Pair(
        tmp_path / "m.json",
        tmp_path / "i.tif",
        tmp_path / "l.tif",
        {"activation": "EMSR9", "product": "AOI03_GRA_PRODUCT"},
    )
    assert (pair.aoi, pair.group) == ("AOI03", "EMSR9/AOI03")


def test_a_dataset_with_reference_bands(tmp_path: Path, labels: LabelRaster) -> None:
    data = tmp_path / "data"
    with_ref = write_pair(data, "EMSR1", "AOI01_DEL_PRODUCT", labels)
    add_reference(with_ref, 0.2, blind_cols=30)
    write_pair(data, "EMSR2", "AOI01_DEL_PRODUCT", labels)  # no reference: left out
    pairs = find_pairs(data)
    assert [p.reference is not None for p in pairs] == [True, False]

    out = tmp_path / "ds"
    summary = build_dataset(pairs, out, size=64, min_observed=0.3, min_valid=0.0, reference=True)
    assert summary.pairs == 1 and summary.chips > 0
    meta = json.loads((out / "dataset.json").read_text())
    assert meta["image_bands"] == ["VV", "VH", "VV_pre", "VH_pre", "dataMask"]
    assert meta["reference"] is True and meta["pairs"][0]["reference_lead_days"] == 12
    rows = list(csv.DictReader((out / "index.csv").open()))
    assert {r["reference_time"] for r in rows} == {"2024-09-09T16:58:40+00:00"}
    first = next(r for r in rows if r["id"].endswith("_c0"))
    with rasterio.open(out / first["image"]) as src:
        assert src.count == 5 and src.descriptions[2] == "VV_pre"
        bands = src.read()
    assert np.allclose(bands[0][bands[4] == 1], 0.05) and np.allclose(bands[2][bands[4] == 1], 0.2)
    # no data in the reference -> no data in the chip -> ignored label
    assert (bands[4][:, :30] == 0).all()
    with rasterio.open(out / first["label"]) as src:
        assert (src.read(1)[:, :30] == IGNORE).all()

    # without reference=True the same pairs give the three-band layout
    plain = build_dataset(pairs, tmp_path / "plain", size=64, min_observed=0.3)
    assert plain.pairs == 2
    assert json.loads((tmp_path / "plain" / "dataset.json").read_text())["image_bands"] == [
        "VV",
        "VH",
        "dataMask",
    ]
    with pytest.raises(EmsError, match="--reference"):
        build_dataset(pairs[1:], tmp_path / "none", reference=True)
