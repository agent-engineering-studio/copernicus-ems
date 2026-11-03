from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from copernicus_ems import (
    Activation,
    EmsError,
    activations_url,
    detail_url,
    parse_activations,
    parse_detail,
    parse_time,
    select_activations,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_a_page_of_activations() -> None:
    page = parse_activations((FIXTURES / "activations_page.json").read_bytes())
    assert page.count == 268
    assert page.next_url is not None and page.next_url.endswith("limit=3&offset=3")
    first = page.activations[0]
    assert first.code == "EMSR935"
    assert first.countries == ("Spain",)
    assert first.event_time == datetime(2026, 10, 19, 3, 10, tzinfo=UTC)
    assert first.closed


def test_rows_without_an_event_time_are_skipped() -> None:
    rows = [
        {"code": "A", "eventTime": None},
        {"code": "B", "eventTime": "2024-01-01T00:00:00Z", "countries": [{"name": "Italy"}]},
    ]
    page = parse_activations(json.dumps({"count": 2, "next": None, "results": rows}))
    assert [a.code for a in page.activations] == ["B"]
    assert page.activations[0].countries == ("Italy",)
    assert page.next_url is None


@pytest.mark.parametrize("body", ["[]", "<html/>", '{"detail": "x"}'])
def test_not_a_list_page_raises(body: str) -> None:
    with pytest.raises(EmsError):
        parse_activations(body)


def _act(code: str, category: str, country: str, day: int) -> Activation:
    return Activation(
        code=code,
        name=code,
        category=category,
        event_time=datetime(2024, 1, day, tzinfo=UTC),
        countries=(country,),
    )


def test_selection_by_country_and_category_is_oldest_first() -> None:
    acts = [
        _act("C", "Storm", "Italy", 3),
        _act("A", "Flood", "Italy", 1),
        _act("B", "Flood", "Spain", 2),
    ]
    assert [
        a.code for a in select_activations(acts, country="Italy", categories=["flood", "STORM"])
    ] == ["A", "C"]
    assert [a.code for a in select_activations(acts)] == ["A", "B", "C"]


def test_the_detail_lists_every_product_with_its_layers() -> None:
    products = parse_detail((FIXTURES / "detail_EMSR762.json").read_bytes())
    first = products[0]
    assert (first.activation_code, first.aoi_number, first.aoi_name) == (
        "EMSR762",
        1,
        "Brisighella",
    )
    assert first.label == "AOI01_DEL_PRODUCT"
    assert first.observed_time == datetime(2024, 9, 20, 5, 27, tzinfo=UTC)
    assert first.aoi_extent_wkt is not None and first.aoi_extent_wkt.startswith("POLYGON")
    assert first.layers["observedEventA"].url.endswith(
        "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json"
    )
    assert all(layer.url.endswith(".json") for p in products for layer in p.layers.values())
    monitoring = [p for p in products if p.monitoring_number]
    assert monitoring and monitoring[0].label.endswith("_MONIT01")


def _layer(kind: str, fmt: str = "vt", url: str | None = None) -> dict[str, str]:
    layer = {"name": f"E1/AOI02/DEL_PRODUCT/E1_AOI02_DEL_PRODUCT_{kind}", "format": fmt}
    if url is not None:
        layer["json"] = url
    return layer


def test_the_highest_layer_version_wins_and_imagery_is_left_out() -> None:
    product = {
        "type": "del",
        "monitoringNumber": 0,
        "version": {"number": 2},
        "images": [],
        "layers": [
            _layer("observedEventA_v1_VT", url="v1.json"),
            _layer("observedEventA_v2_VT", url="v2.json"),
            _layer("GEOEYE1_ORTHO.tif", fmt="cog"),
            _layer("floodDepthA_v1_VT"),
        ],
    }
    body = {
        "results": [{"code": "E1", "aois": [{"number": 2, "name": "x", "products": [product]}]}]
    }
    (parsed,) = parse_detail(json.dumps(body))
    assert parsed.product_type == "DEL"
    assert parsed.version == 2
    assert parsed.observed_time is None
    assert {k: v.url for k, v in parsed.layers.items()} == {"observedEventA": "v2.json"}


def test_a_detail_that_is_not_json_raises() -> None:
    with pytest.raises(EmsError, match="did not return JSON"):
        parse_detail(b"<html>")
    with pytest.raises(EmsError, match="unexpected activation detail"):
        parse_detail(b"[]")


def test_times_are_utc_even_without_a_zone() -> None:
    assert parse_time("2024-09-20T05:27:00") == datetime(2024, 9, 20, 5, 27, tzinfo=UTC)
    assert parse_time("2024-09-20T05:27:00Z") == datetime(2024, 9, 20, 5, 27, tzinfo=UTC)
    assert parse_time("not a time") is None
    assert parse_time(None) is None


def test_urls() -> None:
    assert activations_url(page_size=10).endswith("public-activations-info/?limit=10")
    assert detail_url("EMSR762").endswith("public-activations/?code=EMSR762")
