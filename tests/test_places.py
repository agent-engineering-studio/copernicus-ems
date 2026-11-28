"""The water observed over one place: observed, flooded and not observed kept apart."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import shapely
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry
from test_offline import run_init

from copernicus_ems import EmsError, read_products
from copernicus_ems.geo import area_km2
from copernicus_ems.places import (
    CAVEAT,
    place_geometry,
    place_water,
    saved_events,
    saved_verdicts,
    water_over_place,
)

FIXTURES = Path(__file__).parent / "fixtures"
LAYER = json.loads((FIXTURES / "EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.json").read_bytes())
FLOODS = [shape(f["geometry"]) for f in LAYER["features"]]


@pytest.fixture
def saved(tmp_path: Path) -> Path:
    assert run_init(tmp_path).ok
    return tmp_path


def aoi(saved: Path, number: int) -> BaseGeometry:
    product = next(p for p in read_products("EMSR762", saved) if p.aoi_number == number)
    assert product.aoi_extent is not None
    return shape(product.aoi_extent)


def around_the_water(saved: Path) -> BaseGeometry:
    """A square of about 4 km on the largest flood polygon, cut to the AOI: all observed."""
    largest = max(FLOODS, key=lambda g: g.area)
    x, y = largest.representative_point().coords[0]
    return box(x - 0.025, y - 0.018, x + 0.025, y + 0.018).intersection(aoi(saved, 1))


def test_water_over_a_place_is_the_flooded_part_of_it(saved: Path) -> None:
    place = around_the_water(saved)
    [found] = water_over_place(place, saved)
    expected = area_km2(shapely.intersection(place, shapely.make_valid(shapely.union_all(FLOODS))))
    assert expected > 0.01
    assert found.water_km2 == pytest.approx(expected, rel=1e-9)
    assert found.activation_code == "EMSR762"
    # AOI07 (a grading map) lies inside AOI01: it looked at the place too, and its layer is empty.
    assert found.products == ("EMSR762-aoi01-DEL0", "EMSR762-aoi07-GRA0")
    assert found.wet_products == ("EMSR762-aoi01-DEL0",)
    assert found.place_km2 == pytest.approx(area_km2(place))
    assert found.observed_km2 == pytest.approx(found.place_km2, rel=1e-9)
    assert found.observed_share == pytest.approx(1.0)
    assert found.water_share == pytest.approx(found.water_km2 / found.observed_km2)
    # The delineation saw it on 20 September, the grading map five days later.
    assert found.observed_from is not None and found.observed_to is not None
    assert (found.observed_to - found.observed_from).days == 5
    assert found.attribution.endswith(str(found.event_time.year))


def test_a_geojson_place_in_any_form_gives_the_same_answer(saved: Path) -> None:
    place = around_the_water(saved)
    geometry: dict[str, Any] = dict(mapping(place))
    feature = {"type": "Feature", "properties": {"name": "x"}, "geometry": geometry}
    collection = {"type": "FeatureCollection", "features": [feature]}
    answers = [
        water_over_place(p, saved)[0].water_km2 for p in (place, geometry, feature, collection)
    ]
    assert answers == pytest.approx([answers[0]] * 4)


def test_outside_every_mask_is_not_an_answer_of_no_water(saved: Path) -> None:
    assert water_over_place(box(0.0, 0.0, 0.1, 0.1), saved) == []


def test_a_place_half_outside_the_mask_is_half_observed(saved: Path) -> None:
    looked = shapely.union_all(
        [shape(p.aoi_extent) for p in read_products("EMSR762", saved) if p.aoi_extent]
    )
    place = looked.boundary.interpolate(0.4, normalized=True).buffer(0.01)
    [found] = water_over_place(place, saved)
    assert 0.0 < found.observed_share < 1.0
    assert found.observed_km2 < found.place_km2


def test_seen_dry_is_zero_water_with_an_observed_area(saved: Path) -> None:
    # AOI02: its layers are empty in the saved copy, and it has a monitoring product.
    place = aoi(saved, 2).representative_point().buffer(0.005)
    [found] = water_over_place(place, saved)
    assert found.water_km2 == 0.0 and found.wet_products == ()
    assert found.observed_share == pytest.approx(1.0) and found.water_share == 0.0
    assert found.products == ("EMSR762-aoi02-DEL0",)
    assert found.later_products == ("EMSR762-aoi02-DEL1",)  # a later view: not added


def test_the_radar_verdict_of_the_products_used_comes_with_it(saved: Path) -> None:
    folder = saved / "EMSR762" / "sentinel-1"
    folder.mkdir(parents=True)
    for product, verdict in (("AOI01_DEL_PRODUCT", "to check"), ("AOI03_DEL_PRODUCT", "coherent")):
        body = {"activation": "EMSR762", "product": product, "verdict": verdict}
        (folder / f"{product}_40m_x_quality.json").write_text(json.dumps(body))
    assert saved_verdicts("EMSR762", saved) == {
        "EMSR762-aoi01-DEL0": "to check",
        "EMSR762-aoi03-DEL0": "coherent",
    }
    [found] = water_over_place(around_the_water(saved), saved)
    assert found.verdicts == {"EMSR762-aoi01-DEL0": "to check"}
    row = found.as_dict()
    assert row["verdicts"] == found.verdicts and row["caveat"] == CAVEAT
    assert row["event_time"] == found.event_time.isoformat()
    assert json.dumps(row)


def test_codes_country_and_what_is_not_saved(saved: Path) -> None:
    place = around_the_water(saved)
    assert water_over_place(place, saved, codes=["emsr762"])
    assert water_over_place(place, saved, codes=["EMSR999"]) == []
    assert water_over_place(place, saved, country="France") == []
    # Only the products near the place are read from disk.
    assert [e.product.aoi_number for e in saved_events("EMSR762", saved, over=place)] == [1, 7]
    assert len(saved_events("EMSR762", saved)) > 1
    with pytest.raises(FileNotFoundError):
        water_over_place(place, saved / "nothing-here")


def test_a_product_without_extent_does_not_count(saved: Path) -> None:
    from dataclasses import replace

    from copernicus_ems import read_activations

    [activation] = [a for a in read_activations(saved) if a.code == "EMSR762"]
    events = saved_events("EMSR762", saved)
    blind = [replace(e, product=replace(e.product, aoi_extent_wkt=None)) for e in events]
    assert place_water(around_the_water(saved), activation, blind) is None


@pytest.mark.parametrize(
    "place",
    [
        {"type": "Point", "coordinates": [11.0, 44.0]},
        {"type": "Polygon", "coordinates": []},
        {"type": "Feature", "properties": {}},
        {"type": "Nonsense"},
    ],
)
def test_a_place_without_area_is_an_error(place: dict[str, Any]) -> None:
    with pytest.raises(EmsError):
        place_geometry(place)
