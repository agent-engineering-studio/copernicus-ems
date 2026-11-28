"""Rules of the repository, checked: what the library imports, the known cases, the notebooks."""

from __future__ import annotations

import ast
import importlib
import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "copernicus_ems"

#: Limen, its core and the sister libraries: none may be imported here. The library
#: stands alone; what joins two libraries lives in limen-core.
BANNED = (
    "limen",
    "limen_core",
    "dpc_radar",
    "egms_client",
    "effis_client",
    "ispra_idrogeo",
    "idrogeo",
    "ingv_client",
    "arpa_client",
    "istat_client",
)
#: What each module may import beside the standard library and the package itself.
#: The core (every module not named here) has httpx alone.
EXTRAS = {
    "geo": {"shapely", "pyproj"},
    "truth": {"shapely", "pyproj", "numpy"},
    "overlay": {"shapely"},
    "places": {"shapely"},
    "reports": {"shapely"},
    "export": {"pyarrow", "shapely"},
    "ml": {"numpy", "rasterio", "shapely", "pyproj"},
    "dataset": {"numpy", "rasterio"},
    "quality": {"numpy", "rasterio"},
    "agreement": {"numpy", "rasterio", "shapely", "pyproj"},
    "sentinelhub": {"numpy", "rasterio", "shapely", "pyproj"},
    "mcp_server": {"mcp", "mcp_types", "starlette", "pyarrow"},
    "describe": {"anthropic"},
}


def imported(path: Path) -> set[str]:
    """The top-level names a module imports, wherever in the file."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


MODULES = sorted(SRC.glob("*.py"))


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.stem)
def test_no_module_imports_limen_or_a_sister_library(path: Path) -> None:
    assert not imported(path) & set(BANNED)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.stem)
def test_third_party_imports_are_httpx_or_the_extra_of_the_module(path: Path) -> None:
    import sys

    third = {
        name
        for name in imported(path)
        if name not in sys.stdlib_module_names and name != "copernicus_ems"
    }
    assert third <= {"httpx"} | EXTRAS.get(path.stem, set()), path.stem


def test_the_core_has_one_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == ["httpx>=0.27"]
    listed = " ".join(d for extra in project["optional-dependencies"].values() for d in extra)
    assert not any(name.replace("_", "-") in listed for name in BANNED)


def test_the_known_maps_keep_their_verdict() -> None:
    from benchmarks import run

    data = run.load()
    assert len(data["cases"]) == 24
    results = run.run(data)
    assert all(ok for _, _, ok in results["quality"])
    # Trusting every map is right only on the 13 the check calls coherent.
    assert sum(ok for _, _, ok in results["accept-all"]) == 13
    assert run.main(["--check"]) == 0


def test_the_checks_of_describe_catch_what_the_set_says() -> None:
    from benchmarks import faithful

    data = faithful.load()
    text, changed = faithful.score(data)
    assert changed == []
    assert "11 of 13 planted faults caught" in text and "2 of 2 faithful answers kept" in text
    # The blind spot stays written down: a swap of two real figures passes.
    swaps = [a for a in data["answers"] if "swapped" in a["id"]]
    assert len(swaps) == 2 and all(a["expect"]["description"] == "kept" for a in swaps)
    assert faithful.main(["--check"]) == 0


NOTEBOOKS = sorted((ROOT / "notebooks").glob("*.ipynb"))
_IMPORT = re.compile(r"^\s*from (copernicus_ems[\w.]*) import \(?([^)#\n]+)", re.MULTILINE)


def test_notebooks_are_numbered_and_listed() -> None:
    numbers = [int(p.name[:2]) for p in NOTEBOOKS]
    assert numbers == list(range(1, len(NOTEBOOKS) + 1)) and len(NOTEBOOKS) == 8
    listed = (ROOT / "notebooks" / "README.md").read_text()
    assert all(p.name in listed for p in NOTEBOOKS)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_imports_names_that_exist(path: Path) -> None:
    # The notebooks are committed without results (the `notebook` workflow runs them, with
    # the CDSE secrets): what can be checked here is that the names they import still exist.
    cells = [c for c in json.loads(path.read_text())["cells"] if c["cell_type"] == "code"]
    assert cells
    source = "\n".join("".join(c["source"]) for c in cells)
    # A parenthesised import over several lines: join it first.
    source = re.sub(r"\(\s*\n", "(", source)
    source = re.sub(r",\s*\n\s*", ", ", source)
    for module, names in _IMPORT.findall(source):
        loaded = importlib.import_module(module)
        for name in names.split(","):
            name = name.strip().split(" as ")[0].strip("() ")
            if name:
                assert hasattr(loaded, name), f"{path.name}: {module}.{name}"
