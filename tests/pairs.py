"""Pairs on disk, as ``make_pair`` writes them, for the tests."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine

from copernicus_ems.ml import LabelRaster


def write_pair(
    root: Path,
    activation: str,
    product: str,
    labels: LabelRaster,
    *,
    blind_rows: int = 0,
    made_from: list[dict[str, str]] | None = None,
    gap_hours: float = 35.5,
) -> Path:
    """A pair as make_pair writes it; the first ``blind_rows`` rows of the image have no data."""
    folder = root / activation / "sentinel-1"
    folder.mkdir(parents=True, exist_ok=True)
    grid = labels.grid
    image = np.ones((3, grid.height, grid.width), dtype=np.float32) * 0.05
    image[2] = 1
    image[:, :blind_rows] = 0
    name = f"S1_{activation}_{product}.tif"
    with rasterio.open(
        folder / name,
        "w",
        driver="GTiff",
        width=grid.width,
        height=grid.height,
        count=3,
        dtype="float32",
        crs=grid.crs,
        transform=Affine(*grid.transform),
    ) as dst:
        dst.write(image)
        for i, band in enumerate(("VV", "VH", "dataMask"), start=1):
            dst.set_band_description(i, band)
    labels.to_geotiff(folder / f"{product}_labels.tif")
    manifest = folder / f"{product}_pair.json"
    manifest.write_text(
        json.dumps(
            {
                "activation": activation,
                "product": product,
                "image": name,
                "labels": f"{product}_labels.tif",
                "observed_time": "2024-09-20T05:27:00+00:00",
                "pass_time": "2024-09-21T16:58:40+00:00",
                "gap_hours": gap_hours,
                **({"made_from": made_from} if made_from is not None else {}),
                "grid": {"crs": grid.crs, "transform": list(grid.transform)},
                "credit": ["CEMS credit", "Sentinel credit"],
            }
        )
    )
    return manifest


def add_reference(manifest: Path, value: float, *, blind_cols: int = 0) -> Path:
    """Write a pre-event image next to a pair and name it in the manifest."""
    meta = json.loads(manifest.read_text())
    with rasterio.open(manifest.parent / meta["image"]) as src:
        profile, image = src.profile, src.read()
    image[:2] = value
    image[2] = 1
    image[:, :, :blind_cols] = 0
    name = manifest.name.replace("_pair.json", "_pre.tif")
    with rasterio.open(manifest.parent / name, "w", **profile) as dst:
        dst.write(image)
    meta["reference"] = {"image": name, "pass_time": "2024-09-09T16:58:40+00:00", "lead_days": 12}
    manifest.write_text(json.dumps(meta))
    return manifest.parent / name
