"""A training dataset from the image-label pairs of ``copernicus-ems pairs``.

Needs the ``ml`` extra (numpy, rasterio). It reads the pairs already in the
data directory (``<data dir>/<code>/sentinel-1/*_pair.json``), so it spends
no processing units and works offline, and writes::

    dataset/
    ├── images/<id>.tif     Sentinel-1 VV, VH, dataMask (float32, linear power)
    ├── labels/<id>.tif     1 flooded, 0 dry, 255 ignore (uint8)
    ├── index.csv           one row per chip: split, provenance, shares, bounds
    └── dataset.json        bands, label values, splits, statistics, credits

Two rules keep the dataset honest:

* a label pixel where the image has no data (``dataMask`` 0) becomes
  ``255``: there is nothing to learn from;
* train / val / test are assigned by **area** (activation + AOI), never by
  chip: the products of one AOI overlap in space, and splitting their chips
  at random would put the same ground in training and in test.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import rasterio
    from rasterio.transform import Affine
    from rasterio.windows import Window
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.dataset needs the ml extra: pip install 'copernicus-ems[ml]'"
    ) from exc

from copernicus_ems.errors import EmsError
from copernicus_ems.ml import DRY, FLOODED, IGNORE, Grid, LabelRaster, chips

SPLITS = ("train", "val", "test")
#: The version of the dataset layout written by :func:`build_dataset`.
DATASET_VERSION = 1

_INDEX_FIELDS = (
    "id",
    "split",
    "group",
    "activation",
    "product",
    "aoi",
    "observed_time",
    "pass_time",
    "gap_hours",
    "observed_share",
    "flooded_share",
    "image",
    "label",
    "crs",
    "x0",
    "y0",
    "resolution_m",
    "size",
)


@dataclass(frozen=True, slots=True)
class Pair:
    """One image-label pair written by :func:`copernicus_ems.sentinelhub.make_pair`."""

    manifest: Path
    image: Path
    labels: Path
    meta: dict[str, Any]

    @property
    def activation(self) -> str:
        return str(self.meta.get("activation", ""))

    @property
    def product(self) -> str:
        return str(self.meta.get("product", ""))

    @property
    def aoi(self) -> str:
        """``AOI01`` from ``AOI01_DEL_MONIT02``."""
        return self.product.split("_", 1)[0]

    @property
    def group(self) -> str:
        """The unit the splits are made of: one area of interest of one activation."""
        return f"{self.activation}/{self.aoi}"


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    directory: Path
    chips: int
    pairs: int
    groups: dict[str, str]
    per_split: dict[str, int]


def find_pairs(directory: Path, codes: Iterable[str] | None = None) -> list[Pair]:
    """Every pair under ``directory/<code>/sentinel-1/``, ``codes`` only if given."""
    wanted = {c.upper() for c in codes} if codes is not None else None
    out: list[Pair] = []
    for manifest in sorted(directory.glob("*/sentinel-1/*_pair.json")):
        if wanted is not None and manifest.parent.parent.name.upper() not in wanted:
            continue
        try:
            meta = json.loads(manifest.read_text())
            image = manifest.parent / str(meta["image"])
            labels = manifest.parent / str(meta["labels"])
        except (OSError, ValueError, KeyError) as exc:
            raise EmsError(f"unreadable pair manifest {manifest}: {exc}") from exc
        if image.is_file() and labels.is_file():
            out.append(Pair(manifest, image, labels, meta))
    return out


def split_groups(
    sizes: dict[str, int],
    ratios: Sequence[float] = (0.7, 0.15, 0.15),
    *,
    seed: int = 0,
) -> dict[str, str]:
    """Assign each group to a split so that chip counts approach ``ratios``.

    Deterministic for a ``seed``: groups are taken in a seeded hash order,
    largest first among equals, each to the split furthest below its target.
    With fewer groups than splits, the last splits stay empty.
    """
    if len(ratios) != len(SPLITS) or any(r < 0 for r in ratios) or not sum(ratios):
        raise ValueError("ratios must be three non-negative numbers, not all zero")
    total_ratio = sum(ratios)
    targets = [r / total_ratio for r in ratios]
    order = sorted(
        sizes,
        key=lambda g: (-sizes[g], hashlib.sha256(f"{seed}:{g}".encode()).hexdigest()),
    )
    counts = dict.fromkeys(SPLITS, 0)
    assigned: dict[str, str] = {}
    done = 0
    for group in order:
        done += sizes[group]
        # the split whose share would stay furthest below its target
        best = max(
            (s for s, t in zip(SPLITS, targets, strict=True) if t > 0),
            key=lambda s: targets[SPLITS.index(s)] * done - counts[s],
        )
        assigned[group] = best
        counts[best] += sizes[group]
    return assigned


def build_dataset(
    pairs: Sequence[Pair],
    directory: Path,
    *,
    size: int = 256,
    stride: int | None = None,
    min_observed: float = 0.5,
    min_flooded: float = 0.0,
    min_valid: float = 0.9,
    ratios: Sequence[float] = (0.7, 0.15, 0.15),
    seed: int = 0,
) -> DatasetSummary:
    """Cut every pair into aligned chips, assign splits by area, write the dataset.

    A chip is kept when ``min_observed`` of its pixels are observed (label
    0 or 1), ``min_flooded`` of those are flooded, and the image has data on
    ``min_valid`` of the observed ones.
    """
    if not pairs:
        raise EmsError("no pairs: run `copernicus-ems pairs CODE` first")
    images_dir, labels_dir = directory / "images", directory / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    stats = {s: {"chips": 0, "flooded": 0, "dry": 0} for s in SPLITS}
    kept: list[tuple[Pair, list[dict[str, Any]]]] = []
    for pair in pairs:
        kept.append(
            (
                pair,
                _cut(
                    pair, images_dir, labels_dir, size, stride, min_observed, min_flooded, min_valid
                ),
            )
        )
    sizes: dict[str, int] = {}
    for pair, chip_rows in kept:
        sizes[pair.group] = sizes.get(pair.group, 0) + len(chip_rows)
    groups = split_groups({g: n for g, n in sizes.items() if n}, ratios, seed=seed)
    for pair, chip_rows in kept:
        for row in chip_rows:
            split = groups[pair.group]
            row["split"] = split
            stats[split]["chips"] += 1
            stats[split]["flooded"] += row.pop("_flooded")
            stats[split]["dry"] += row.pop("_dry")
            rows.append(row)

    with (directory / "index.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    credits = sorted({c for pair, _ in kept for c in pair.meta.get("credit", [])})
    meta = {
        "version": DATASET_VERSION,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "chip_size": size,
        "stride": stride or size,
        "filters": {
            "min_observed": min_observed,
            "min_flooded": min_flooded,
            "min_valid": min_valid,
        },
        "image_bands": ["VV", "VH", "dataMask"],
        "image_units": "linear power (10 * log10 for dB)",
        "label_values": {"flooded": FLOODED, "dry": DRY, "ignore": IGNORE},
        "split_by": "activation + AOI (no area in two splits)",
        "ratios": dict(zip(SPLITS, ratios, strict=True)),
        "seed": seed,
        "splits": {
            s: {
                "chips": v["chips"],
                "flooded_share": round(v["flooded"] / (v["flooded"] + v["dry"]), 4)
                if v["flooded"] + v["dry"]
                else None,
                "groups": sorted(g for g, gs in groups.items() if gs == s),
            }
            for s, v in stats.items()
        },
        "pairs": [
            {
                "activation": p.activation,
                "product": p.product,
                "group": p.group,
                "gap_hours": p.meta.get("gap_hours"),
                "chips": len(r),
                "manifest": str(p.manifest),
            }
            for p, r in kept
        ],
        "credit": credits,
    }
    (directory / "dataset.json").write_text(json.dumps(meta, indent=2))
    return DatasetSummary(
        directory=directory,
        chips=len(rows),
        pairs=len(pairs),
        groups=groups,
        per_split={s: v["chips"] for s, v in stats.items()},
    )


def _cut(
    pair: Pair,
    images_dir: Path,
    labels_dir: Path,
    size: int,
    stride: int | None,
    min_observed: float,
    min_flooded: float,
    min_valid: float,
) -> list[dict[str, Any]]:
    labels = LabelRaster.from_geotiff(pair.labels)
    rows: list[dict[str, Any]] = []
    with rasterio.open(pair.image) as src:
        grid = Grid.from_transform(src.crs, src.transform, src.width, src.height)
        if grid != labels.grid:
            raise EmsError(f"{pair.image.name} is not on the grid of {pair.labels.name}")
        profile = {
            "driver": "GTiff",
            "width": size,
            "height": size,
            "count": src.count,
            "dtype": "float32",
            "crs": src.crs,
            "compress": "deflate",
            "predictor": 3,
        }
        descriptions = src.descriptions
        for chip in chips(
            labels, size, stride=stride, min_observed=min_observed, min_flooded=min_flooded
        ):
            window = Window(chip.col, chip.row, size, size)
            image = src.read(window=window, boundless=True, fill_value=0).astype(np.float32)
            label = chip.labels.copy()
            no_data = image[-1] == 0
            observed = label != IGNORE
            if observed.sum() == 0:
                continue
            if 1 - np.count_nonzero(no_data & observed) / np.count_nonzero(observed) < min_valid:
                continue
            label[no_data] = IGNORE
            if np.count_nonzero(label != IGNORE) / label.size < min_observed:
                continue
            chip_id = f"{pair.activation}_{pair.product}_r{chip.row}_c{chip.col}"
            transform = Affine(*chip.grid.transform)
            with rasterio.open(
                images_dir / f"{chip_id}.tif", "w", transform=transform, **profile
            ) as dst:
                dst.write(image)
                for index, name in enumerate(descriptions, start=1):
                    if name:
                        dst.set_band_description(index, name)
            LabelRaster(
                label, chip.grid, {"activation": pair.activation, "product": pair.product}
            ).to_geotiff(labels_dir / f"{chip_id}.tif")
            wet = int(np.count_nonzero(label == FLOODED))
            dry = int(np.count_nonzero(label == DRY))
            rows.append(
                {
                    "id": chip_id,
                    "group": pair.group,
                    "activation": pair.activation,
                    "product": pair.product,
                    "aoi": pair.aoi,
                    "observed_time": pair.meta.get("observed_time"),
                    "pass_time": pair.meta.get("pass_time"),
                    "gap_hours": pair.meta.get("gap_hours"),
                    "observed_share": round((wet + dry) / label.size, 4),
                    "flooded_share": round(wet / (wet + dry), 4) if wet + dry else 0.0,
                    "image": f"images/{chip_id}.tif",
                    "label": f"labels/{chip_id}.tif",
                    "crs": chip.grid.crs,
                    "x0": chip.grid.x0,
                    "y0": chip.grid.y0,
                    "resolution_m": chip.grid.resolution_m,
                    "size": size,
                    "_flooded": wet,
                    "_dry": dry,
                }
            )
    return rows
