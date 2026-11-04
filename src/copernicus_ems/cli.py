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
from datetime import date, timedelta
from pathlib import Path
from typing import TextIO

import httpx

from copernicus_ems.catalogue import Activation
from copernicus_ems.client import ProductEvents, RapidMapping
from copernicus_ems.errors import EmsError
from copernicus_ems.events import FLOOD_EVENT_TYPE, FLOOD_NOTATIONS
from copernicus_ems.export import mask_features, tagged_features, write_features
from copernicus_ems.scenes import SENTINEL_1_GRD, SENTINEL_2_L2A, ScenePass, passes

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
    export.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="the file to write: .geojson, or .parquet for GeoParquet (parquet extra)",
    )
    export.add_argument(
        "--masks", type=Path, help="also write the observation masks here (.geojson or .parquet)"
    )

    scenes = commands.add_parser(
        "scenes", help="open Sentinel passes over the products of an activation, nearest first"
    )
    scenes.add_argument("code", metavar="CODE", help="e.g. EMSR762")
    scenes.add_argument("--aoi", type=int, help="only this AOI number")
    scenes.add_argument("--days", type=float, default=3.0, help="search window, ± days (default 3)")
    scenes.add_argument("--optical", action="store_true", help="add Sentinel-2 L2A to Sentinel-1")
    scenes.add_argument("--max-cloud", type=float, help="drop optical scenes cloudier than this %%")
    scenes.add_argument("--top", type=int, default=3, help="passes per product (default 3)")
    scenes.add_argument("--json", action="store_true", help="one JSON object per pass")

    pairs = commands.add_parser(
        "pairs",
        help="Sentinel-1 backscatter + labels on the same grid, per product (cdse extra)",
    )
    pairs.add_argument("code", metavar="CODE", help="e.g. EMSR664")
    pairs.add_argument("--aoi", type=int, help="only this AOI number")
    pairs.add_argument("--product", help="only this product, e.g. AOI01_DEL_MONIT01")
    pairs.add_argument("--resolution", type=float, default=20.0, help="metres (default 20)")
    pairs.add_argument("--days", type=float, default=2.0, help="search window, ± days (default 2)")
    pairs.add_argument(
        "--max-gap-hours", type=float, default=24.0, help="pass to observation (default 24)"
    )
    pairs.add_argument(
        "--min-coverage", type=float, default=0.9, help="share of the AOI (default 0.9)"
    )
    pairs.add_argument(
        "--budget-pu", type=float, default=500.0, help="stop above this many PU (default 500)"
    )
    pairs.add_argument(
        "--dry-run", action="store_true", help="show the plan and its PU, send nothing"
    )

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
        if args.command == "scenes":
            return await _scenes(ems, args)
        if args.command == "pairs":
            return await _pairs(ems, args)
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

    events: list[ProductEvents] = []
    for code in codes:
        found = await _events(ems, code, args)
        _report(code, found, sys.stderr)
        events.extend(found)
    features = tagged_features(events)
    masks = mask_features(events)

    write_features(args.output, features)
    print(
        f"{len(features)} features from {len(codes)} activations -> {args.output}", file=sys.stderr
    )
    if args.masks is not None:
        write_features(args.masks, masks)
        print(f"{len(masks)} observation masks -> {args.masks}", file=sys.stderr)
    return 0


async def _scenes(ems: RapidMapping, args: argparse.Namespace) -> int:
    try:
        from copernicus_ems.geo import coverage
    except ImportError:  # pragma: no cover - without the geo extra
        print("copernicus-ems: scenes needs the geo extra: pip install 'copernicus-ems[geo]'")
        return 2
    collections = [SENTINEL_1_GRD, SENTINEL_2_L2A] if args.optical else [SENTINEL_1_GRD]
    products = await ems.products(args.code)
    events = {
        e.product.label: e
        for e in await ems.observed_events(args.code, event_types=None, notations=None)
    }
    for product in products:
        if args.aoi is not None and product.aoi_number != args.aoi:
            continue
        if product.observed_time is None or product.aoi_extent is None:
            continue
        found = passes(
            await ems.scenes(
                product,
                collections=collections,
                within=timedelta(days=args.days),
                max_cloud_cover=args.max_cloud,
            )
        )[: args.top]
        sources = ", ".join(f"{i.sensor_name} ({i.resolution_class})" for i in product.images)
        if not args.json:
            when = f"{product.observed_time:%Y-%m-%d %H:%M}"
            print(f"{product.label}\t{when}\tmade from {sources or '?'}")
        for p in found:
            event = events.get(product.label) or ProductEvents(product=product, features=[])
            print(_pass_line(p, coverage(p.geometry, event), as_json=args.json))
    return 0


async def _pairs(ems: RapidMapping, args: argparse.Namespace) -> int:
    try:
        from copernicus_ems.sentinelhub import SentinelHub, make_pair, plan_pair
    except ImportError:  # pragma: no cover - without the cdse extra
        print("copernicus-ems: pairs needs: pip install 'copernicus-ems[cdse]'", file=sys.stderr)
        return 2
    events = [
        e
        for e in await ems.observed_events(args.code, event_types=None, notations=None)
        if (args.aoi is None or e.product.aoi_number == args.aoi)
        and (args.product is None or e.product.label == args.product)
        and e.product.observed_time is not None
        and e.product.aoi_extent is not None
    ]
    plans = [
        await plan_pair(
            ems,
            e,
            resolution_m=args.resolution,
            within=timedelta(days=args.days),
            min_coverage=args.min_coverage,
            max_gap=timedelta(hours=args.max_gap_hours),
        )
        for e in events
    ]
    unique = {
        p.backscatter_path: p
        for p in plans
        if p.scene_pass is not None and not _done(p.backscatter_path)
    }
    to_fetch = list(unique.values())  # one request per pass and grid, shared by products
    total = sum(p.pu for p in to_fetch)
    for plan in plans:
        if plan.scene_pass is None:
            print(f"{plan.product}\tskipped: {plan.reason}")
            continue
        if _done(plan.backscatter_path):
            state = "cached"
        elif unique.get(plan.backscatter_path) is plan:
            state = f"{plan.pu:.1f} PU"
        else:
            state = f"shares the image of {unique[plan.backscatter_path].product}"
        print(
            f"{plan.product}\t{plan.grid.width}x{plan.grid.height} px\t"
            f"{plan.scene_pass.gap_hours:+.1f} h\tAOI {plan.coverage:.0%}\t{state}"
        )
    print(f"{len(to_fetch)} images to fetch, {total:.1f} PU estimated", file=sys.stderr)
    if args.dry_run or not to_fetch:
        return 0
    if total > args.budget_pu:
        print(
            f"copernicus-ems: {total:.1f} PU is over --budget-pu {args.budget_pu:.0f}",
            file=sys.stderr,
        )
        return 2
    async with SentinelHub() as hub:
        for plan in plans:
            if plan.scene_pass is None:
                continue
            image, labels = await make_pair(hub, plan)
            print(f"{plan.product}\t{image.path}\t{labels}")
    print(f"{hub.pu_estimated:.1f} PU spent (estimated)", file=sys.stderr)
    return 0


def _done(path: Path | None) -> bool:
    return path is not None and path.is_file()


def _pass_line(p: ScenePass, cover: float, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "product": p.product.label,
                "collection": p.collection,
                "platform": p.platform,
                "time": p.time.isoformat(),
                "gap_hours": round(p.gap_hours, 2),
                "aoi_coverage": round(cover, 3),
                "cloud_cover": p.cloud_cover,
                "scenes": [s.id for s in p.scenes],
                "download_urls": [s.download_url for s in p.scenes if s.download_url],
            }
        )
    cloud = "" if p.cloud_cover is None else f"\tcloud {p.cloud_cover:.0f}%"
    return (
        f"  {p.gap_hours:+7.1f} h\t{p.collection}\t{len(p.scenes)} scenes\t"
        f"AOI {cover:.0%}{cloud}\t{p.scenes[0].id}"
    )


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
