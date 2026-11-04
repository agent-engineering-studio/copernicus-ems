"""``copernicus-ems``: list activations, fetch their layers, export them as one GeoJSON.

    copernicus-ems list --country Italy --since 2024-01-01
    copernicus-ems fetch EMSR762 EMSR664
    copernicus-ems export --country Italy -o italy.geojson --masks italy_masks.geojson

Only the standard library on top of the package itself. Progress goes to
standard error, results to standard output or to the files asked for.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, TextIO

import httpx

from copernicus_ems.catalogue import Activation
from copernicus_ems.client import ProductEvents, RapidMapping
from copernicus_ems.errors import EmsError
from copernicus_ems.events import FLOOD_EVENT_TYPE, FLOOD_NOTATIONS

#: Many floods are filed as ``Storm``: the features are filtered on ``5-Flood`` anyway.
EXPORT_CATEGORIES = ("Flood", "Storm")


def main(argv: Sequence[str] | None = None, *, client: httpx.AsyncClient | None = None) -> int:
    """Run the command line; the exit status. ``client`` is for tests and embedding."""
    args = _parser().parse_args(argv)
    if args.command == "mcp":  # pragma: no cover - blocks on stdio
        from copernicus_ems.mcp_server import run

        run(directory=args.dir)
        return 0
    try:
        return asyncio.run(_run(args, client))
    except (EmsError, httpx.HTTPError) as exc:
        print(f"copernicus-ems: {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copernicus-ems",
        description="Copernicus EMS Rapid Mapping: activations and observed event layers.",
    )
    parser.add_argument(
        "--dir", type=Path, default=None, help="where layers are saved (default: the cache)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def filters(sub: argparse.ArgumentParser, categories: Sequence[str] | None) -> None:
        sub.add_argument("--country", help="e.g. Italy")
        sub.add_argument(
            "--category",
            action="append",
            dest="categories",
            default=None,
            help=f"repeatable (default: {', '.join(categories) if categories else 'all'})",
        )
        sub.add_argument("--since", type=date.fromisoformat, help="YYYY-MM-DD, included")
        sub.add_argument("--until", type=date.fromisoformat, help="YYYY-MM-DD, included")
        sub.set_defaults(default_categories=categories)

    def events(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--all-events",
            action="store_true",
            help="every feature, not only floods (landslides, AOI, …)",
        )

    listing = commands.add_parser("list", help="public activations, oldest first")
    filters(listing, None)
    listing.add_argument("--json", action="store_true", help="one JSON object per line")

    fetch = commands.add_parser("fetch", help="download the observed event layers of activations")
    fetch.add_argument("codes", nargs="+", metavar="CODE", help="e.g. EMSR762")
    events(fetch)

    export = commands.add_parser(
        "export", help="observed events of activations, or of a country, as one GeoJSON"
    )
    export.add_argument("codes", nargs="*", metavar="CODE", help="activations (or use --country)")
    filters(export, EXPORT_CATEGORIES)
    events(export)
    export.add_argument("-o", "--output", type=Path, required=True, help="the GeoJSON to write")
    export.add_argument("--masks", type=Path, help="also write the observation masks here")

    commands.add_parser(
        "mcp", help="serve the MCP tools on stdio, for LLM agents (needs the mcp extra)"
    )
    return parser


async def _run(args: argparse.Namespace, client: httpx.AsyncClient | None) -> int:
    async with RapidMapping(client, directory=args.dir) as ems:
        if args.command == "list":
            for activation in await _activations(ems, args):
                print(_listing(activation, as_json=args.json))
            return 0
        if args.command == "fetch":
            for code in args.codes:
                found = await _events(ems, code, args)
                _report(code, found, sys.stderr)
            print(f"saved in {ems.directory}", file=sys.stderr)
            return 0
        return await _export(ems, args)


async def _activations(ems: RapidMapping, args: argparse.Namespace) -> list[Activation]:
    return await ems.activations(
        country=args.country,
        categories=args.categories or args.default_categories,
        since=args.since,
        until=args.until,
    )


async def _events(ems: RapidMapping, code: str, args: argparse.Namespace) -> list[ProductEvents]:
    if args.all_events:
        return await ems.observed_events(code, event_types=None, notations=None)
    return await ems.observed_events(
        code, event_types=(FLOOD_EVENT_TYPE,), notations=FLOOD_NOTATIONS
    )


async def _export(ems: RapidMapping, args: argparse.Namespace) -> int:
    if args.codes and args.country:
        print("copernicus-ems: give activation codes or --country, not both", file=sys.stderr)
        return 2
    if args.codes:
        codes: list[str] = list(args.codes)
    elif args.country:
        codes = [a.code for a in await _activations(ems, args)]
    else:
        print("copernicus-ems: give activation codes or --country", file=sys.stderr)
        return 2

    features: list[dict[str, Any]] = []
    masks: list[dict[str, Any]] = []
    for code in codes:
        found = await _events(ems, code, args)
        _report(code, found, sys.stderr)
        for event in found:
            tags = _tags(event)
            features.extend(
                {**f, "properties": {**(f.get("properties") or {}), **tags}} for f in event.features
            )
            if (mask := event.product.aoi_extent) is not None:
                masks.append({"type": "Feature", "geometry": mask, "properties": tags})

    _write(args.output, features)
    print(
        f"{len(features)} features from {len(codes)} activations -> {args.output}", file=sys.stderr
    )
    if args.masks is not None:
        _write(args.masks, masks)
        print(f"{len(masks)} observation masks -> {args.masks}", file=sys.stderr)
    return 0


def _tags(event: ProductEvents) -> dict[str, Any]:
    observed = event.product.observed_time
    return {
        "activation": event.product.activation_code,
        "product": event.product.label,
        "aoi_name": event.product.aoi_name,
        "observed_time": observed.isoformat() if observed else None,
    }


def _listing(activation: Activation, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "code": activation.code,
                "name": activation.name,
                "category": activation.category,
                "event_time": activation.event_time.isoformat(),
                "countries": list(activation.countries),
                "closed": activation.closed,
            }
        )
    fields = (activation.code, f"{activation.event_time:%Y-%m-%d}", activation.category)
    return "\t".join((*fields, activation.name))


def _report(code: str, found: list[ProductEvents], out: TextIO) -> None:
    count = sum(len(e.features) for e in found)
    print(f"{code}: {count} features in {len(found)} products", file=out)


def _write(path: Path, features: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
