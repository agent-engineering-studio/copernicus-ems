from __future__ import annotations

from pathlib import Path

import pytest

from copernicus_ems import ENV_DATA_DIR


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test writes to the user's cache."""
    directory = tmp_path / "ems"
    monkeypatch.setenv(ENV_DATA_DIR, str(directory))
    return directory
