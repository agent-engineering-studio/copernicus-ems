from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx
import pytest

from copernicus_ems import (
    ENV_DATA_DIR,
    RapidMapping,
    RetryTransport,
    default_directory,
    fetch_activations,
    fetch_layer,
    fetch_observed_events,
    fetch_products,
)

FIXTURES = Path(__file__).parent / "fixtures"
LAYER = "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json"


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "public-activations-info" in url:
        if "offset" in url:
            return httpx.Response(
                200,
                json={
                    "count": 4,
                    "next": None,
                    "results": [
                        {
                            "code": "EMSR1",
                            "eventTime": "2023-01-01T00:00:00",
                            "countries": ["Italy"],
                        }
                    ],
                },
            )
        return httpx.Response(200, content=(FIXTURES / "activations_page.json").read_bytes())
    if "public-activations/" in url:
        return httpx.Response(200, content=(FIXTURES / "detail_EMSR762.json").read_bytes())
    if url.endswith(LAYER):
        return httpx.Response(302, headers={"location": "https://s3.example/layer.json"})
    if url == "https://s3.example/layer.json":
        return httpx.Response(200, content=(FIXTURES / LAYER).read_bytes())
    if url.endswith(".json"):
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
    raise AssertionError(url)


def run(
    body: Callable[[httpx.AsyncClient], Coroutine[Any, Any, Any]],
    seen: list[str] | None = None,
) -> Any:
    def recording(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return handler(request)

    async def main() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(recording)) as client:
            return await body(client)

    return asyncio.run(main())


def test_activations_follow_the_pages() -> None:
    acts = run(lambda c: fetch_activations(c, page_size=3))
    assert [a.code for a in acts][-1] == "EMSR1"
    assert len(acts) == 4


def test_observed_floods_come_from_the_geojson_layers() -> None:
    events = run(lambda c: fetch_observed_events(c, "EMSR762"))
    assert len(events) == 1  # the other layers are empty in this fake
    (only,) = events
    assert only.product.label == "AOI01_DEL_PRODUCT"
    assert len(only.features) == 43


def test_products_of_an_activation() -> None:
    products = run(lambda c: fetch_products(c, "EMSR762"))
    without = [p for p in products if "observedEventA" not in p.layers]
    events = run(lambda c: fetch_observed_events(c, "EMSR762", event_types=None, notations=None))
    assert len(events) == 1
    assert len(products) > len(without)


def test_layers_are_saved_and_read_back_without_the_network(data_dir: Path) -> None:
    first: list[str] = []
    run(lambda c: fetch_observed_events(c, "EMSR762"), first)
    saved = data_dir / "EMSR762" / LAYER.replace(".json", ".geojson")
    assert saved.is_file()
    assert not list(data_dir.rglob("*.part"))

    again: list[str] = []
    events = run(lambda c: fetch_observed_events(c, "EMSR762"), again)
    assert len(events[0].features) == 43
    assert [u for u in again if "s3" in u or u.endswith(".json")] == []
    assert len(again) == 1  # only the activation detail


def test_an_explicit_directory_wins(tmp_path: Path, data_dir: Path) -> None:
    async def body(c: httpx.AsyncClient) -> Any:
        products = await fetch_products(c, "EMSR762")
        (product,) = [p for p in products if p.label == "AOI01_DEL_PRODUCT"]
        return await fetch_layer(c, product, "observedEventA", directory=tmp_path / "mine")

    assert run(body) is not None
    assert list((tmp_path / "mine" / "EMSR762").glob("*.geojson"))
    assert not data_dir.exists()


def test_missing_layer_kind_is_none() -> None:
    async def body(c: httpx.AsyncClient) -> Any:
        return await fetch_layer(c, (await fetch_products(c, "EMSR762"))[0], "noSuchLayer")

    assert run(body) is None


def test_default_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, " /data/ems ")
    assert default_directory() == Path("/data/ems")
    monkeypatch.delenv(ENV_DATA_DIR)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_directory() == tmp_path / "copernicus-ems"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert default_directory() == Path("~/.cache/copernicus-ems").expanduser()


def test_rapid_mapping_with_your_client(data_dir: Path) -> None:
    async def body(c: httpx.AsyncClient) -> Any:
        async with RapidMapping(c) as ems:
            assert ems.directory == data_dir
            italy = await ems.activations(country="Italy", categories=["flood"])
            events = await ems.observed_events("EMSR762")
            layer = await ems.layer(events[0].product, "observedEventA")
        assert not c.is_closed  # yours: left open
        return italy, events, layer

    italy, events, layer = run(body)
    assert all("Italy" in a.countries for a in italy)
    assert len(events[0].features) == 43
    assert layer is not None


def test_rapid_mapping_closes_the_client_it_opened(tmp_path: Path) -> None:
    async def main() -> bool:
        ems = RapidMapping(directory=tmp_path)
        assert ems.directory == tmp_path
        await ems.aclose()
        return ems._client.is_closed

    assert asyncio.run(main())


def retrying(answers: list[int | type[httpx.TransportError]]) -> tuple[RetryTransport, list[float]]:
    waits: list[float] = []

    def answer(request: httpx.Request) -> httpx.Response:
        got = answers.pop(0)
        if isinstance(got, int):
            return httpx.Response(got)
        raise got("down", request=request)

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    return RetryTransport(httpx.MockTransport(answer), retries=2, sleep=sleep), waits


def status_after(transport: RetryTransport) -> int:
    async def main() -> int:
        async with httpx.AsyncClient(transport=transport) as client:
            return (await client.get("https://x.test/")).status_code

    return asyncio.run(main())


def test_retry_until_it_answers() -> None:
    transport, waits = retrying([502, httpx.ConnectError, 200])
    assert status_after(transport) == 200
    assert waits == [2.0, 4.0]


def test_retry_gives_up_and_returns_the_last_answer() -> None:
    transport, waits = retrying([503, 503, 503])
    assert status_after(transport) == 503
    assert len(waits) == 2


def test_retry_raises_the_last_network_error() -> None:
    transport, _ = retrying([httpx.ConnectError, httpx.ConnectError, httpx.ReadTimeout])
    with pytest.raises(httpx.ReadTimeout):
        status_after(transport)


def test_other_errors_are_not_retried() -> None:
    transport, waits = retrying([404])
    assert status_after(transport) == 404
    assert waits == []


def test_layers_download_concurrently_and_keep_the_product_order(tmp_path: Path) -> None:
    in_flight = peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        if "public-activations/" not in str(request.url):
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
        return handler(request)

    async def main(concurrency: int) -> list[str]:
        nonlocal peak
        peak = 0
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            events = await fetch_observed_events(
                client,
                "EMSR762",
                event_types=None,
                notations=None,
                concurrency=concurrency,
                directory=tmp_path / str(concurrency),
            )
        return [e.product.label for e in events]

    assert asyncio.run(main(1)) == ["AOI01_DEL_PRODUCT"]
    assert peak == 1
    asyncio.run(main(3))
    assert peak == 3


def test_concurrency_must_be_positive() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        run(lambda c: fetch_observed_events(c, "EMSR762", concurrency=0))


def test_rapid_mapping_filters_activations_by_date() -> None:
    from datetime import date

    async def body(c: httpx.AsyncClient) -> Any:
        return await RapidMapping(c).activations(since=date(2026, 10, 9), until=date(2026, 10, 9))

    assert [a.code for a in run(body)] == ["EMSR934"]


def test_send_with_a_gzip_server() -> None:
    """The caller's client decodes gzip: the library must not decode it again."""
    import gzip

    from copernicus_ems import SendTransport

    body = json.dumps({"ok": True, "text": "x" * 500}).encode()

    def gzipped(request: httpx.Request) -> httpx.Response:
        packed = gzip.compress(body)
        return httpx.Response(
            200,
            content=packed,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(packed))},
        )

    async def go() -> httpx.Response:
        shared = httpx.AsyncClient(transport=httpx.MockTransport(gzipped))

        async def send(request: httpx.Request) -> httpx.Response:
            return await shared.send(request)

        async with httpx.AsyncClient(transport=SendTransport(send)) as client:
            response = await client.get("https://example.test/x")
        await shared.aclose()
        return response

    response = asyncio.run(go())
    assert response.json()["ok"] is True and response.content == body
    assert "content-encoding" not in response.headers
    assert response.headers.get("content-length") in (None, str(len(body)))
