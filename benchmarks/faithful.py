"""What the checks of ``describe`` catch: hand-written answers with one known fault each.

    uv run python -m benchmarks.faithful            # the table
    uv run python -m benchmarks.faithful --check    # fail if a check changed its answer

``copernicus-ems describe`` lets a model write a description of an activation
from the official text and from the numbers the library computed
(:func:`copernicus_ems.describe_input.facts`). The rule: the numbers come from
the data, a model may explain them, never invent them.
:func:`copernicus_ems.describe_checks.check` enforces it in code: a number, a
date or a name that is in neither the facts nor the official text rejects the
description; an extracted field whose quote is not in the text is dropped.

**The set** (``faithful.json``): the real official text and the real facts of
EMSR664 (Emilia-Romagna, May 2023), recorded once, and answers written by
hand. One is faithful, one rounds as written, the others carry one fault
each: an invented number, a wrong rounding, an invented date, an unknown
place, a map called wrong, a quote that is not in the text. ``expect`` is
what the check must do with each; ``--check`` fails if it does not. No model
is asked and no key is needed: this tests the checker, not a model.

**The blind spot, measured.** Two answers swap figures that are both facts
(the water of Lugo said of Faenza). The check sees that every number exists;
it does not see which place a number is said of. They pass, and the set says
so: ``expect`` records the pass, and the table marks them. A swap is the
fault left to a reader, or to a second check that ties a number to its place.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from copernicus_ems.describe_checks import check
from copernicus_ems.describe_input import Source

SET = Path(__file__).parent / "faithful.json"
BLIND = "blind spot"


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SET.read_text())
    return data


def outcome(data: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    """What the check does with one answer: the description kept or not, the fields dropped."""
    checked = check(answer["answer"], Source(**data["source"]), data["facts"])
    return {
        "description": "kept" if checked.description is not None else "rejected",
        "dropped": sorted({str(d.get("field")) for d in checked.dropped}),
    }


def caught(answer: dict[str, Any], got: dict[str, Any]) -> bool | None:
    """True if a planted fault was caught; ``None`` for an answer with no fault."""
    if answer["fault"] is None:
        return None
    return got["description"] == "rejected" or bool(got["dropped"])


def score(data: dict[str, Any]) -> tuple[str, list[str]]:
    """The table, and the ids of the answers the check no longer treats as expected."""
    lines = ["| Answer | Fault | The check | |", "|---|---|---|---|"]
    changed: list[str] = []
    faults = found = clean = clean_kept = 0
    for answer in data["answers"]:
        got = outcome(data, answer)
        if got != answer["expect"]:
            changed.append(answer["id"])
        hit = caught(answer, got)
        did = got["description"] + (
            f", dropped {', '.join(got['dropped'])}" if got["dropped"] else ""
        )
        if hit is None:
            clean += 1
            clean_kept += got["description"] == "kept" and not got["dropped"]
            mark = "✅ kept" if got["description"] == "kept" else "❌ refused a faithful answer"
        else:
            faults += 1
            found += hit
            mark = "✅ caught" if hit else ("⚠️ not caught: " + BLIND)
        lines.append(
            f"| `{answer['id']}`: {answer['what']} | {answer['fault'] or 'none'} | {did} | {mark} |"
        )
    lines.append("")
    lines.append(
        f"{found} of {faults} planted faults caught; "
        f"{clean_kept} of {clean} faithful answers kept; "
        f"{faults - found} not caught (number swaps)."
    )
    return "\n".join(lines), changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchmarks.faithful", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--check", action="store_true", help="fail if a check changed its answer")
    args = parser.parse_args(argv)
    text, changed = score(load())
    print(text)
    if args.check and changed:
        for name in changed:
            print(f"changed: {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
