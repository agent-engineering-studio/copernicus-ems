from __future__ import annotations

from pathlib import Path

import pytest

from copernicus_ems import (
    DATA_ROOT_FOLDER,
    ENV_DATA_DIR,
    ENV_DATA_ROOT,
    ProductEvents,
    parse_detail,
    parse_layer,
    select_features,
)
from copernicus_ems.ml import LabelRaster, rasterize

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test writes to the user's cache: ``DATA_ROOT`` points into ``tmp_path``."""
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    monkeypatch.setenv(ENV_DATA_ROOT, str(tmp_path / "data"))
    return tmp_path / "data" / DATA_ROOT_FOLDER


@pytest.fixture(scope="module")
def labels() -> LabelRaster:
    products = parse_detail((FIXTURES / "detail_EMSR762.json").read_bytes())
    (product,) = [p for p in products if p.label == "AOI01_DEL_PRODUCT"]
    layer = parse_layer(
        (FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json").read_bytes()
    )
    return rasterize(ProductEvents(product=product, features=select_features(layer)), 100)
