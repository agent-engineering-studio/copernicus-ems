from __future__ import annotations

import copernicus_ems


def test_version_is_exposed() -> None:
    assert copernicus_ems.__version__
