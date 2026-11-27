"""The checks of a model's answer: what it may keep, what is dropped, why.

Pure functions, no I/O. :data:`ANSWER_SCHEMA` is the JSON schema the model must
follow (Claude's ``output_config.format`` and llama.cpp's ``response_format``):
plain objects and arrays, every object closed (``additionalProperties: false``)
and fully ``required``; the one nullable field, ``casualties``, is written as
``anyOf`` with ``{"type": "null"}``, which both accept. No numeric or length
constraints (Claude's structured outputs do not support them).

:func:`check` keeps an extracted field only when its ``quote`` occurs in the
official text (case, whitespace, typographic quotes and dashes unified) and
says why it dropped the others. The description is rejected when it carries a
number, a date or a name that is not among the library's numbers, in the
official text or in :data:`ALLOWED_NAMES`, or a word of :data:`FORBIDDEN`.
Fields and description are independent: a rejected description keeps the
checked fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from copernicus_ems.describe_input import Source, allowed_names, allowed_numbers

_TEXT: dict[str, Any] = {"type": "string"}


def _closed(**properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _quoted(what: str) -> dict[str, Any]:
    return {
        "type": "string",
        "description": f"The exact words of the official text that give the {what}.",
    }


ANSWER_SCHEMA: dict[str, Any] = _closed(
    description=_closed(it=_TEXT, en=_TEXT),
    places={"type": "array", "items": _closed(name=_TEXT, quote=_quoted("place"))},
    rivers={"type": "array", "items": _closed(name=_TEXT, quote=_quoted("river"))},
    related_events={
        "type": "array",
        "items": _closed(
            date={"type": "string", "description": "ISO date when the text gives it, else ''."},
            what=_TEXT,
            quote=_quoted("event"),
        ),
    },
    casualties={
        "anyOf": [
            _closed(deaths={"type": "integer"}, quote=_quoted("number of deaths")),
            {"type": "null"},
        ]
    },
)

MONTHS: dict[str, int] = {
    name: number
    for names in (
        "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre "
        "novembre dicembre",
        "January February March April May June July August September October November December",
    )
    for number, name in enumerate(names.split(), start=1)
}
_WEEKDAYS = (
    "lunedì martedì mercoledì giovedì venerdì sabato domenica "
    "Monday Tuesday Wednesday Thursday Friday Saturday Sunday"
)

# Generic capitalised nouns and abbreviations: none of them can carry an invented fact.
#   en: River, Province, Region, Municipality, Valley, Sea, Mount
#   it: Fiume, Provincia, Regione, Comune, Valle, Mare, Monte, Servizio, Protezione,
#       Civile, Dipartimento
#   both: AOI, AOIs, CEMS, UTC, CET, CEST
# plus, above: Italia, Italy, Europa, Europe, Copernicus, EMS, Emergency, Management,
# Service, Rapid, Mapping, Sentinel, Sentinel-1, Sentinel-2, the months and the weekdays.
_GENERIC = [
    "River",
    "Province",
    "Region",
    "Municipality",
    "Valley",
    "Sea",
    "Mount",
    "Fiume",
    "Provincia",
    "Regione",
    "Comune",
    "Valle",
    "Mare",
    "Monte",
    "Servizio",
    "Protezione",
    "Civile",
    "Dipartimento",
    "AOI",
    "AOIs",
    "CEMS",
    "UTC",
    "CET",
    "CEST",
]

ALLOWED_NAMES: frozenset[str] = frozenset(
    {
        "Italia",
        "Italy",
        "Europa",
        "Europe",
        "Copernicus",
        "EMS",
        "Emergency",
        "Management",
        "Service",
        "Rapid",
        "Mapping",
        "Sentinel",
        "Sentinel-1",
        "Sentinel-2",
        *MONTHS,
        *_WEEKDAYS.split(),
        *_GENERIC,
    }
)
"""Names a description may use beyond the official text and the facts."""

FORBIDDEN: dict[str, tuple[str, ...]] = {
    "it": (
        r"sbagliat\w*",
        r"error[ei]",
        r"errat[aeio]",
        r"erroneamente",
        r"inesatt\w*",
        r"acqua\s+(?:non\s+)?(?:mancat\w*|sfuggit\w*)",
        r"mancat\w*\s+acqua",
    ),
    "en": (
        r"wrong\w*",
        r"errors?",
        r"erroneous\w*",
        r"mistakes?",
        r"mistaken\w*",
        r"inaccura\w*",
        r"missed\s+(?:water|flood\w*|area\w*)",
        r"missing\s+water",
    ),
}
"""Words that judge a map as wrong or call radar water missed (regex, whole words)."""

NUMBER_WORDS: dict[str, dict[str, int]] = {
    "it": {
        name: n
        for n, name in enumerate(
            [
                "zero",
                "uno",
                "due",
                "tre",
                "quattro",
                "cinque",
                "sei",
                "sette",
                "otto",
                "nove",
                "dieci",
                "undici",
                "dodici",
                "tredici",
                "quattordici",
                "quindici",
                "sedici",
                "diciassette",
                "diciotto",
                "diciannove",
                "venti",
            ]
        )
    }
    | {"un": 1, "una": 1},
    "en": {
        name: n
        for n, name in enumerate(
            [
                "zero",
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
                "ten",
                "eleven",
                "twelve",
                "thirteen",
                "fourteen",
                "fifteen",
                "sixteen",
                "seventeen",
                "eighteen",
                "nineteen",
                "twenty",
            ]
        )
    },
}
# "venti" (winds) and "sei" (you are) are ordinary Italian words: not read as numbers.
for _ambiguous in ("venti", "sei"):
    del NUMBER_WORDS["it"][_ambiguous]

_ARTICLES = {"un", "uno", "una", "one"}  # also "a"/"an": never read as the number 1

_FORBIDDEN = [
    re.compile(rf"\b(?:{p})\b", re.IGNORECASE) for words in FORBIDDEN.values() for p in words
]
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DATE = re.compile(
    rf"\b(?P<d1>\d{{1,2}})(?:st|nd|rd|th|º|°)?\s+(?:di\s+)?(?P<m1>{_MONTH})\b"
    rf"(?:,?\s+(?P<y1>\d{{4}})\b)?"
    rf"|\b(?P<m2>{_MONTH})\s+(?P<d2>\d{{1,2}})(?!\d)(?:st|nd|rd|th)?(?:,?\s+(?P<y2>\d{{4}})\b)?",
    re.IGNORECASE,
)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TIME = re.compile(r"(?<![\d:.,])(\d{1,2}):(\d{2})(?![\d:])")
_SPACES = " \u00a0\u202f\u2009"
_NUM = re.compile(
    rf"(?<!\w)(?<![^\W\d_]-)(?:\d{{1,3}}(?:[.,{_SPACES}]\d{{3}})+(?![\d])(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
)
_NAME = re.compile(r"\w[\w-]*")
_TYPOGRAPHIC = str.maketrans(
    {
        **dict.fromkeys("\u2018\u2019\u201a\u201b\u2032`\u00b4", "'"),
        **dict.fromkeys("\u201c\u201d\u201e\u201f\u00ab\u00bb\u2033", '"'),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    }
)

Day = tuple[int, int, int | None]


@dataclass(slots=True)
class Checked:
    """What survives the checks: the description (or None), the fields, and why."""

    description: dict[str, str] | None
    places: list[dict[str, str]] = field(default_factory=list)
    rivers: list[dict[str, str]] = field(default_factory=list)
    related_events: list[dict[str, str]] = field(default_factory=list)
    casualties: dict[str, Any] | None = None
    dropped: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def normalise(text: str) -> str:
    """Casefolded, one space between words, plain quotes, apostrophes and dashes."""
    return " ".join(text.translate(_TYPOGRAPHIC).casefold().split())


def _quote_found(quote: str, reason: str) -> bool:
    needle = normalise(quote).strip("\"' ")
    return bool(needle) and needle in normalise(reason)


def _days(text: str) -> set[Day]:
    """Dates written as day and month name (with the year when given), and ISO dates."""
    out: set[Day] = set()
    for m in _DATE.finditer(text):
        day, month, year = (m["d1"], m["m1"], m["y1"]) if m["d1"] else (m["d2"], m["m2"], m["y2"])
        out.add((int(day), _month(month), int(year) if year else None))
    out |= {(int(d), int(mo), int(y)) for y, mo, d in _ISO.findall(text)}
    return out


def _month(name: str) -> int:
    return next(n for k, n in MONTHS.items() if k.casefold() == name.casefold())


def _words(text: str, languages: tuple[str, ...]) -> list[tuple[str, int]]:
    """Numbers written out (0-20) in the given languages, as (word, value)."""
    out = []
    for language in languages:
        table = NUMBER_WORDS[language]
        pattern = re.compile(rf"\b({'|'.join(table)})\b", re.IGNORECASE)
        out += [(m[1], table[m[1].casefold()]) for m in pattern.finditer(text)]
    return out


def _readings(token: str) -> list[tuple[float, int]]:
    """The values a written number may mean, each with its written precision."""
    if any(c in token for c in _SPACES):
        return [(float(re.sub(r"\D", "", token)), 0)]
    if "." in token and "," in token:
        head, tail = re.split(r"[.,](?=\d+$)", token)
        return [(float(re.sub(r"\D", "", head) + "." + tail), len(tail))]
    match = re.fullmatch(r"(\d+)[.,](\d+)", token)
    if not match:
        return [(float(re.sub(r"\D", "", token)), 0)]
    whole, part = match.groups()
    out = [(float(f"{whole}.{part}"), len(part))]
    if len(part) == 3 and len(whole) <= 3 and re.fullmatch(r"\d{1,3}([.,]\d{3})+", token):
        out.append((float(whole + part), 0))
    return out


@dataclass(slots=True)
class _Allowed:
    strings: set[str]
    values: set[float]
    days: set[Day]
    names: set[str]
    times: set[tuple[int, int]]

    def number(self, token: str) -> bool:
        if token in self.strings:
            return True
        return any(
            abs(v - x) <= 0.5 * 10**-p + 1e-9 for x, p in _readings(token) for v in self.values
        )

    def day(self, day: int, month: int, year: int | None) -> bool:
        for d, m, y in self.days:
            if (d, m) != (day, month):
                continue
            if year is None or y == year or (y is None and str(year) in self.strings):
                return True
        return False

    def name(self, word: str) -> bool:
        if word.casefold() in self.names:
            return True
        parts = [p for p in word.split("-") if p]
        return len(parts) > 1 and all(
            p.casefold() in self.names or not p[0].isupper() for p in parts
        )


def _allowed(facts: dict[str, Any], source: Source) -> _Allowed:
    strings = allowed_numbers(facts, source)
    values = {x for s in strings if "-" not in s for x, _ in _readings(s)}
    texts = [source.reason, *(b["title"] for b in source.bulletins)]
    for text in texts:
        values |= {float(n) for _, n in _words(text, ("it", "en"))}
    days: set[Day] = set()
    for s in strings:
        days |= _days(s)
    for text in texts:
        days |= _days(text)
    names = {n.casefold() for n in allowed_names(facts, source) | ALLOWED_NAMES}
    times = {(int(h), int(m)) for text in texts for h, m in _TIME.findall(text)}
    event = facts.get("event_time")
    if isinstance(event, str):
        times |= {(int(h), int(m)) for h, m in _TIME.findall(event[11:16])}
    return _Allowed(strings, values, days, names, times)


def _blank(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _sentence_start(text: str, start: int) -> bool:
    before = text[:start]
    stripped = before.rstrip()
    return not stripped or stripped[-1] in ".!?:" or "\n" in before[len(stripped) :]


def _text_problems(language: str, text: str, allowed: _Allowed) -> list[str]:
    out = []
    plain = text
    for m in _DATE.finditer(text):
        day, month, year = (m["d1"], m["m1"], m["y1"]) if m["d1"] else (m["d2"], m["m2"], m["y2"])
        if not allowed.day(int(day), _month(month), int(year) if year else None):
            out.append(
                f'{language}: the date "{m[0]}" is not among the library\'s dates '
                "or in the official text"
            )
        plain = _blank(plain, m.start(), m.end())
    for m in _TIME.finditer(plain):
        if (int(m[1]), int(m[2])) not in allowed.times:
            out.append(
                f'{language}: the time "{m[0]}" is not the activation\'s time '
                "or in the official text"
            )
        plain = _blank(plain, m.start(), m.end())
    for m in _ISO.finditer(plain):
        if m[0] not in allowed.strings and not allowed.day(int(m[3]), int(m[2]), int(m[1])):
            out.append(f'{language}: the date "{m[0]}" is not among the library\'s dates')
        plain = _blank(plain, m.start(), m.end())
    for m in _NUM.finditer(plain):
        token = m[0]
        parts = re.split(f"[{_SPACES}]", token)
        if allowed.number(token) or (len(parts) > 1 and all(allowed.number(p) for p in parts)):
            continue
        out.append(
            f'{language}: the number "{token}" is not among the library\'s numbers '
            "or in the official text"
        )
    for word, value in _words(plain, (language,) if language in NUMBER_WORDS else ("it", "en")):
        if word.casefold() in _ARTICLES or allowed.number(str(value)):
            continue
        out.append(
            f'{language}: the number "{word}" ({value}) is not among the library\'s numbers '
            "or in the official text"
        )
    for m in _NAME.finditer(text):
        word = m[0].strip("-")
        if not word or not word[0].isupper() or _sentence_start(text, m.start()):
            continue
        if not allowed.name(word):
            out.append(f'{language}: the name "{word}" is not in the official text or the facts')
    for pattern in _FORBIDDEN:
        out += [
            f'{language}: "{m[0]}" judges a map or calls radar water missed; not allowed'
            for m in pattern.finditer(text)
        ]
    return out


def _drop(checked: Checked, name: str, value: Any, why: str) -> None:
    checked.dropped.append({"field": name, "value": value, "why": why})


def _named(checked: Checked, name: str, items: Any, reason: str) -> list[dict[str, str]]:
    kept = []
    for item in items if isinstance(items, list) else []:
        if not (
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and item["name"].strip()
            and isinstance(item.get("quote"), str)
        ):
            _drop(checked, name, item, "not a name with a quote")
        elif not _quote_found(item["quote"], reason):
            _drop(checked, name, item, "quote not found in the official text")
        elif normalise(item["name"]) not in normalise(item["quote"]):
            _drop(checked, name, item, "name not found in its quote")
        else:
            kept.append({"name": item["name"], "quote": item["quote"]})
    return kept


def _date_in(date: str, reason: str) -> bool:
    if not date:
        return True
    match = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", date)
    if not match:
        return False
    year, month, day = (int(g) if g else None for g in match.groups())
    days = _days(reason)
    years = {int(n) for n in re.findall(r"\b\d{4}\b", reason)}
    if day is not None:
        return (day, month, year) in days or ((day, month, None) in days and year in years)
    if month is not None:
        return year in years and any(m == month for _, m, _ in days)
    return year in years


def _events(checked: Checked, items: Any, reason: str) -> list[dict[str, str]]:
    kept = []
    for item in items if isinstance(items, list) else []:
        if not (
            isinstance(item, dict)
            and all(isinstance(item.get(k), str) for k in ("date", "what", "quote"))
            and item["what"].strip()
        ):
            _drop(checked, "related_events", item, "not a date, a description and a quote")
        elif not _quote_found(item["quote"], reason):
            _drop(checked, "related_events", item, "quote not found in the official text")
        elif not _date_in(item["date"].strip(), reason):
            _drop(checked, "related_events", item, "date not found in the official text")
        else:
            kept.append({k: item[k] for k in ("date", "what", "quote")})
    return kept


def _casualties(checked: Checked, item: Any, reason: str) -> dict[str, Any] | None:
    if item is None:
        return None
    deaths = item.get("deaths") if isinstance(item, dict) else None
    if not (
        isinstance(item, dict)
        and isinstance(deaths, int)
        and not isinstance(deaths, bool)
        and isinstance(item.get("quote"), str)
    ):
        _drop(checked, "casualties", item, "not a number of deaths with a quote")
        return None
    quote = item["quote"]
    if not _quote_found(quote, reason):
        _drop(checked, "casualties", item, "quote not found in the official text")
        return None
    said = {int(n) for n in re.findall(r"\d+", quote)} | {n for _, n in _words(quote, ("it", "en"))}
    if deaths not in said:
        _drop(checked, "casualties", item, "the number of deaths is not in its quote")
        return None
    return {"deaths": deaths, "quote": quote}


def check(answer: dict[str, Any], source: Source, facts: dict[str, Any]) -> Checked:
    """Check a model's answer against the official text and the library's numbers."""
    reason = source.reason
    checked = Checked(description=None)
    checked.places = _named(checked, "places", answer.get("places"), reason)
    checked.rivers = _named(checked, "rivers", answer.get("rivers"), reason)
    checked.related_events = _events(checked, answer.get("related_events"), reason)
    checked.casualties = _casualties(checked, answer.get("casualties"), reason)

    description = answer.get("description")
    texts = description if isinstance(description, dict) else {}
    allowed = _allowed(facts, source)
    for language in ("it", "en"):
        text = texts.get(language)
        if not isinstance(text, str) or not text.strip():
            checked.problems.append(f"{language}: the description is missing")
            continue
        checked.problems += _text_problems(language, text, allowed)
    if not checked.problems:
        checked.description = {"it": texts["it"], "en": texts["en"]}
    return checked
