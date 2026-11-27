"""Checks of a model answer (src/copernicus_ems/describe_checks.py): pure, hand-made answers."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from copernicus_ems.describe_checks import (
    ALLOWED_NAMES,
    ANSWER_SCHEMA,
    FORBIDDEN,
    Checked,
    check,
)
from copernicus_ems.describe_input import Source

REASON = (
    "A new wave of severe weather has again hit south-eastern areas of the "
    "Emilia-Romagna region, after the floods already on 2 May 2023, which resulted "
    "in three deaths. The rivers Lamone and Savio overflowed near Faenza and Cesena."
)

SOURCE = Source(
    code="EMSR664",
    name="Flood in Emilia-Romagna",
    reason=REASON,
    bulletins=[{"title": "Bulletin 1: Flood in Italy", "url": "https://example.org/1"}],
    related=["EMSR659"],
)

FACTS: dict[str, Any] = {
    "code": "EMSR664",
    "name": "Flood in Emilia-Romagna",
    "category": "Flood",
    "event_time": "2023-05-16T00:00:00",
    "countries": ["Italy"],
    "aois": {
        "Faenza": {
            "water_km2": 16.23,
            "observed_km2": 1846,
            "first_map": "2023-05-18",
            "last_map": "2023-05-21",
            "sensors": ["COSMO-SkyMed"],
            "maps": 2,
            "radar": {"confirms": 1, "to_check": 1, "not_assessable": 0},
        }
    },
}

VALID: dict[str, Any] = {
    "description": {
        "it": (
            "Una nuova ondata di maltempo ha colpito l'Emilia-Romagna. A Faenza le mappe "
            "del 18 maggio 2023 mostrano 16,23 km² d'acqua su 1.846 km² osservati, "
            "da immagini COSMO-SkyMed. Il radar conferma 1 mappa su 2."
        ),
        "en": (
            "A new wave of severe weather hit Emilia-Romagna. In Faenza the maps of "
            "18 May 2023 show 16.23 km² of water over 1,846 km² observed, from "
            "COSMO-SkyMed images. The radar confirms 1 map out of 2."
        ),
    },
    "places": [{"name": "Faenza", "quote": "near Faenza and Cesena"}],
    "rivers": [{"name": "Lamone", "quote": "The rivers Lamone and Savio overflowed"}],
    "related_events": [
        {"date": "2023-05-02", "what": "floods", "quote": "the floods already on 2 May 2023"}
    ],
    "casualties": {"deaths": 3, "quote": "which resulted in three deaths"},
}


def answer(**changes: Any) -> dict[str, Any]:
    out = copy.deepcopy(VALID)
    out.update(changes)
    return out


def with_text(it: str, en: str = "A flood hit Emilia-Romagna.") -> dict[str, Any]:
    return answer(description={"it": it, "en": en})


def test_valid_answer_kept() -> None:
    checked = check(VALID, SOURCE, FACTS)
    assert isinstance(checked, Checked)
    assert checked.problems == []
    assert checked.description == VALID["description"]
    assert checked.places == VALID["places"]
    assert checked.rivers == VALID["rivers"]
    assert checked.related_events == VALID["related_events"]
    assert checked.casualties == VALID["casualties"]
    assert checked.dropped == []


def test_quote_case_and_space_variant_kept() -> None:
    variant = {"name": "Savio", "quote": "  the RIVERS lamone\n and   savio  Overflowed "}
    checked = check(answer(rivers=[variant]), SOURCE, FACTS)
    assert checked.rivers == [variant]
    assert checked.dropped == []


def test_typographic_quotes_and_dashes_unified() -> None:
    source = Source("X", "", "The so-called \u201cRomagna\u201d area \u2013 near Lugo.", [], [])
    quote = 'the so\u2010called "romagna" area - near lugo'
    checked = check(
        answer(places=[{"name": "Lugo", "quote": quote}], rivers=[], related_events=[]),
        source,
        FACTS | {"aois": {}},
    )
    assert checked.places == [{"name": "Lugo", "quote": quote}]


def test_invented_quote_dropped() -> None:
    bad = {"name": "Ravenna", "quote": "the river flooded Ravenna"}
    checked = check(answer(places=[bad, VALID["places"][0]]), SOURCE, FACTS)
    assert checked.places == [VALID["places"][0]]
    assert checked.dropped == [
        {"field": "places", "value": bad, "why": "quote not found in the official text"}
    ]
    assert checked.description is not None  # fields and description are independent


def test_name_not_in_its_quote_dropped() -> None:
    bad = {"name": "Ravenna", "quote": "near Faenza and Cesena"}
    checked = check(answer(places=[bad]), SOURCE, FACTS)
    assert checked.places == []
    assert checked.dropped[0]["why"] == "name not found in its quote"


def test_casualties_checked() -> None:
    wrong = {"deaths": 5, "quote": "which resulted in three deaths"}
    checked = check(answer(casualties=wrong), SOURCE, FACTS)
    assert checked.casualties is None
    assert checked.dropped == [
        {"field": "casualties", "value": wrong, "why": "the number of deaths is not in its quote"}
    ]
    invented = {"deaths": 3, "quote": "three people died"}
    assert check(answer(casualties=invented), SOURCE, FACTS).casualties is None
    assert check(answer(casualties=None), SOURCE, FACTS).dropped == []


def test_related_event_date_checked() -> None:
    bad = {"date": "2023-05-09", "what": "floods", "quote": "the floods already on 2 May 2023"}
    checked = check(answer(related_events=[bad]), SOURCE, FACTS)
    assert checked.related_events == []
    assert checked.dropped[0]["why"] == "date not found in the official text"
    year = {"date": "2023", "what": "floods", "quote": "the floods already on 2 May 2023"}
    assert check(answer(related_events=[year]), SOURCE, FACTS).related_events == [year]


def test_malformed_items_dropped() -> None:
    checked = check(
        answer(places=["Faenza", {"name": "", "quote": "near Faenza"}], casualties={"deaths": "3"}),
        SOURCE,
        FACTS,
    )
    assert checked.places == [] and checked.casualties is None
    assert len(checked.dropped) == 3


def test_invented_number_rejected() -> None:
    checked = check(with_text("A Faenza 42 km² d'acqua."), SOURCE, FACTS)
    assert checked.description is None
    assert any("42" in p for p in checked.problems)
    assert checked.places == VALID["places"]  # fields kept


def test_rounding_to_written_precision_accepted() -> None:
    ok = with_text("A Faenza circa 16,2 km² d'acqua, 16 in cifra tonda, su 1846 km².")
    assert check(ok, SOURCE, FACTS).problems == []
    assert check(with_text("A Faenza 16,4 km² d'acqua."), SOURCE, FACTS).problems


def test_number_words_tre_vittime_vs_three_deaths() -> None:
    checked = check(
        with_text("L'alluvione del 2 maggio causò tre vittime.", "It caused three deaths."),
        SOURCE,
        FACTS,
    )
    assert checked.problems == []
    rejected = check(with_text("L'alluvione causò sette vittime."), SOURCE, FACTS)
    assert rejected.description is None
    assert any("sette" in p for p in rejected.problems)
    assert check(with_text("x", "It caused seven deaths."), SOURCE, FACTS).problems


def test_dates_written_out() -> None:
    ok = with_text(
        "Le alluvioni del 2 maggio 2023 e la mappa del 21 maggio.",
        "The floods of 2 May 2023, then May 18 and May 21, 2023.",
    )
    assert check(ok, SOURCE, FACTS).problems == []
    bad = check(with_text("Le alluvioni del 9 maggio 2023."), SOURCE, FACTS)
    assert bad.description is None
    assert any("9 maggio 2023" in p for p in bad.problems)
    assert check(with_text("Le alluvioni del 2 maggio 2021."), SOURCE, FACTS).problems


def test_year_alone_must_be_allowed() -> None:
    assert check(with_text("Nel 2023 un'alluvione."), SOURCE, FACTS).problems == []
    assert check(with_text("Nel 2019 un'alluvione."), SOURCE, FACTS).problems


def test_unknown_proper_noun_rejected() -> None:
    checked = check(with_text("L'acqua ha raggiunto Ravenna e Faenza."), SOURCE, FACTS)
    assert checked.description is None
    assert any("Ravenna" in p for p in checked.problems)


def test_names_allowed_and_sentence_starts() -> None:
    text = (
        "Copernicus ha mappato Faenza in Italia, nell'Emilia-Romagna, con Sentinel-1.\n"
        "Lunedì: Nuove mappe. Cesena? Sì! Ecco il fiume Lamone"
    )
    assert check(with_text(text), SOURCE, FACTS).problems == []
    assert {"Italia", "Copernicus", "Sentinel-1", "maggio", "May"} <= ALLOWED_NAMES


def test_forbidden_word_rejected() -> None:
    for it in (
        "La mappa di Faenza è sbagliata.",
        "Un errore della mappa.",
        "La mappa è ERRATA.",
        "Il radar mostra acqua mancata.",
    ):
        checked = check(with_text(it), SOURCE, FACTS)
        assert checked.description is None, it
        assert checked.problems, it
    for en in ("The map is wrong.", "A mistake in the map.", "The radar found missed water."):
        assert check(with_text("Una mappa.", en), SOURCE, FACTS).problems, en
    # ordinary words are not hit
    assert (
        check(with_text("Il territorio è ampio e corretto.", "Terrain."), SOURCE, FACTS).problems
        == []
    )
    assert FORBIDDEN


def test_missing_description_is_a_problem() -> None:
    checked = check(answer(description={"it": "", "en": "A flood."}), SOURCE, FACTS)
    assert checked.description is None and checked.problems
    assert check({}, SOURCE, FACTS).problems


def check_schema(node: dict[str, Any]) -> None:
    """Every object closed and fully required; only what Claude and llama.cpp both accept."""
    allowed = {"type", "properties", "required", "additionalProperties", "items", "anyOf"}
    allowed |= {"description"}
    assert set(node) <= allowed, node
    if node.get("type") == "object":
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])
        for child in node["properties"].values():
            check_schema(child)
    if "items" in node:
        check_schema(node["items"])
    for option in node.get("anyOf", []):
        check_schema(option)


def test_schema_shape() -> None:
    check_schema(ANSWER_SCHEMA)
    props = ANSWER_SCHEMA["properties"]
    assert set(props) == {"description", "places", "rivers", "related_events", "casualties"}
    assert set(props["description"]["properties"]) == {"it", "en"}
    assert set(props["places"]["items"]["properties"]) == {"name", "quote"}
    assert set(props["rivers"]["items"]["properties"]) == {"name", "quote"}
    assert set(props["related_events"]["items"]["properties"]) == {"date", "what", "quote"}
    casualties = props["casualties"]["anyOf"]
    assert {"type": "null"} in casualties
    dead = next(o for o in casualties if o.get("type") == "object")
    assert dead["properties"]["deaths"]["type"] == "integer"


@pytest.mark.parametrize("word", ["uno", "una", "un", "one"])
def test_article_one_never_rejected(word: str) -> None:
    text = f"Faenza: {word} nuova ondata."
    assert (
        check(
            with_text(text, f"Faenza: {word} new wave." if word == "one" else "A wave."),
            SOURCE,
            FACTS,
        ).problems
        == []
    )
