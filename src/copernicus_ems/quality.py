"""Screen a Copernicus EMS flood map against Sentinel-1 radar.

Needs the ``ml`` extra (``pip install copernicus-ems[ml]``: numpy, rasterio).

This is a **screening**, not a judgment: it measures how far the radar agrees
with the map inside the observation mask, and flags what deserves a human look.
Radar sees open water only; it cannot tell a flood from a lake, and it knows
nothing about urban or vegetated flooding.

Rasters hold linear power; thresholds are in dB. Everything is counted only
where the map observed (label ``!= 255``) and the radar is valid.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any

try:
    import numpy as np
    import numpy.typing as npt
    from rasterio.features import sieve
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.quality needs the ml extra: pip install 'copernicus-ems[ml]'"
    ) from exc

VV_DB = -15.5
VV_PRE_DB = -14.0
MIN_PIXELS = 4
CLUSTER_KM2 = 1.0
NEAR_M = 120.0
RESOLUTION_M = 40.0

MAX_GAP_HOURS = 24.0
MIN_COVERAGE = 0.9
MIN_MAPPED_KM2 = 0.1
# calibrated thresholds, provisional until calibration
PARTIAL_EXTENT = 0.1
PARTIAL_RATIO = 2.0
UNMAPPED_RATIO = 3.0
LITTLE_CONFIRMED = 0.1

COHERENT = "coherent"
TO_CHECK = "to check"
NOT_ASSESSABLE = "not assessable"

_IGNORE = 255
_FLOODED = 1


def _db(power: npt.NDArray[np.float32]) -> npt.NDArray[np.float64]:
    return 10.0 * np.log10(np.maximum(power.astype(np.float64), 1e-6))


def _sieve(mask: npt.NDArray[np.bool_], size: int) -> npt.NDArray[np.bool_]:
    """Drop 8-connected patches of ``mask`` smaller than ``size`` pixels."""
    if size >= mask.size:  # rasterio refuses a size as large as the image
        whole = size == mask.size and bool(mask.all())
        return mask.copy() if whole else np.zeros_like(mask)
    kept = sieve(mask.astype(np.uint8), size=max(size, 1), connectivity=8)
    # sieve also fills small holes; only ever remove pixels
    return np.asarray(kept, dtype=np.bool_) & mask


def detect(vv: npt.NDArray[np.float32], vv_pre: npt.NDArray[np.float32]) -> npt.NDArray[np.bool_]:
    """Open water that is new: dark now, not dark before, in patches of
    at least :data:`MIN_PIXELS` pixels. Inputs are linear power."""
    new_water = (_db(vv) < VV_DB) & (_db(vv_pre) >= VV_PRE_DB)
    return _sieve(new_water, MIN_PIXELS)


def _dilate(mask: npt.NDArray[np.bool_], pixels: int) -> npt.NDArray[np.bool_]:
    """Grow ``mask`` by ``pixels`` steps of 8-neighbourhood."""
    out = mask.copy()
    for _ in range(pixels):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        grown[1:, 1:] |= out[:-1, :-1]
        grown[1:, :-1] |= out[:-1, 1:]
        grown[:-1, 1:] |= out[1:, :-1]
        grown[:-1, :-1] |= out[1:, 1:]
        out = grown
    return out


@dataclass(frozen=True, slots=True)
class Measurements:
    """What the radar says about a map, inside the observation mask."""

    coverage: float
    mapped_km2: float
    confirmed_share: float
    unmapped_km2: float
    unmapped_clustered_km2: float
    mapped_extent: float
    unmapped_outside_extent_km2: float

    def as_dict(self) -> dict[str, float]:
        return {f.name: round(getattr(self, f.name), 4) for f in fields(self)}


def _span(mask: npt.NDArray[np.bool_]) -> tuple[int, int]:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    return int(rows[-1] - rows[0] + 1), int(cols[-1] - cols[0] + 1)


def measure(
    detected: npt.NDArray[np.bool_],
    labels: npt.NDArray[np.uint8],
    valid: npt.NDArray[np.bool_],
    resolution_m: float,
) -> Measurements:
    observed = labels != _IGNORE
    region = observed & valid
    px_km2 = (resolution_m / 1000.0) ** 2

    n_observed = int(observed.sum())
    coverage = int(region.sum()) / n_observed if n_observed else 0.0

    mapped = (labels == _FLOODED) & region
    n_mapped = int(mapped.sum())
    det = detected & region
    confirmed_share = int((det & mapped).sum()) / n_mapped if n_mapped else 0.0

    unmapped = det & ~mapped
    near = _dilate(mapped, round(NEAR_M / resolution_m))
    big = _sieve(unmapped, round(CLUSTER_KM2 / px_km2))
    clustered = big & ~near

    outside = clustered.copy()
    mapped_extent = 0.0
    if n_mapped:
        m_rows, m_cols = _span(mapped)
        o_rows, o_cols = _span(region)
        mapped_extent = (m_rows / o_rows) * (m_cols / o_cols)
        r = np.flatnonzero(mapped.any(axis=1))
        c = np.flatnonzero(mapped.any(axis=0))
        outside[r[0] : r[-1] + 1, c[0] : c[-1] + 1] = False

    return Measurements(
        coverage=coverage,
        mapped_km2=n_mapped * px_km2,
        confirmed_share=confirmed_share,
        unmapped_km2=int(unmapped.sum()) * px_km2,
        unmapped_clustered_km2=int(clustered.sum()) * px_km2,
        mapped_extent=mapped_extent,
        unmapped_outside_extent_km2=int(outside.sum()) * px_km2,
    )


@dataclass(frozen=True, slots=True)
class Flag:
    """One thing about the map that deserves a human look."""

    code: str
    text: str
    numbers: dict[str, float]


@dataclass(frozen=True, slots=True)
class QualityReport:
    activation: str
    product: str
    verdict: str
    reason: str | None
    flags: tuple[Flag, ...]
    measurements: Measurements | None
    gap_hours: float | None
    same_image: bool
    resolution_m: float | None
    inputs: dict[str, str] = field(default_factory=dict)
    detector: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["measurements"] = self.measurements.as_dict() if self.measurements else None
        return out


def same_image(meta: Mapping[str, Any]) -> bool:
    """True when the map itself was made from Sentinel-1."""
    sources = meta.get("made_from") or []
    return any("sentinel-1" in str(s.get("sensor", "")).lower() for s in sources)


def _not_assessable(
    meta: Mapping[str, Any],
    measurements: Measurements | None,
    resolution_m: float | None,
    has_reference: bool,
) -> str | None:
    gap = meta.get("gap_hours")
    if same_image(meta):
        return "the map was made from Sentinel-1: agreement would prove nothing"
    if resolution_m is None or abs(resolution_m - RESOLUTION_M) > 0.5:
        return f"thresholds calibrated at {RESOLUTION_M:.0f} m"
    if gap is None:
        return "no pass time"
    if abs(gap) > MAX_GAP_HOURS:
        return f"radar pass {gap:+.1f} h from the map, over ±{MAX_GAP_HOURS:.0f} h"
    if not has_reference:
        return "no pre-event reference (pairs --reference)"
    if measurements is None:
        return "no measurements"
    if measurements.coverage < MIN_COVERAGE:
        return f"radar covers {measurements.coverage:.0%} of the observed area, under 90 %"
    if measurements.mapped_km2 < MIN_MAPPED_KM2:
        return f"too little mapped water to compare ({measurements.mapped_km2:.2f} km²)"
    return None


def _flags(m: Measurements) -> tuple[Flag, ...]:
    flags: list[Flag] = []
    outside = m.unmapped_outside_extent_km2 / m.mapped_km2
    if m.mapped_extent < PARTIAL_EXTENT and outside > PARTIAL_RATIO:
        flags.append(
            Flag(
                "possibly_partial",
                f"mapped water spans {m.mapped_extent:.0%} of the area; "
                f"the radar sees {outside:.1f}× as much darkened water outside it",  # noqa: RUF001
                {"mapped_extent": m.mapped_extent, "outside_ratio": outside},
            )
        )
    clustered = m.unmapped_clustered_km2 / m.mapped_km2
    if clustered > UNMAPPED_RATIO:
        flags.append(
            Flag(
                "much_unmapped",
                f"the radar sees {clustered:.1f}× the mapped water as large unmapped "  # noqa: RUF001
                "darkened patches away from it",
                {"clustered_ratio": clustered},
            )
        )
    if m.confirmed_share < LITTLE_CONFIRMED:
        flags.append(
            Flag(
                "little_confirmed",
                f"the radar confirms only {m.confirmed_share:.0%} of the mapped water",
                {"confirmed_share": m.confirmed_share},
            )
        )
    return tuple(flags)


def assess(
    meta: Mapping[str, Any],
    measurements: Measurements | None,
    *,
    resolution_m: float | None,
    has_reference: bool,
) -> QualityReport:
    """Turn measurements into a screening verdict; the first reason that
    makes the comparison meaningless wins."""
    reason = _not_assessable(meta, measurements, resolution_m, has_reference)
    flags: tuple[Flag, ...] = ()
    if reason is None and measurements is not None:
        flags = _flags(measurements)
    verdict = NOT_ASSESSABLE if reason else (TO_CHECK if flags else COHERENT)
    return QualityReport(
        activation=meta.get("activation", ""),
        product=meta.get("product", ""),
        verdict=verdict,
        reason=reason,
        flags=flags,
        measurements=measurements,
        gap_hours=meta.get("gap_hours"),
        same_image=same_image(meta),
        resolution_m=resolution_m,
        detector={
            "vv_db": VV_DB,
            "vv_pre_db": VV_PRE_DB,
            "min_pixels": MIN_PIXELS,
            "cluster_km2": CLUSTER_KM2,
            "near_m": NEAR_M,
        },
    )
