from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from copernicus_ems import (
    OBSERVED_EVENT,
    InitReport,
    RapidMapping,
    init,
    read_activations,
    read_products,
)
from copernicus_ems.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
DETAIL = (FIXTURES / "detail_EMSR762.json").read_bytes()
LAYER = (FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json").read_bytes()
PAGE = {
    "count": 2,
    "next": None,
    "results": [
        {
            "code": "EMSR762",
            "name": "Flood in Emilia-Romagna",
            "category": "Flood",
            "eventTime": "2024-10-18T00:00:00",
            "countries": ["Italy"],
        },
        {
            "code": "EMSR935",
            "category": "Flood",
            "eventTime": "2026-10-19T03:10:00",
            "countries": ["Spain"],
        },
    ],
}
#: The observedEventA layers of the EMSR762 detail.
LAYERS = 9
EMPTY = {"type": "FeatureCollection", "features": []}

Handler = Callable[[httpx.Request], httpx.Response]


def service(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "public-activations-info" in url:
        return httpx.Response(200, json=PAGE)
    if "public-activations/" in url:
        if "code=EMSR762" not in url:
            return httpx.Response(404)
        return httpx.Response(200, content=DETAIL)
    if url.endswith("EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json"):
        return httpx.Response(200, content=LAYER)
    if url.endswith("_observedEventA_v1.json"):
        return httpx.Response(200, json=EMPTY)
    raise AssertionError(url)


def run_init(
    directory: Path | None = None,
    handler: Handler = service,
    seen: list[str] | None = None,
    **options: Any,
) -> InitReport:
    def recording(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return handler(request)

    async def body() -> InitReport:
        async with httpx.AsyncClient(transport=httpx.MockTransport(recording)) as client:
            report = await init(directory, client=client, **options)
            assert not client.is_closed  # the caller's client stays open
            return report

    options.setdefault("backoff_s", 0)
    return asyncio.run(body())


def test_init_saves_list_details_and_layers(data_dir: Path) -> None:
    report = run_init()
    assert report.directory == str(data_dir)
    assert report.ok
    assert report.failed == {} and report.skipped == []
    assert report.downloaded[:2] == ["activations.json", "EMSR762/detail.json"]
    layers = report.downloaded[2:]
    assert len(layers) == LAYERS
    assert all(
        n.startswith("EMSR762/") and n.endswith("_observedEventA_v1.geojson") for n in layers
    )
    assert report.bytes == sum((data_dir / n).stat().st_size for n in report.downloaded)
    assert not list(data_dir.rglob("*.part"))
    # Spain is not selected: no detail asked for it
    assert not (data_dir / "EMSR935").exists()

    saved = json.loads((data_dir / "activations.json").read_bytes())
    assert [r["code"] for r in saved["results"]] == ["EMSR762", "EMSR935"]
    assert saved["next"] is None
    assert report.as_dict()["ok"] is True


def test_init_then_offline(data_dir: Path) -> None:
    run_init()
    assert [a.code for a in read_activations(country="Italy")] == ["EMSR762"]
    assert [a.code for a in read_activations(country="Spain", categories=["flood"])] == ["EMSR935"]
    products = read_products("EMSR762")
    assert len(products) == 10

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async def body() -> Any:
        client = httpx.AsyncClient(transport=httpx.MockTransport(offline))
        async with RapidMapping(client) as ems:
            return await ems.layer(products[0], OBSERVED_EVENT)

    layer = asyncio.run(body())
    assert layer is not None and len(layer["features"]) == 43


def test_init_twice_downloads_nothing_new(data_dir: Path) -> None:
    run_init()
    seen: list[str] = []
    report = run_init(seen=seen)
    assert report.downloaded == []
    assert report.bytes == 0
    assert len(report.skipped) == 2 + LAYERS
    # the list and the detail are asked again (they change); the layers are not
    assert len(seen) == 2


def test_a_changed_detail_is_rewritten(data_dir: Path) -> None:
    run_init()
    (data_dir / "EMSR762" / "detail.json").write_text('{"results": []}')
    report = run_init()
    assert report.downloaded == ["EMSR762/detail.json"]


def test_a_broken_layer_on_disk_is_downloaded_again(data_dir: Path) -> None:
    run_init()
    broken = data_dir / "EMSR762" / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.geojson"
    broken.write_text("{not json")
    report = run_init()
    assert report.downloaded == [broken.relative_to(data_dir).as_posix()]
    assert broken.read_bytes() == LAYER


def failing(match: str, status: int = 500, times: int = 1_000) -> tuple[Handler, list[int]]:
    """``service``, except that URLs containing ``match`` answer ``status`` ``times`` times."""
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if match in str(request.url):
            calls[0] += 1
            if calls[0] <= times:
                return httpx.Response(status)
        return service(request)

    return handler, calls


def test_one_failed_layer_does_not_stop_the_rest(data_dir: Path) -> None:
    handler, calls = failing("AOI03_DEL_MONIT01_observedEventA")
    report = run_init(handler=handler, retries=0)
    assert not report.ok
    (name,) = report.failed
    assert name == "EMSR762/EMSR762_AOI03_DEL_MONIT01_observedEventA_v1.geojson"
    assert "500" in report.failed[name]
    assert len(report.downloaded) == 2 + LAYERS - 1
    assert calls[0] == 1  # retries=0: one request, the caller retries
    # the next run fetches only what is missing
    report = run_init()
    assert report.ok
    assert report.downloaded == [name]


def test_retries_on_503_and_network_errors(data_dir: Path) -> None:
    handler, calls = failing("public-activations-info", status=503, times=2)
    report = run_init(handler=handler, retries=2)
    assert report.ok
    assert calls[0] == 3

    attempts = [0]

    def flaky(request: httpx.Request) -> httpx.Response:
        if "AOI01_DEL_PRODUCT_observedEventA" in str(request.url):
            attempts[0] += 1
            raise httpx.ConnectError("reset", request=request)
        return service(request)

    report = run_init(data_dir.parent / "other", handler=flaky, retries=1)
    assert attempts[0] == 2
    assert list(report.failed) == ["EMSR762/EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.geojson"]


def test_a_bad_answer_is_a_failure_not_a_file(data_dir: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "AOI01_DEL_PRODUCT_observedEventA" in str(request.url):
            return httpx.Response(200, text="<html>maintenance</html>")
        return service(request)

    report = run_init(handler=handler)
    assert list(report.failed) == ["EMSR762/EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.geojson"]
    assert not (Path(report.directory) / next(iter(report.failed))).exists()


def test_without_the_list_the_saved_copy_is_used(data_dir: Path) -> None:
    handler, _ = failing("public-activations-info")
    report = run_init(handler=handler, retries=0)
    assert list(report.failed) == ["activations.json"]
    assert report.downloaded == []  # nothing to select from

    run_init()
    handler, _ = failing("public-activations-info")
    report = run_init(handler=handler, retries=0)
    assert list(report.failed) == ["activations.json"]
    assert len(report.skipped) == 1 + LAYERS  # detail and layers, from the saved list


def test_without_the_detail_the_saved_copy_is_used(data_dir: Path) -> None:
    handler, _ = failing("public-activations/")
    report = run_init(handler=handler, retries=0)
    assert list(report.failed) == ["EMSR762/detail.json"]
    assert report.downloaded == ["activations.json"]

    run_init()
    report = run_init(handler=handler, retries=0)
    assert list(report.failed) == ["EMSR762/detail.json"]
    assert len(report.skipped) == 1 + LAYERS


def test_a_disk_that_cannot_be_written_raises(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    with pytest.raises(OSError):
        run_init(blocked)


def test_selection_and_kinds(tmp_path: Path) -> None:
    report = run_init(tmp_path, country=None, categories=None, kinds=())
    # EMSR935 is selected too; this fake service only knows EMSR762
    assert report.failed.keys() == {"EMSR935/detail.json"}
    assert report.downloaded == ["activations.json", "EMSR762/detail.json"]

    report = run_init(tmp_path / "since", since=date(2025, 1, 1))
    assert report.downloaded == ["activations.json"]


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        run_init(concurrency=0)
    with pytest.raises(ValueError, match="retries"):
        run_init(retries=-1)


def test_init_opens_and_closes_its_own_client(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    opened: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def fake(**kwargs: Any) -> httpx.AsyncClient:
        client = real(transport=httpx.MockTransport(service), **kwargs)
        opened.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", fake)
    report = asyncio.run(init())
    assert report.ok
    assert opened and opened[0].is_closed


def test_cli_init(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def client(handler: Handler = service) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    assert main(["init"], client=client()) == 0
    out = capsys.readouterr().out
    assert f"{2 + LAYERS} downloaded, 0 skipped, 0 failed" in out
    assert str(data_dir) in out

    assert main(["init", "--json"], client=client()) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert len(report["skipped"]) == 2 + LAYERS

    handler, _ = failing("AOI01_DEL_PRODUCT_observedEventA")
    target = data_dir.parent / "cli"
    args = ["--dir", str(target), "init", "--retries", "0", "--layer", OBSERVED_EVENT]
    assert main(args, client=client(handler)) == 1
    captured = capsys.readouterr()
    assert "failed\tEMSR762/EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.geojson" in captured.err
    assert (target / "activations.json").is_file()

    other = data_dir.parent / "world"
    args = ["--dir", str(other), "init", "--all-countries", "--category", "flood"]
    assert main([*args, "--until", "2025-01-01", "--json"], client=client()) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
