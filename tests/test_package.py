from __future__ import annotations

import package_name


def test_version_is_exposed() -> None:
    assert package_name.__version__
