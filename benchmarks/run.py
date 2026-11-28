"""Known cases of the quality check: 24 real flood maps and the verdict each must get.

    uv run python -m benchmarks.run            # the table
    uv run python -m benchmarks.run --check    # fail if a verdict or a flag changed

``cases.json`` holds, for each of the 24 Italian maps of notebook 08, what the
radar measured inside the observation mask and the verdict the library gave
when the thresholds were calibrated. A change of threshold, or of the order
of the reasons, that moves one of them fails here before it reaches a release.

Two methods run on the same cases. ``accept-all`` trusts every map: it is
what a caller does who never looks at the verdict. ``quality`` is
:func:`copernicus_ems.quality.assess`. No radar image is read: the cases are
the measurements, so the run needs no network and no account.

What this set is not: a proof that the verdicts are right. One map is a
known suspect (EMSR664 AOI01, a map that looked at part of the area); the
thresholds were fitted so that it is flagged. The other 23 say only that
the check has not changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from copernicus_ems import quality

CASES = Path(__file__).parent / "cases.json"

Answer = tuple[str, list[str]]


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(CASES.read_text())
    return data


def assess(case: dict[str, Any], resolution_m: float) -> Answer:
    """The verdict and the flags of the library for one case."""
    m = case["measurements"]
    measurements = quality.Measurements(
        coverage=1.0,
        mapped_km2=m["mapped_km2"],
        confirmed_share=m["confirmed_share"],
        unmapped_km2=m["unmapped_clustered_km2"],
        unmapped_clustered_km2=m["unmapped_clustered_km2"],
        mapped_extent=m["mapped_extent"],
        unmapped_outside_extent_km2=m["unmapped_outside_extent_km2"],
    )
    meta = {
        "activation": case["activation"],
        "product": case["product"],
        "gap_hours": case["gap_hours"],
        "made_from": [{"sensor": case["made_from"]}],
    }
    report = quality.assess(meta, measurements, resolution_m=resolution_m, has_reference=True)
    return report.verdict, [f.code for f in report.flags]


def accept_all(_: dict[str, Any], __: float) -> Answer:
    return quality.COHERENT, []


METHODS: dict[str, Callable[[dict[str, Any], float], Answer]] = {
    "accept-all": accept_all,
    "quality": assess,
}


def run(data: dict[str, Any]) -> dict[str, list[tuple[dict[str, Any], Answer, bool]]]:
    """For each method: the case, its answer, and whether it is the expected one."""
    out: dict[str, list[tuple[dict[str, Any], Answer, bool]]] = {}
    for name, method in METHODS.items():
        rows = []
        for case in data["cases"]:
            answer = method(case, data["resolution_m"])
            expected = (case["expect"]["verdict"], case["expect"]["flags"])
            rows.append((case, answer, answer == expected))
        out[name] = rows
    return out


def table(results: dict[str, list[tuple[dict[str, Any], Answer, bool]]]) -> str:
    lines = ["| Case | Expected | " + " | ".join(f"`{m}`" for m in results) + " |"]
    lines.append("|---|---|" + "---|" * len(results))
    cases = [case for case, _, _ in next(iter(results.values()))]
    for i, case in enumerate(cases):
        flags = ", ".join(case["expect"]["flags"])
        expected = case["expect"]["verdict"] + (f" ({flags})" if flags else "")
        cells = []
        for rows in results.values():
            _, (verdict, _), ok = rows[i]
            cells.append(("✅ " if ok else "❌ ") + verdict)
        name = f"{case['activation']} {case['product']}"
        lines.append(f"| {name} | {expected} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def summary(results: dict[str, list[tuple[dict[str, Any], Answer, bool]]]) -> str:
    lines = []
    for name, rows in results.items():
        right = sum(ok for _, _, ok in rows)
        verdicts = Counter(answer[0] for _, answer, _ in rows)
        said = ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items()))
        lines.append(f"{name}: {right} of {len(rows)} as expected ({said})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmarks.run", description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if `quality` changed a case")
    args = parser.parse_args(argv)
    results = run(load())
    print(table(results))
    print()
    print(summary(results))
    wrong = [case for case, _, ok in results["quality"] if not ok]
    if args.check and wrong:
        for case in wrong:
            print(f"changed: {case['activation']} {case['product']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
