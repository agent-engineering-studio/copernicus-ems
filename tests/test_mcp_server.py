from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client
from test_client import handler

from copernicus_ems.mcp_server import ATTRIBUTION, build_server


def call(
    tool: str,
    arguments: dict[str, Any],
    transport: httpx.MockTransport | None = None,
    directory: Path | None = None,
) -> tuple[bool, Any]:
    """(is_error, structured content or text) of one tool call, over the MCP protocol."""

    async def main() -> tuple[bool, Any]:
        http = httpx.AsyncClient(transport=transport or httpx.MockTransport(handler))
        async with http, Client(build_server(http, directory=directory)) as client:
            result = await client.call_tool(tool, arguments)
        if result.is_error:
            return True, result.content[0].text  # type: ignore[union-attr]
        return False, result.structured_content

    return asyncio.run(main())


def test_the_tools_and_prompt_are_advertised() -> None:
    async def main() -> tuple[Any, Any, Any]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with http, Client(build_server(http)) as client:
            tools = await client.list_tools()
            prompts = await client.list_prompts()
            prompt = await client.get_prompt("situation_report", {"code": "EMSR762"})
            return tools, prompts, prompt

    tools, prompts, prompt = asyncio.run(main())
    by_name = {t.name: t for t in tools.tools}
    assert set(by_name) == {
        "list_activations",
        "activation_summary",
        "compare_products",
        "export_observed_events",
        "find_scenes",
    }
    assert by_name["activation_summary"].annotations.read_only_hint is True
    assert by_name["export_observed_events"].annotations.read_only_hint is False
    assert by_name["activation_summary"].output_schema is not None
    assert [p.name for p in prompts.prompts] == ["situation_report"]
    text = prompt.messages[0].content.text
    assert "EMSR762" in text and ATTRIBUTION in text


def test_server_instructions_explain_the_three_states() -> None:
    server = build_server(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert "unknown" in (server.instructions or "")
    assert ATTRIBUTION in (server.instructions or "")


def test_list_activations_newest_first() -> None:
    error, out = call("list_activations", {"limit": 2})
    assert not error
    assert out["total"] == 4 and out["returned"] == 2
    assert [a["code"] for a in out["activations"]] == ["EMSR935", "EMSR934"]

    _, out = call("list_activations", {"categories": ["flood"], "since": "2026-10-02"})
    assert [a["code"] for a in out["activations"]] == ["EMSR935"]
    assert out["activations"][0]["countries"] == ["Spain"]


def test_a_bad_date_is_a_tool_error() -> None:
    error, text = call("list_activations", {"since": "last week"})
    assert error and "YYYY-MM-DD" in text


def test_activation_summary_gives_facts_not_geometry() -> None:
    error, out = call("activation_summary", {"code": "emsr762"})
    assert not error
    assert out["code"] == "EMSR762" and out["attribution"] == ATTRIBUTION
    (product,) = out["products"]
    assert product["product"] == "AOI01_DEL_PRODUCT"
    assert product["features"] == 43
    assert product["event_km2"] == pytest.approx(1.19, abs=0.02)
    assert 1700 < product["observed_km2"] < 2000
    assert len(product["bbox_lon_lat"]) == 4
    (aoi,) = out["aois"]
    assert aoi["latest_product"] == "AOI01_DEL_PRODUCT" and aoi["products"] == 1
    assert aoi["product_types"] == ["DEL"] and product["product_type"] == "DEL"
    assert "coordinates" not in json.dumps(out)


def test_other_hazards() -> None:
    _, out = call("activation_summary", {"code": "EMSR762", "hazard": "landslide"})
    assert out["products"] == [] and out["aois"] == []
    _, out = call("activation_summary", {"code": "EMSR762", "hazard": "all"})
    assert out["products"][0]["features"] == 43
    error, _ = call("activation_summary", {"code": "EMSR762", "hazard": "fire"})
    assert error


def test_compare_products_of_an_aoi() -> None:
    _, out = call("compare_products", {"code": "EMSR762", "aoi_number": 1})
    (only,) = out["timeline"]
    assert only["change_km2"] is None and only["observed_time"].startswith("20")
    assert out["aoi_name"] == "Brisighella"
    error, text = call("compare_products", {"code": "EMSR762", "aoi_number": 99})
    assert error and "AOI 99" in text


def test_export_writes_files_and_returns_paths(tmp_path: Path, data_dir: Path) -> None:
    _, out = call("export_observed_events", {"code": "EMSR762"})
    events = Path(out["events_path"])
    assert events.parent == (data_dir / "exports").resolve()
    collection = json.loads(events.read_text())
    assert len(collection["features"]) == out["features"] == 43
    assert collection["features"][0]["properties"]["activation"] == "EMSR762"
    masks = json.loads(Path(out["masks_path"]).read_text())
    assert masks["features"][0]["geometry"]["type"] == "Polygon"

    _, out = call("export_observed_events", {"code": "EMSR762", "with_masks": False})
    assert out["masks_path"] is None


def test_export_into_a_given_directory(tmp_path: Path) -> None:
    from copernicus_ems import RapidMapping
    from copernicus_ems.mcp_server import export_observed_events

    async def main() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await export_observed_events(
                RapidMapping(http), "EMSR762", directory=tmp_path / "out"
            )

    assert Path(asyncio.run(main())["events_path"]).parent == (tmp_path / "out").resolve()


@pytest.mark.parametrize("code", ["762", "EMSRxyz", ""])
def test_bad_codes(code: str) -> None:
    error, text = call("activation_summary", {"code": code})
    assert error and "activation code" in text


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (lambda r: httpx.Response(500), "answered 500"),
        (lambda r: httpx.Response(200, text="<html>"), "did not return JSON"),
    ],
)
def test_service_failures_are_tool_errors(answer: Any, message: str) -> None:
    error, text = call("activation_summary", {"code": "EMSR762"}, httpx.MockTransport(answer))
    assert error and message in text


def test_network_failures_are_tool_errors() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    error, text = call("list_activations", {}, httpx.MockTransport(down))
    assert error and "cannot reach" in text


def test_the_server_closes_the_client_it_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []
    original = httpx.AsyncClient.aclose

    async def spy(self: httpx.AsyncClient) -> None:
        closed.append(True)
        await original(self)

    monkeypatch.setattr(httpx.AsyncClient, "aclose", spy)

    async def main() -> None:
        async with Client(build_server()) as client:
            await client.list_tools()

    asyncio.run(main())
    assert closed


def test_changes_compare_like_with_like() -> None:
    """A grading map after a delineation is not the flood receding."""
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from copernicus_ems import (
        ProductEvents,
        RapidMapping,
        parse_detail,
        parse_layer,
        select_features,
    )
    from copernicus_ems.geo import summarize
    from copernicus_ems.mcp_server import _aois, _product_row, compare_products

    fixtures = Path(__file__).parent / "fixtures"
    (base,) = [
        p
        for p in parse_detail((fixtures / "detail_EMSR762.json").read_bytes())
        if p.label == "AOI01_DEL_PRODUCT"
    ]
    features = select_features(
        parse_layer((fixtures / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json").read_bytes())
    )
    t0 = datetime(2024, 5, 1, tzinfo=UTC)

    def product(kind: str, monitoring: int, day: int, n: int) -> ProductEvents:
        p = replace(
            base,
            product_type=kind,
            monitoring_number=monitoring,
            acquisitions=(t0 + timedelta(days=day),),
        )
        return ProductEvents(product=p, features=features[:n])

    events = [product("DEL", 0, 0, 43), product("DEL", 1, 1, 20), product("GRA", 0, 2, 5)]

    class Fake(RapidMapping):
        async def observed_events(self, code: str, **_: Any) -> list[ProductEvents]:
            return events

    out = asyncio.run(compare_products(Fake(httpx.AsyncClient()), "EMSR762", 1))
    changes = [(t["product"], t["change_km2"]) for t in out["timeline"]]
    assert changes[0] == ("AOI01_DEL_PRODUCT", None)
    assert changes[1][0] == "AOI01_DEL_MONIT01" and changes[1][1] is not None and changes[1][1] < 0
    assert changes[2] == ("AOI01_GRA_PRODUCT", None)  # first of its type: no change

    rows = [_product_row(e, summarize(e)) for e in events]
    (aoi,) = _aois(rows)
    assert aoi["latest_product"] == "AOI01_DEL_MONIT01"  # not the later GRA map
    assert aoi["product_types"] == ["DEL", "GRA"]


def test_find_scenes_pairs_products_with_open_passes() -> None:
    from test_scenes import stac_handler

    transport = httpx.MockTransport(stac_handler)
    error, out = call(
        "find_scenes", {"code": "EMSR762", "aoi_number": 1, "min_coverage": 0}, transport
    )
    assert not error
    assert "account" in out["note"]
    first = next(p for p in out["products"] if p["product"] == "AOI01_DEL_PRODUCT")
    assert first["made_from"][0]["sensor"] == "PAZ"
    assert 0 < len(first["passes"]) <= 3
    gaps = [abs(p["gap_hours"]) for p in first["passes"]]
    assert gaps == sorted(gaps)
    assert all(p["download_urls"] for p in first["passes"])

    _, radar = call(
        "find_scenes",
        {"code": "EMSR762", "aoi_number": 1, "sensors": ["sentinel-1"], "min_coverage": 0},
        transport,
    )
    first = next(p for p in radar["products"] if p["product"] == "AOI01_DEL_PRODUCT")
    assert {p["collection"] for p in first["passes"]} == {"sentinel-1-grd"}

    _, strict = call(
        "find_scenes", {"code": "EMSR762", "aoi_number": 1, "min_coverage": 1.01}, transport
    )
    assert all(p["passes"] == [] for p in strict["products"])


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"code": "EMSR762", "days": 0}, "days"),
        ({"code": "EMSR762", "aoi_number": 99}, "AOI 99"),
    ],
)
def test_find_scenes_rejects_bad_arguments(arguments: dict[str, Any], message: str) -> None:
    from test_scenes import stac_handler

    error, text = call("find_scenes", arguments, httpx.MockTransport(stac_handler))
    assert error and message in text


def test_find_scenes_unknown_sensor() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from copernicus_ems import RapidMapping
    from copernicus_ems.mcp_server import find_scenes

    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await find_scenes(RapidMapping(http), "EMSR762", sensors=["landsat"])

    with pytest.raises(ToolError, match="sensors"):
        asyncio.run(main())


def test_export_as_geoparquet(data_dir: Path) -> None:
    import pyarrow.parquet as pq

    _, out = call("export_observed_events", {"code": "EMSR762", "file_format": "geoparquet"})
    assert out["events_path"].endswith("EMSR762_flood.parquet")
    assert pq.read_table(out["events_path"]).num_rows == 43
    assert out["masks_path"].endswith("_masks.parquet")
