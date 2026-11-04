"""Open Sentinel scenes near a product: imagery to pair with the labels.

Rapid Mapping products are often made from commercial images (PAZ,
COSMO-SkyMed, RADARSAT-2, Pléiades… see :attr:`Product.images`), which are
not open. To train or test a model on open data, pair each product with the
**nearest open acquisition** over its AOI: Sentinel-1 (radar, sees through
clouds, the usual choice for floods) or Sentinel-2 (optical).

The scenes come from the public STAC catalogue of the Copernicus Data Space
Ecosystem: searching needs no account, **downloading does** (a free account
on https://dataspace.copernicus.eu). Every match carries its time ``gap`` to
the product's ``observed_time``: water moves, and a scene a day later can
show a different flood.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from copernicus_ems.catalogue import Product, _json, parse_time
from copernicus_ems.errors import EmsError

STAC_SEARCH_URL = "https://stac.dataspace.copernicus.eu/v1/search"
SENTINEL_1_GRD = "sentinel-1-grd"
SENTINEL_2_L2A = "sentinel-2-l2a"

_MAX_PAGES = 20


@dataclass(frozen=True, slots=True)
class Scene:
    """One scene of the Copernicus Data Space catalogue."""

    id: str
    collection: str
    platform: str
    time: datetime
    geometry: dict[str, Any]
    orbit_state: str | None = None
    relative_orbit: int | None = None
    polarizations: tuple[str, ...] = ()
    cloud_cover: float | None = None
    assets: dict[str, str] = field(default_factory=dict)
    #: Scenes of one pass of the satellite share it (``eopf:datatake_id``).
    datatake_id: str | None = None

    @property
    def download_url(self) -> str | None:
        """The zipped product on the Data Space (needs an account token)."""
        return self.assets.get("Product")


@dataclass(frozen=True, slots=True)
class SceneMatch:
    """A scene near a product, and how far apart they are in time."""

    product: Product
    scene: Scene
    #: ``scene.time - product.observed_time``: negative before, positive after.
    gap: timedelta

    @property
    def gap_hours(self) -> float:
        return self.gap.total_seconds() / 3600


@dataclass(frozen=True, slots=True)
class ScenePass:
    """The scenes of one pass of a satellite over a product's AOI.

    A catalogue scene is a slice (a Sentinel-1 slice, a Sentinel-2 tile):
    one pass often needs several to cover an AOI.
    """

    product: Product
    collection: str
    platform: str
    datatake_id: str | None
    scenes: tuple[Scene, ...]
    gap: timedelta

    @property
    def gap_hours(self) -> float:
        return self.gap.total_seconds() / 3600

    @property
    def time(self) -> datetime:
        return min(s.time for s in self.scenes)

    @property
    def geometry(self) -> dict[str, Any]:
        """The footprints, as a GeoJSON GeometryCollection (lon/lat)."""
        return {"type": "GeometryCollection", "geometries": [s.geometry for s in self.scenes]}

    @property
    def cloud_cover(self) -> float | None:
        """The cloudiest scene of the pass (``None`` for radar)."""
        clouds = [s.cloud_cover for s in self.scenes if s.cloud_cover is not None]
        return max(clouds) if clouds else None


def passes(matches: Iterable[SceneMatch]) -> list[ScenePass]:
    """Group scene matches by pass (datatake), nearest pass first."""
    groups: dict[tuple[str, str, str], list[SceneMatch]] = {}
    for m in matches:
        key = (m.product.label, m.scene.collection, m.scene.datatake_id or m.scene.id)
        groups.setdefault(key, []).append(m)
    out = [
        ScenePass(
            product=group[0].product,
            collection=group[0].scene.collection,
            platform=group[0].scene.platform,
            datatake_id=group[0].scene.datatake_id,
            scenes=tuple(sorted((m.scene for m in group), key=lambda s: (s.time, s.id))),
            gap=min((m.gap for m in group), key=abs),
        )
        for group in groups.values()
    ]
    return sorted(out, key=lambda p: (abs(p.gap), p.collection, p.datatake_id or ""))


def scene_search_body(
    geometry: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    collections: Iterable[str] = (SENTINEL_1_GRD,),
    limit: int = 100,
) -> dict[str, Any]:
    """The STAC ``/search`` request for scenes intersecting ``geometry`` (lon/lat GeoJSON)."""
    if end < start:
        raise ValueError("end is before start")
    return {
        "collections": list(collections),
        "intersects": geometry,
        "datetime": f"{_stac_time(start)}/{_stac_time(end)}",
        "limit": limit,
    }


def parse_scenes(body: bytes | str) -> tuple[list[Scene], dict[str, Any] | None]:
    """One page of a STAC search: the scenes, and the ``next`` link if there is one."""
    data = _json(body)
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise EmsError(f"unexpected STAC answer: {str(body)[:200]!r}")
    scenes: list[Scene] = []
    for item in data["features"]:
        props = item.get("properties") or {}
        when = parse_time(props.get("datetime") or props.get("start_datetime"))
        if when is None or not item.get("geometry"):
            continue
        cloud = props.get("eo:cloud_cover")
        orbit = props.get("sat:relative_orbit")
        scenes.append(
            Scene(
                id=str(item.get("id")),
                collection=str(item.get("collection") or ""),
                platform=str(props.get("platform") or ""),
                time=when,
                geometry=item["geometry"],
                orbit_state=props.get("sat:orbit_state") or None,
                relative_orbit=int(orbit) if orbit is not None else None,
                polarizations=tuple(props.get("sar:polarizations") or ()),
                cloud_cover=float(cloud) if cloud is not None else None,
                datatake_id=str(props["eopf:datatake_id"])
                if props.get("eopf:datatake_id") is not None
                else None,
                assets={
                    name: str(asset["href"])
                    for name, asset in (item.get("assets") or {}).items()
                    if isinstance(asset, dict) and asset.get("href")
                },
            )
        )
    following = next((link for link in data.get("links") or [] if link.get("rel") == "next"), None)
    return scenes, following


async def fetch_scenes(
    client: httpx.AsyncClient,
    geometry: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    collections: Iterable[str] = (SENTINEL_1_GRD,),
    url: str = STAC_SEARCH_URL,
) -> list[Scene]:
    """Every scene intersecting ``geometry`` between ``start`` and ``end``, following pages."""
    body: dict[str, Any] | None = scene_search_body(geometry, start, end, collections=collections)
    method, target = "POST", url
    out: list[Scene] = []
    for _ in range(_MAX_PAGES):
        if method == "POST":
            response = await client.post(target, json=body)
        else:
            response = await client.get(target)
        response.raise_for_status()
        scenes, following = parse_scenes(response.content)
        out.extend(scenes)
        if following is None:
            break
        method = str(following.get("method") or "GET").upper()
        target = str(following["href"])
        if following.get("body") is not None:
            body = (
                {**(body or {}), **following["body"]}
                if following.get("merge")
                else following["body"]
            )
    return out


async def find_scenes(
    client: httpx.AsyncClient,
    product: Product,
    *,
    collections: Iterable[str] = (SENTINEL_1_GRD,),
    within: timedelta = timedelta(days=3),
    max_cloud_cover: float | None = None,
    url: str = STAC_SEARCH_URL,
) -> list[SceneMatch]:
    """Open scenes over the product's AOI within ``within`` of its observed time, nearest first.

    ``max_cloud_cover`` (0-100) drops cloudier optical scenes; radar scenes
    have no cloud cover and are always kept.
    """
    observed = product.observed_time
    if observed is None:
        raise EmsError(f"{product.label} has no observation time")
    geometry = product.aoi_extent
    if geometry is None:
        raise EmsError(f"{product.label} has no AOI extent")
    scenes = await fetch_scenes(
        client, geometry, observed - within, observed + within, collections=collections, url=url
    )
    matches = [
        SceneMatch(product=product, scene=s, gap=s.time - observed)
        for s in _unique(scenes)
        if max_cloud_cover is None or s.cloud_cover is None or s.cloud_cover <= max_cloud_cover
    ]
    return sorted(matches, key=lambda m: (abs(m.gap), m.scene.id))


def _unique(scenes: Iterable[Scene]) -> list[Scene]:
    seen: dict[str, Scene] = {}
    for scene in scenes:
        seen.setdefault(scene.id, scene)
    return list(seen.values())


def _stac_time(when: datetime) -> str:
    aware = when if when.tzinfo else when.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
