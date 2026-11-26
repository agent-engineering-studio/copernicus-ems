from __future__ import annotations

from pathlib import Path

import pytest

from copernicus_ems import DATA_ROOT_FOLDER, ENV_DATA_DIR, ENV_DATA_ROOT


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test writes to the user's cache: ``DATA_ROOT`` points into ``tmp_path``."""
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    monkeypatch.setenv(ENV_DATA_ROOT, str(tmp_path / "data"))
    return tmp_path / "data" / DATA_ROOT_FOLDER
