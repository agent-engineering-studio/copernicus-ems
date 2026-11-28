"""The radar reports beside each Sentinel-1 pair, read back as files.

``copernicus-ems quality`` writes ``<stem>_quality.json`` (the verdict) and
``copernicus-ems radar`` writes ``<stem>_agreement.json`` with its
``.geojson`` (where map and radar agree, by class), under
``<directory>/<CODE>/sentinel-1/``. This module reads the **files**: it needs
neither numpy nor rasterio, and only :func:`read_classes` needs shapely (the
``geo`` extra).

The rules of those reports hold for whoever reads them:

* a verdict is a screening: "to check" means "look again", never "wrong";
* ``candidate`` is possible water outside the map: never truth, never a miss;
* ``map_only`` is not an error of the map;
* with ``independent`` false, ``confirmed`` and the scores prove nothing.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from copernicus_ems.ids import label_product_id

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

#: The verdicts of :mod:`copernicus_ems.quality`, as written in the files.
VERDICTS = ("coherent", "to check", "not assessable")
TO_CHECK = "to check"

#: The two agreement classes :func:`read_classes` keeps by default.
CLASS_CONFIRMED = "confirmed"
CLASS_CANDIDATE = "candidate"

QUALITY_SUFFIX = "_quality.json"
AGREEMENT_SUFFIX = "_agreement.json"

#: The kinds of :class:`ReportNote`.
SEVERAL_PAIRS = "several_pairs"
UNREADABLE_FILE = "unreadable_file"
UNKNOWN_PRODUCT = "unknown_product"
UNREADABLE_GEOJSON = "unreadable_geojson"


def _finite(value: Any) -> Any:
    """Non-finite numbers become ``None``, inside dictionaries and lists too."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _object(path: Path) -> dict[str, Any]:
    """The JSON object of a file; anything else (a list, a number) is a ``ValueError``."""
    d = json.loads(path.read_text())
    if not isinstance(d, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return d


def _stem(path: Path, suffix: str) -> str:
    return path.name[: -len(suffix)]


@dataclass(frozen=True, slots=True)
class QualityFile:
    """One ``_quality.json``."""

    activation_code: str
    product: str
    stem: str
    verdict: str
    reason: str | None
    flags: list[dict[str, Any]]
    measurements: dict[str, Any]
    thresholds: dict[str, Any] | None
    gap_hours: float | None
    resolution_m: float | None
    file: str

    @property
    def flag_codes(self) -> tuple[str, ...]:
        return tuple(str(f.get("code")) for f in self.flags if f.get("code"))


@dataclass(frozen=True, slots=True)
class AgreementFile:
    """One ``_agreement.json``."""

    activation_code: str
    product: str
    stem: str
    computed: bool
    reason: str | None
    independent: bool | None
    km2: dict[str, Any]
    scores: dict[str, Any]
    quality_verdict: str | None
    gap_hours: float | None
    resolution_m: float | None
    file: str
    geojson: str | None


def read_quality(path: Path, *, activation_code: str | None = None) -> QualityFile:
    """A ``_quality.json``. A verdict outside :data:`VERDICTS` is a ``ValueError``.

    ``activation_code`` is used when the file does not name its activation.
    """
    d = _object(path)
    verdict = str(d.get("verdict") or "")
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r} in {path.name}")
    return QualityFile(
        activation_code=str(d.get("activation") or activation_code or "").upper(),
        product=str(d["product"]),
        stem=_stem(path, QUALITY_SUFFIX),
        verdict=verdict,
        reason=d.get("reason"),
        flags=_finite(list(d.get("flags") or [])),
        measurements=_finite(dict(d.get("measurements") or {})),
        thresholds=_finite(d.get("thresholds")),
        gap_hours=_number(d.get("gap_hours")),
        resolution_m=_number(d.get("resolution_m")),
        file=path.name,
    )


def read_agreement(path: Path, *, activation_code: str | None = None) -> AgreementFile:
    """An ``_agreement.json``. With ``computed`` false there is no tif and no geojson."""
    d = _object(path)
    computed = bool(d.get("computed"))
    files = d.get("files") or {}
    geojson = files.get("geojson") if computed else None
    independent = d.get("independent")
    quality_verdict = d.get("quality_verdict")
    return AgreementFile(
        activation_code=str(d.get("activation") or activation_code or "").upper(),
        product=str(d["product"]),
        stem=_stem(path, AGREEMENT_SUFFIX),
        computed=computed,
        reason=d.get("reason"),
        independent=bool(independent) if independent is not None else None,
        km2=_finite(dict(d.get("km2") or {})),
        scores=_finite(dict(d.get("scores") or {})),
        quality_verdict=quality_verdict if quality_verdict in VERDICTS else None,
        gap_hours=_number(d.get("gap_hours")),
        resolution_m=_number(d.get("resolution_m")),
        file=path.name,
        geojson=str(geojson) if geojson else None,
    )


@dataclass(frozen=True, slots=True)
class AgreementClass:
    """The polygons of one agreement class (a MultiPolygon, lon/lat)."""

    name: str
    area_km2: float | None
    geom: BaseGeometry


def read_classes(
    path: Path, names: Collection[str] = (CLASS_CONFIRMED, CLASS_CANDIDATE)
) -> dict[str, AgreementClass]:
    """The classes ``names`` of an agreement ``.geojson`` (needs the ``geo`` extra).

    The classes stay apart by construction: who wants truth takes
    ``confirmed``; ``candidate`` is never truth. An empty class is missing.
    """
    try:
        from shapely.geometry import MultiPolygon, shape
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "copernicus_ems.reports.read_classes needs the geo extra: "
            "pip install 'copernicus-ems[geo]'"
        ) from exc
    d = _object(path)
    out: dict[str, AgreementClass] = {}
    for feature in d.get("features") or []:
        props = feature.get("properties") or {}
        name = props.get("class")
        if not isinstance(name, str) or name not in names or feature.get("geometry") is None:
            continue
        geom: BaseGeometry = shape(feature["geometry"])
        if geom.is_empty:
            continue
        if not isinstance(geom, MultiPolygon):
            geom = MultiPolygon([geom])  # type: ignore[list-item]
        out[name] = AgreementClass(name=name, area_km2=_number(props.get("km2")), geom=geom)
    return out


@dataclass(slots=True)
class ProductReport:
    """What is known of one product: verdict, agreement and its classes."""

    activation_code: str
    product: str
    product_id: str
    stem: str
    quality: QualityFile | None = None
    agreement: AgreementFile | None = None
    classes: dict[str, AgreementClass] = field(default_factory=dict)

    @property
    def verdict(self) -> str | None:
        if self.quality is not None:
            return self.quality.verdict
        return self.agreement.quality_verdict if self.agreement is not None else None

    @property
    def independent(self) -> bool | None:
        return self.agreement.independent if self.agreement is not None else None


@dataclass(frozen=True, slots=True)
class ReportNote:
    """Something :func:`collect_reports` skipped or chose, to be logged by the caller."""

    kind: str
    activation_code: str
    product: str
    #: The file (:data:`UNREADABLE_FILE`, :data:`UNREADABLE_GEOJSON`) or the stem kept.
    subject: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReportScan:
    """The reports found under a directory, and what was skipped or chosen."""

    reports: list[ProductReport]
    notes: list[ReportNote] = field(default_factory=list)


def newest_pair(files: Mapping[str, Sequence[Path]]) -> str:
    """The stem to keep among the pairs of one product: the most recent.

    ``files`` maps a stem to its report files. A pair made again replaces the
    one before, whatever its resolution; with the same time, the greater stem.
    This is the one rule of the library: ``describe`` and the demo use it too.
    """
    return max(files, key=lambda s: (max(p.stat().st_mtime for p in files[s]), s))


def _product_of(stem: str) -> str:
    """``AOI01_GRA_PRODUCT_20m_x`` gives ``AOI01_GRA_PRODUCT``: the first three parts."""
    return stem.split("_40m_")[0] if "_40m_" in stem else "_".join(stem.split("_")[:3])


def collect_reports(
    directory: Path, *, classes: Collection[str] = (CLASS_CONFIRMED, CLASS_CANDIDATE)
) -> ReportScan:
    """Every report under ``<directory>/*/sentinel-1/``, one per product.

    A product with several pairs keeps the most recent. A file that cannot be
    read, or with a product label of another form, is skipped. Both are said
    in ``notes``: never a map dropped in silence, never a report made up.
    ``classes`` are the agreement classes read from the ``.geojson`` (none:
    the geometry is not read and shapely is not needed).
    """
    return _scan(sorted(directory.glob("*/sentinel-1")), classes)


def activation_reports(
    directory: Path,
    code: str,
    *,
    classes: Collection[str] = (CLASS_CONFIRMED, CLASS_CANDIDATE),
) -> ReportScan:
    """The reports of ``<directory>/<code>/sentinel-1/``, as :func:`collect_reports`."""
    return _scan([directory / code / "sentinel-1"], classes)


def _scan(folders: Sequence[Path], classes: Collection[str]) -> ReportScan:
    groups: dict[tuple[str, str], dict[str, list[Path]]] = {}
    for folder in folders:
        code = folder.parent.name.upper()
        paths = [*folder.glob(f"*{QUALITY_SUFFIX}"), *folder.glob(f"*{AGREEMENT_SUFFIX}")]
        for path in sorted(paths):
            suffix = QUALITY_SUFFIX if path.name.endswith(QUALITY_SUFFIX) else AGREEMENT_SUFFIX
            stem = _stem(path, suffix)
            groups.setdefault((code, _product_of(stem)), {}).setdefault(stem, []).append(path)

    reports: list[ProductReport] = []
    notes: list[ReportNote] = []
    for (code, product), files in sorted(groups.items()):
        stem = newest_pair(files)
        if len(files) > 1:
            notes.append(ReportNote(SEVERAL_PAIRS, code, product, stem, " ".join(sorted(files))))
        report = _read_pair(code, product, stem, files[stem], classes, notes)
        if report is not None:
            reports.append(report)
    return ReportScan(reports=reports, notes=notes)


def _read_pair(
    code: str,
    product: str,
    stem: str,
    paths: Sequence[Path],
    classes: Collection[str],
    notes: list[ReportNote],
) -> ProductReport | None:
    quality: QualityFile | None = None
    agreement: AgreementFile | None = None
    for path in paths:
        try:
            if path.name.endswith(QUALITY_SUFFIX):
                quality = read_quality(path, activation_code=code)
            else:
                agreement = read_agreement(path, activation_code=code)
        except (OSError, ValueError, KeyError) as exc:
            notes.append(ReportNote(UNREADABLE_FILE, code, product, str(path), str(exc)[:200]))
    read = quality or agreement
    if read is None:
        return None
    key = label_product_id(read.activation_code, read.product)
    if key is None:
        notes.append(ReportNote(UNKNOWN_PRODUCT, code, product, stem))
        return None
    found: dict[str, AgreementClass] = {}
    if classes and agreement is not None and agreement.geojson:
        geojson = paths[0].parent / agreement.geojson
        try:
            found = read_classes(geojson, classes)
        except (OSError, ValueError, KeyError) as exc:
            notes.append(
                ReportNote(UNREADABLE_GEOJSON, code, product, str(geojson), str(exc)[:200])
            )
    return ProductReport(
        activation_code=read.activation_code,
        product=read.product,
        product_id=key,
        stem=stem,
        quality=quality,
        agreement=agreement,
        classes=found,
    )
