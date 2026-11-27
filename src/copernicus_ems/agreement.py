"""Classes of agreement between a Copernicus EMS flood map and Sentinel-1 radar.

Needs the ``ml`` extra (``pip install copernicus-ems[ml]``: numpy, rasterio).

Inside the observed area the map and the radar detector (the one of
:mod:`copernicus_ems.quality`) split every pixel into:

- ``confirmed``: mapped as flood and detected by the radar;
- ``map_only``: mapped as flood, not seen by the radar;
- ``candidate``: radar water outside and away from the map, in a cluster of at
  least one square kilometre (the quality check's clustered unmapped area);
- ``radar_scattered``: any other radar water outside the map;
- ``dry``: neither.

Pixels the map did not observe, or where the radar is not valid, are
``NOT_OBSERVED``. This is a **screening**, not a judgment: radar sees open
water only, so a radar-only candidate is something to look at, never ground
truth.
"""

from __future__ import annotations

from copernicus_ems.quality import CLUSTER_KM2, NEAR_M, _dilate, _sieve

try:
    import numpy as np
    import numpy.typing as npt
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "copernicus_ems.agreement needs the ml extra: pip install 'copernicus-ems[ml]'"
    ) from exc

DRY = 0
CONFIRMED = 1
MAP_ONLY = 2
CANDIDATE = 3
RADAR_SCATTERED = 4
NOT_OBSERVED = 255

CLASS_NAMES: dict[int, str] = {
    DRY: "dry",
    CONFIRMED: "confirmed",
    MAP_ONLY: "map_only",
    CANDIDATE: "candidate",
    RADAR_SCATTERED: "radar_scattered",
}

_FLOODED = 1


def classify(
    detected: npt.NDArray[np.bool_],
    labels: npt.NDArray[np.uint8],
    valid: npt.NDArray[np.bool_],
    resolution_m: float,
) -> npt.NDArray[np.uint8]:
    """Class of every pixel; see the module docstring for the classes."""
    observed = labels != NOT_OBSERVED
    region = observed & valid
    px_km2 = (resolution_m / 1000.0) ** 2

    mapped = (labels == _FLOODED) & region
    det = detected & region
    unmapped = det & ~mapped
    near = _dilate(mapped, round(NEAR_M / resolution_m))
    candidate = _sieve(unmapped, round(CLUSTER_KM2 / px_km2)) & ~near

    out = np.full(labels.shape, DRY, dtype=np.uint8)
    out[unmapped] = RADAR_SCATTERED
    out[candidate] = CANDIDATE
    out[mapped] = MAP_ONLY
    out[mapped & det] = CONFIRMED
    out[~region] = NOT_OBSERVED
    return out


def class_km2(classes: npt.NDArray[np.uint8], resolution_m: float) -> dict[str, float]:
    """Area of each class in km2; all five names are always present."""
    px_km2 = (resolution_m / 1000.0) ** 2
    return {name: int((classes == value).sum()) * px_km2 for value, name in CLASS_NAMES.items()}
