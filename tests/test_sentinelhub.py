from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from test_scenes import STAC, stac_handler

from copernicus_ems import (
    EmsError,
    ProductEvents,
    RapidMapping,
    parse_detail,
    parse_layer,
    select_features,
)
from copernicus_ems.cli import main
from copernicus_ems.ml import FLOODED, IGNORE, Grid, LabelRaster
from copernicus_ems.sentinelhub import (
    PROCESS_URL,
    TOKEN_URL,
    Credentials,
    SentinelHub,
    backscatter_path,
    backscatter_request,
    estimate_pu,
    evalscript,
    fetch_backscatter,
    make_pair,
    parse_token,
    pass_window,
    plan_pair,
    plan_reference,
    tiles,
)

FIXTURES = Path(__file__).parent / "fixtures"
CREDS = Credentials("my-client", "s3cret")
T0 = datetime(2024, 9, 21, 16, 58, tzinfo=UTC)
GRID = Grid("EPSG:32632", 700_000, 4_900_000, 20, 300, 200)


class FakeCdse:
    """Token endpoint + a Process API that paints each pixel with its own coordinates."""

    def __init__(self, *, expires_in: int = 3600, status: int = 200, body: Any = None) -> None:
        self.tokens = 0
        self.requests: list[dict[str, Any]] = []
        self.expires_in = expires_in
        self.status = status
        self.body = body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TOKEN_URL:
            self.tokens += 1
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            if form.get("client_secret") != "s3cret":
                return httpx.Response(
                    401, json={"error": "invalid_client", "error_description": "bad"}
                )
            return httpx.Response(
                200, json={"access_token": f"tok{self.tokens}", "expires_in": self.expires_in}
            )
        if url == PROCESS_URL:
            assert request.headers["authorization"].startswith("Bearer tok")
            assert request.headers["accept"] == "image/tiff"
            body = json.loads(request.content)
            self.requests.append(body)
            if self.status != 200:
                return httpx.Response(self.status, json=self.body)
            return httpx.Response(200, content=_tiff(body))
        return stac_handler(request)


def _tiff(body: dict[str, Any]) -> bytes:
    x0, y0, x1, y1 = body["input"]["bounds"]["bbox"]
    epsg = body["input"]["bounds"]["properties"]["crs"].rsplit("/", 1)[-1]
    width, height = body["output"]["width"], body["output"]["height"]
    transform = from_bounds(x0, y0, x1, y1, width, height)
    cols, rows = np.meshgrid(np.arange(width), np.arange(height))
    xs = (x0 + (cols + 0.5) * (x1 - x0) / width).astype(np.float32)
    ys = (y1 - (rows + 0.5) * (y1 - y0) / height).astype(np.float32)
    data = np.stack([xs, ys, np.ones_like(xs)])
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=width,
            height=height,
            count=3,
            dtype="float32",
            crs=f"EPSG:{epsg}",
            transform=transform,
        ) as dst:
            dst.write(data)
        return bytes(memory.read())


def hub(fake: FakeCdse, clock: Any = None) -> SentinelHub:
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return SentinelHub(CREDS, client, **({"clock": clock} if clock else {}))


def event() -> ProductEvents:
    products = parse_detail((FIXTURES / "detail_EMSR762.json").read_bytes())
    (product,) = [p for p in products if p.label == "AOI01_DEL_PRODUCT"]
    layer = parse_layer(
        (FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json").read_bytes()
    )
    return ProductEvents(product=product, features=select_features(layer))


def test_credentials_from_the_environment_or_a_file(tmp_path: Path) -> None:
    creds = Credentials.from_env({"CDSE_CLIENT_ID": " id ", "CDSE_CLIENT_SECRET": " sec "})
    assert creds == Credentials("id", "sec")
    assert "sec" not in repr(creds)
    secret = tmp_path / "cdse.json"
    secret.write_text(json.dumps({"client_id": "a", "client_secret": "b"}))
    assert Credentials.from_env({"CDSE_CREDENTIALS_FILE": str(secret)}) == Credentials("a", "b")
    with pytest.raises(EmsError, match="OAuth clients"):
        Credentials.from_env({})
    with pytest.raises(EmsError, match="cannot read"):
        Credentials.from_env({"CDSE_CREDENTIALS_FILE": str(tmp_path / "missing.json")})


def test_tokens_are_parsed_and_refusals_explained() -> None:
    token = parse_token(200, '{"access_token": "SECRETVALUE", "expires_in": 600}', now=100)
    assert token.expires_at == 700 and "SECRETVALUE" not in repr(token)
    with pytest.raises(EmsError, match=r"HTTP 401.*bad"):
        parse_token(401, '{"error": "invalid_client", "error_description": "bad"}', now=0)
    with pytest.raises(EmsError, match="HTTP 502"):
        parse_token(502, "<html>", now=0)


def test_the_token_is_reused_until_it_nearly_expires() -> None:
    fake, now = FakeCdse(expires_in=300), [1000.0]

    async def go() -> list[str]:
        async with hub(fake, clock=lambda: now[0]) as h:
            first = (await h.token()).access_token
            now[0] += 200  # still valid
            second = (await h.token()).access_token
            now[0] += 60  # inside the renewal margin
            third = (await h.token()).access_token
            return [first, second, third]

    assert asyncio.run(go()) == ["tok1", "tok1", "tok2"]
    assert fake.tokens == 2


def test_bad_credentials_are_reported() -> None:
    fake = FakeCdse()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    with pytest.raises(EmsError, match="token refused"):
        asyncio.run(SentinelHub(Credentials("x", "wrong"), client).token())


def test_the_pu_formula_matches_the_documented_example() -> None:
    # 1024x1024, 4 bands, FLOAT32, 2 samples, orthorectified: 4 * 4/3 * 2 * 2 * 2
    assert estimate_pu(1024, 1024, bands=4, samples=2) == pytest.approx(42.667, abs=1e-3)
    assert estimate_pu(512, 512, bands=3, float32=False, orthorectify=False) == 1
    assert estimate_pu(1, 1, float32=False, orthorectify=False) == pytest.approx(0.01 * 2 / 3)
    assert estimate_pu(512, 512, terrain_correction=True, speckle_filter=True) == pytest.approx(
        2 / 3 * 2 * 2 * 2.5 * 2
    )


def test_tiles_cover_the_grid_exactly() -> None:
    parts = tiles(GRID, 128)
    assert [(r, c) for r, c, _ in parts] == [
        (0, 0),
        (0, 128),
        (0, 256),
        (128, 0),
        (128, 128),
        (128, 256),
    ]
    assert sum(w.width * w.height for _, _, w in parts) == GRID.width * GRID.height
    assert parts[-1][2].width == 300 - 256 and parts[-1][2].height == 200 - 128
    assert len(tiles(GRID)) == 1
    with pytest.raises(ValueError):
        tiles(GRID, 0)


def test_the_request_asks_for_exactly_the_grid() -> None:
    body = backscatter_request(
        GRID, T0, T0 + timedelta(minutes=20), orbit_direction="ascending", speckle_filter=True
    )
    bounds = body["input"]["bounds"]
    assert bounds["bbox"] == [700_000, 4_896_000, 706_000, 4_900_000]
    assert bounds["properties"]["crs"] == "http://www.opengis.net/def/crs/EPSG/0/32632"
    assert (body["output"]["width"], body["output"]["height"]) == (300, 200)
    data = body["input"]["data"][0]
    assert data["type"] == "sentinel-1-grd"
    assert (
        data["dataFilter"]["polarization"] == "DV"
        and data["dataFilter"]["orbitDirection"] == "ASCENDING"
    )
    assert data["dataFilter"]["timeRange"] == {
        "from": "2024-09-21T16:58:00Z",
        "to": "2024-09-21T17:18:00Z",
    }
    assert data["processing"]["orthorectify"] == "true"
    assert data["processing"]["speckleFilter"]["type"] == "LEE"
    assert "FLOAT32" in body["evalscript"] and "s.dataMask" in body["evalscript"]
    assert (
        backscatter_request(GRID, T0, T0 + timedelta(1), polarizations=["VV"])["input"]["data"][0][
            "dataFilter"
        ]["polarization"]
        == "VV"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"end": T0}, "after start"),
        ({"back_coeff": "GAMMA0_TERRAIN", "orthorectify": False}, "orthorectify"),
        ({"polarizations": ["VV", "HH"]}, "polarizations"),
        ({"grid": replace(GRID, crs="+proj=utm")}, "EPSG"),
    ],
)
def test_bad_requests(kwargs: dict[str, Any], message: str) -> None:
    args: dict[str, Any] = {"grid": GRID, "start": T0, "end": T0 + timedelta(minutes=5)} | kwargs
    with pytest.raises(ValueError, match=message):
        backscatter_request(args.pop("grid"), args.pop("start"), args.pop("end"), **args)


def test_evalscript_bands() -> None:
    assert '["HH", "dataMask"]' in evalscript(["HH"]) and "bands: 2" in evalscript(["HH"])


def test_backscatter_is_mosaicked_on_the_grid_and_cached(tmp_path: Path) -> None:
    fake = FakeCdse()
    path = tmp_path / "s1.tif"
    start, end = pass_window(T0)

    async def go() -> Any:
        async with hub(fake) as h:
            first = await fetch_backscatter(
                h, GRID, start, end, path=path, max_pixels=128, tags={"product": "X"}
            )
            again = await fetch_backscatter(h, GRID, start, end, path=path, max_pixels=128)
            return first, again, h.pu_estimated

    first, again, spent = asyncio.run(go())
    assert len(fake.requests) == 6  # tiled once, then read from disk
    assert not first.cached and again.cached and again.pu == 0
    assert spent == pytest.approx(first.pu)
    with rasterio.open(path) as src:
        assert Grid.from_transform(src.crs, src.transform, src.width, src.height) == GRID
        assert src.descriptions == ("VV", "VH", "dataMask")
        assert src.tags()["product"] == "X" and "Sentinel" in src.tags()["source"]
        xs, ys, mask = src.read()
    # every pixel holds its own centre: the tiles landed where they belong
    centres_x = GRID.x0 + (np.arange(GRID.width) + 0.5) * GRID.resolution_m
    centres_y = GRID.y0 - (np.arange(GRID.height) + 0.5) * GRID.resolution_m
    assert np.allclose(xs[0], centres_x) and np.allclose(ys[:, 0], centres_y)
    assert mask.min() == 1
    assert not list(tmp_path.glob("*.part"))


def test_the_budget_is_checked_before_any_request(tmp_path: Path) -> None:
    fake = FakeCdse()

    async def go() -> None:
        async with hub(fake) as h:
            await fetch_backscatter(
                h, GRID, T0, T0 + timedelta(minutes=5), path=tmp_path / "x.tif", budget_pu=0.01
            )

    with pytest.raises(EmsError, match="over the budget"):
        asyncio.run(go())
    assert fake.requests == [] and fake.tokens == 0


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (
            400,
            {"error": {"status": 400, "reason": "Bad Request", "message": "Invalid bbox"}},
            "400: Invalid bbox",
        ),
        (403, {"message": "quota exceeded"}, "403: quota exceeded"),
        (500, ["odd"], "500"),
    ],
)
def test_process_errors_are_explained(status: int, body: Any, message: str, tmp_path: Path) -> None:
    fake = FakeCdse(status=status, body=body)

    async def go() -> None:
        async with hub(fake) as h:
            await fetch_backscatter(h, GRID, T0, T0 + timedelta(minutes=5), path=tmp_path / "x.tif")

    with pytest.raises(EmsError, match=message):
        asyncio.run(go())
    assert not (tmp_path / "x.tif").exists()


def test_a_misaligned_or_broken_answer_is_refused(tmp_path: Path) -> None:
    def shifted(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        body = json.loads(request.content)
        body["input"]["bounds"]["bbox"][0] += 5  # half a pixel off
        body["input"]["bounds"]["bbox"][2] += 5
        return httpx.Response(200, content=_tiff(body))

    def html(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, text="<html>")

    for transport, message in (
        (shifted, "not on the asked grid"),
        (html, "did not return a GeoTIFF"),
    ):
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        with pytest.raises(EmsError, match=message):
            asyncio.run(
                fetch_backscatter(
                    SentinelHub(CREDS, client),
                    GRID,
                    T0,
                    T0 + timedelta(minutes=5),
                    path=tmp_path / "x.tif",
                )
            )


def test_paths_depend_on_grid_and_time_not_on_the_product(tmp_path: Path) -> None:
    a = backscatter_path(GRID, T0, T0 + timedelta(1), activation="EMSR1", directory=tmp_path)
    assert a.parent == tmp_path / "EMSR1" / "sentinel-1" and a.name.startswith(
        "S1_20240921T1658_20m_"
    )
    assert a == backscatter_path(
        GRID, T0, T0 + timedelta(1), activation="EMSR1", directory=tmp_path
    )
    assert a != backscatter_path(
        replace(GRID, width=301), T0, T0 + timedelta(1), activation="EMSR1", directory=tmp_path
    )
    assert a != backscatter_path(
        GRID, T0, T0 + timedelta(1), activation="EMSR1", directory=tmp_path, orbit="x"
    )


def test_plan_and_make_a_pair(tmp_path: Path) -> None:
    fake = FakeCdse()

    async def go() -> Any:
        http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
        ems = RapidMapping(http, directory=tmp_path)
        plan = await plan_pair(
            ems, event(), resolution_m=100, max_gap=timedelta(hours=48), min_coverage=0.1
        )
        async with SentinelHub(CREDS, http) as h:
            return plan, await make_pair(h, plan)

    plan, (image, labels) = asyncio.run(go())
    assert plan.scene_pass is not None and plan.coverage >= 0.1 and plan.pu > 0
    assert image.path.parent == tmp_path / "EMSR762" / "sentinel-1"
    label = LabelRaster.from_geotiff(labels)
    with rasterio.open(image.path) as src:
        assert Grid.from_transform(src.crs, src.transform, src.width, src.height) == label.grid
    assert set(np.unique(label.labels)) >= {FLOODED, IGNORE}
    manifest = json.loads(
        labels.with_name(labels.name.replace("_labels.tif", "_pair.json")).read_text()
    )
    assert (
        manifest["product"] == "AOI01_DEL_PRODUCT" and manifest["made_from"][0]["sensor"] == "PAZ"
    )
    assert manifest["gap_hours"] == round(plan.scene_pass.gap_hours, 2)
    assert manifest["bands"] == ["VV", "VH", "dataMask"] and len(manifest["credit"]) == 2


def test_no_pass_means_a_reason_not_a_request(tmp_path: Path) -> None:
    fake = FakeCdse()

    async def plan(**kwargs: Any) -> Any:
        http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
        return await plan_pair(
            RapidMapping(http, directory=tmp_path), event(), resolution_m=100, **kwargs
        )

    tight = asyncio.run(plan(max_gap=timedelta(hours=1), min_coverage=0.1))
    assert tight.scene_pass is None and "h away" in (tight.reason or "")
    blind = asyncio.run(plan(min_coverage=1.01))
    assert blind.scene_pass is None and "no pass covers" in (blind.reason or "")
    with pytest.raises(EmsError, match="no pass covers"):
        asyncio.run(make_pair(SentinelHub(CREDS, httpx.AsyncClient()), blind))
    assert fake.requests == []


def test_the_pairs_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeCdse()
    monkeypatch.setenv("CDSE_CLIENT_ID", "my-client")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "s3cret")
    real = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(fake)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    argv = [
        "--dir",
        str(tmp_path),
        "pairs",
        "EMSR762",
        "--aoi",
        "1",
        "--resolution",
        "100",
        "--max-gap-hours",
        "48",
        "--min-coverage",
        "0.1",
    ]
    assert main([*argv, "--dry-run"]) == 0
    out = capsys.readouterr()
    assert "AOI01_DEL_PRODUCT" in out.out and "PU estimated" in out.err and fake.requests == []

    assert main([*argv, "--budget-pu", "0.001"]) == 2
    assert "over --budget-pu" in capsys.readouterr().err and fake.requests == []

    assert main(argv) == 0
    out = capsys.readouterr()
    assert ".tif" in out.out and "PU spent" in out.err
    sent = len(fake.requests)
    assert sent >= 1
    assert main(argv) == 0  # all cached now
    assert len(fake.requests) == sent
    assert "cached" in capsys.readouterr().out


def test_pairs_labels_floods_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A landslide over the whole AOI must not become water in the labels."""
    from test_client import LAYER

    fake = FakeCdse()
    layer = json.loads((FIXTURES / LAYER).read_text())
    aoi = event().product.aoi_extent
    layer["features"].append(
        {
            "type": "Feature",
            "geometry": aoi,
            "properties": {"event_type": "6-Mass Movement", "notation": "Landslide"},
        }
    )

    def with_landslide(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://s3.example/layer.json":
            return httpx.Response(200, json=layer)
        return fake(request)

    monkeypatch.setenv("CDSE_CLIENT_ID", "my-client")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "s3cret")
    real = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(with_landslide)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    argv = [
        "--dir",
        str(tmp_path),
        "pairs",
        "EMSR762",
        "--aoi",
        "1",
        "--resolution",
        "100",
        "--max-gap-hours",
        "48",
        "--min-coverage",
        "0.1",
    ]
    assert main(argv) == 0
    (labels,) = (tmp_path / "EMSR762" / "sentinel-1").glob("AOI01_DEL_PRODUCT_*_labels.tif")
    flooded = np.count_nonzero(LabelRaster.from_geotiff(labels).labels == FLOODED)
    observed = np.count_nonzero(LabelRaster.from_geotiff(labels).labels != IGNORE)
    assert 0 < flooded < 0.01 * observed  # the 1.19 km² of flood, not the whole AOI

    # labels written wrong (e.g. by 0.5.0) are corrected by running again, for free
    stale = LabelRaster.from_geotiff(labels)
    LabelRaster(np.full_like(stale.labels, FLOODED), stale.grid, stale.meta).to_geotiff(labels)
    sent = len(fake.requests)
    assert main(argv) == 0
    assert np.count_nonzero(LabelRaster.from_geotiff(labels).labels == FLOODED) == flooded
    assert len(fake.requests) == sent  # the image came from the cache


def with_references(request: httpx.Request) -> httpx.Response:
    """STAC that also knows the passes before the flood: orbit 44 twelve and
    twenty-four days earlier, and an orbit 168 pass in between (wrong geometry)."""
    from copernicus_ems.scenes import STAC_SEARCH_URL

    if str(request.url) != STAC_SEARCH_URL:
        return stac_handler(request)
    body = json.loads(request.content)
    start, end = (
        datetime.fromisoformat(t.replace("Z", "+00:00")) for t in body["datetime"].split("/")
    )
    page = json.loads(STAC.read_text())
    s1 = [f for f in page["features"] if f["collection"] == "sentinel-1-grd"]
    asc = next(f for f in s1 if f["properties"]["sat:relative_orbit"] == 44)
    desc = next(f for f in s1 if f["properties"]["sat:relative_orbit"] == 168)
    extra = []
    for base, days, take in ((asc, 12, "1"), (asc, 24, "2"), (desc, 9, "3")):
        f = json.loads(json.dumps(base))
        when = datetime.fromisoformat(f["properties"]["datetime"].replace("Z", "+00:00"))
        when -= timedelta(days=days)
        f["id"] = f"{f['id']}_minus{days}"
        f["properties"]["datetime"] = when.isoformat().replace("+00:00", "Z")
        f["properties"]["start_datetime"] = f["properties"]["datetime"]
        f["properties"]["eopf:datatake_id"] = f"ref{take}"
        extra.append(f)
    feats = [
        f
        for f in s1 + extra
        if start
        <= datetime.fromisoformat(f["properties"]["datetime"].replace("Z", "+00:00"))
        <= end
    ]
    page["features"] = feats
    page["links"] = []
    return httpx.Response(200, json=page)


def test_a_reference_pass_of_the_same_orbit(tmp_path: Path) -> None:
    fake = FakeCdse()

    def transport(request: httpx.Request) -> httpx.Response:
        return with_references(request) if "stac" in str(request.url) else fake(request)

    async def go(**kwargs: Any) -> Any:
        http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        ems = RapidMapping(http, directory=tmp_path)
        plan = await plan_pair(
            ems, event(), resolution_m=100, max_gap=timedelta(hours=48), min_coverage=0.1
        )
        ref = await plan_reference(ems, plan, min_coverage=0.1, **kwargs)
        return ems, http, plan, ref

    _ems, http, plan, ref = asyncio.run(go())
    assert plan.scene_pass is not None
    assert (
        ref.scene_pass is not None
        and ref.relative_orbit == plan.scene_pass.scenes[0].relative_orbit
    )
    lead = plan.scene_pass.time - ref.scene_pass.time
    assert lead == timedelta(days=12) and ref.pu == plan.pu
    assert ref.backscatter_path != plan.backscatter_path

    # the flood began 13 days before the pass: the 12-day pass is already wet
    early = plan.scene_pass.time - timedelta(days=13)
    *_, older = asyncio.run(go(before=early))
    assert older.scene_pass is not None
    assert plan.scene_pass.time - older.scene_pass.time == timedelta(days=24)
    *_, none = asyncio.run(go(before=early, lookback=timedelta(days=5)))
    assert none.scene_pass is None and "relative orbit 44" in (none.reason or "")

    async def make() -> Any:
        async with SentinelHub(CREDS, http) as h:
            return await make_pair(h, plan, reference=ref)

    _image, labels = asyncio.run(make())
    assert ref.backscatter_path is not None and ref.backscatter_path.is_file()
    with rasterio.open(ref.backscatter_path) as src:
        assert Grid.from_transform(src.crs, src.transform, src.width, src.height) == plan.grid
    manifest = json.loads(
        labels.with_name(labels.name.replace("_labels.tif", "_pair.json")).read_text()
    )
    assert manifest["reference"]["image"] == ref.backscatter_path.name
    assert manifest["reference"]["lead_days"] == 12 and manifest["relative_orbit"] == 44
    assert len(fake.requests) == 2


def test_the_pairs_command_with_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeCdse()
    monkeypatch.setenv("CDSE_CLIENT_ID", "my-client")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "s3cret")
    real = httpx.AsyncClient

    def transport(request: httpx.Request) -> httpx.Response:
        return with_references(request) if "stac" in str(request.url) else fake(request)

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(transport)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    argv = [
        "--dir", str(tmp_path), "pairs", "EMSR762", "--aoi", "1", "--resolution", "100",
        "--max-gap-hours", "48", "--min-coverage", "0.1", "--reference",
    ]  # fmt: skip
    assert main([*argv, "--dry-run"]) == 0
    out = capsys.readouterr()
    assert "reference" in out.out and "2 images to fetch" in out.err and fake.requests == []
    assert main(argv) == 0
    assert len(fake.requests) == 2
    assert main(argv) == 0  # all cached
    assert len(fake.requests) == 2 and "0 images to fetch" in capsys.readouterr().err


def test_send_routes_every_request_through_the_caller(tmp_path: Path) -> None:
    """Like an application's fetch_with_retry: one shared client, its own policy."""
    from copernicus_ems.sentinelhub import pu_to_spend

    fake = FakeCdse()
    seen: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        return with_references(request) if "stac" in str(request.url) else fake(request)

    async def go() -> Any:
        shared = httpx.AsyncClient(transport=httpx.MockTransport(transport))

        async def send(request: httpx.Request) -> httpx.Response:
            seen.append(f"{request.method} {request.url.host}")
            return await shared.send(request)

        async with RapidMapping(directory=tmp_path, send=send) as ems:
            plan = await plan_pair(
                ems, event(), resolution_m=100, max_gap=timedelta(hours=48), min_coverage=0.1
            )
            ref = await plan_reference(ems, plan, min_coverage=0.1)
        before = pu_to_spend([plan, plan], [ref])
        assert before == plan.pu + ref.pu  # a plan listed twice is one image
        async with SentinelHub(CREDS, send=send) as hub:
            await make_pair(hub, plan, reference=ref)
        assert pu_to_spend([plan], [ref]) == 0  # both cached now
        await shared.aclose()
        return plan

    asyncio.run(go())
    hosts = {s.split(" ", 1)[1] for s in seen}
    assert "stac.dataspace.copernicus.eu" in hosts and "sh.dataspace.copernicus.eu" in hosts
    assert "POST identity.dataspace.copernicus.eu" in seen
    assert len(fake.requests) == 2 and sum(s.startswith("POST sh.") for s in seen) == 2


def test_send_means_no_retry_here_and_not_with_a_client() -> None:
    calls = 0

    async def send(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "busy"}, request=request)

    async def go() -> None:
        async with SentinelHub(CREDS, send=send) as hub:
            with pytest.raises(EmsError, match="HTTP 503"):
                await hub.token()

    asyncio.run(go())
    assert calls == 1  # the caller's policy decides; no RetryTransport underneath
    with pytest.raises(ValueError, match="not both"):
        SentinelHub(CREDS, httpx.AsyncClient(), send=send)
    with pytest.raises(ValueError, match="not both"):
        RapidMapping(httpx.AsyncClient(), send=send)
