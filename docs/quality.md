# Quality check

## What it answers

An emergency map says where the water was. Nobody can go back and look, so a
natural question is: *can I trust this map?* The quality check gives a first,
cheap answer. It lays the map over a Sentinel-1 radar image of the same place
and day, and reports one of three things:

| Verdict | Plain meaning |
|---|---|
| **coherent** | The radar does not contradict the map. |
| **to check** | Something is odd. Look at this map again before relying on it. |
| **not assessable** | The comparison would mean nothing, and the reason says why. |

This is a **screening, not a judgment**. "To check" means look again; it never
means the map is bad. "Coherent" means the radar does not contradict the map,
not that the map is complete.

It matters for [Limen](https://github.com/agent-engineering-studio/limen),
where these maps are the ground truth of a flood backtest. A forecast scored
against a map that covers only part of the area looks worse than it is. With
the verdict next to each map, Limen can report its scores twice: on all maps,
and without the ones "to check".

```bash
pip install 'copernicus-ems[ml]'
```

## Why a low agreement is normal

Radar and the analysts' map are two independent looks at the water, often
hours or days apart, at different resolutions. Radar sees only open water: it
misses flooding under trees and in towns, and shallow or receded water. So on
independent maps the radar confirms only a small part of the mapped water (the
radar finds only about a third of the water mapped from other satellites in
notebooks 04 and 07, a median share of 0.10 here). That is why the check does **not** ask for a high agreement; it looks for
patterns that a normal map does not show.

Maps drawn on Sentinel-1 itself are the opposite case: they agree with the
radar because they are the radar. They prove nothing, so they are *not
assessable*.

## What it measures

Everything is counted only where the map observed (label other than 255) and
the radar is valid. The radar detector marks water that is new: dark now
(VV below -15.5 dB), not dark in the pre-event image (VV at or above -14.0 dB),
in patches of at least 4 pixels.

| Measurement | Meaning |
|---|---|
| `gap_hours` | radar pass minus the map's observation time |
| `coverage` | share of the observed area with valid radar |
| `same_image` | the map was made from Sentinel-1 |
| `mapped_km2` | mapped water |
| `confirmed_share` | share of the mapped water the detector also marks |
| `unmapped_km2` | detector water where the map says dry |
| `unmapped_clustered_km2` | the part of it in patches of at least 1 km², more than 120 m from mapped water |
| `mapped_extent` | the mapped water's bounding box as a share of the area's |
| `unmapped_outside_extent_km2` | clustered unmapped water outside that box |

## The verdict

**Not assessable**, the first reason that applies:

- the map was made from Sentinel-1;
- the pair is not at 40 m (the thresholds are calibrated there);
- no pass time, or the radar pass is more than 24 h from the map;
- no pre-event reference image;
- the radar covers under 90 % of the observed area;
- under 0.1 km² of mapped water.

Otherwise the map gets flags, each with its numbers, and is **to check** if any
fires, **coherent** if none does.

| Flag | Fires when | Constant |
|---|---|---|
| possibly partial | `mapped_extent` below 0.1 **and** clustered water outside the extent over 2.0 times the mapped water | `PARTIAL_EXTENT = 0.1`, `PARTIAL_RATIO = 2.0` |
| much unmapped water | `unmapped_clustered_km2` over 3.0 times `mapped_km2` | `UNMAPPED_RATIO = 3.0` |
| little confirmed | `confirmed_share` below 0.01 | `LITTLE_CONFIRMED = 0.01` |

The constants are in `copernicus_ems.quality`. They were calibrated on the 24
maps below so that the known suspect is flagged and at most a fifth of the
assessable maps are "to check". Where the two aims conflicted, few reliable
alarms won: "little confirmed" had to be tightened from 0.1 to 0.01.

## Run it

```bash
copernicus-ems quality EMSR664 --dry-run --fetch   # the plan and its PU, nothing spent
copernicus-ems quality EMSR664 --fetch             # fetch the 40 m pairs, then check
copernicus-ems quality EMSR664 --aoi 1             # pairs already on disk
copernicus-ems quality EMSR664 --json              # one JSON object per product
```

| Option | Meaning |
|---|---|
| `--aoi`, `--product` | only this AOI number, or this product |
| `--fetch` | first fetch the pairs (40 m, with a pre-event image) as `pairs` does |
| `--budget-pu` | with `--fetch`: stop above this many PU |
| `--dry-run` | with `--fetch`: show the plan and its PU, then stop |
| `--json` | one JSON object per product |

A `_quality.json` is written beside each pair. It also records the flag
thresholds in force (`thresholds`), so verdicts from a later recalibration can
be told apart. The measurements of a *not assessable* report (a map drawn on
Sentinel-1 can show a confirmed share of 0.885) must never be quoted as
confirmation. In Python:

```python
from pathlib import Path

from copernicus_ems.quality import check_pairs

for report in check_pairs(Path("data"), ["EMSR664"]):
    print(report.product, report.verdict, report.reason or "")
    for flag in report.flags:
        print("  ", flag.code, flag.text)
```

A flag text reads, for example: "the radar sees 4.5× the mapped water as large
unmapped darkened patches away from it".

## The 24 Italian maps

Same pairs as the other notebooks; the table is produced by
[notebook 08](notebooks.md). 13 coherent, 3 to check, 8 not assessable.
*Conf* is the confirmed share, *clustered* the clustered unmapped water,
*extent* the mapped extent, *outside* the clustered water outside it.

| Activation | Product | Made from | S-1 | Gap h | Mapped km² | Conf | Clustered km² | Extent | Outside km² | Verdict | Flags |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| EMSR659 | AOI04_FEP_PRODUCT | Sentinel-1 | yes | −0.0 | 11.13 | 0.115 | 0.00 | 0.360 | 0.00 | not assessable | |
| EMSR664 | AOI01_DEL_MONIT03 | COSMO-SkyMed | no | +12.5 | 44.88 | 0.601 | 18.94 | 0.246 | 13.57 | coherent | |
| EMSR664 | AOI01_GRA_PRODUCT | Pléiades | no | −5.1 | 7.70 | 0.024 | 34.63 | 0.013 | 34.63 | **to check** | possibly partial, much unmapped |
| EMSR664 | AOI03_GRA_PRODUCT | Pléiades | no | −5.1 | 5.81 | 0.020 | 0.00 | 0.798 | 0.00 | coherent | |
| EMSR664 | AOI04_GRA_PRODUCT | WorldView-2 | no | +19.2 | 1.91 | 0.021 | 0.00 | 0.588 | 0.00 | coherent | |
| EMSR664 | AOI05_GRA_PRODUCT | Pléiades Neo | no | −5.0 | 0.16 | 0.150 | 0.00 | 0.181 | 0.00 | coherent | |
| EMSR664 | AOI06_GRA_PRODUCT | SPOT-6/7 | no | +19.2 | 3.03 | 0.441 | 0.00 | 0.450 | 0.00 | coherent | |
| EMSR664 | AOI07_GRA_PRODUCT | SPOT-6/7 | no | +19.2 | 63.84 | 0.145 | 0.00 | 0.987 | 0.00 | coherent | |
| EMSR705 | AOI01_DEL_PRODUCT | COSMO-SkyMed | no | +0.3 | 3.95 | 0.312 | 0.00 | 0.329 | 0.00 | coherent | |
| EMSR705 | AOI02_DEL_MONIT01 | Sentinel-1 | yes | +0.0 | 2.63 | 0.572 | 0.00 | 0.291 | 0.00 | not assessable | |
| EMSR705 | AOI03_DEL_MONIT02 | Sentinel-1 | yes | +0.0 | 12.76 | 0.103 | 0.00 | 0.320 | 0.00 | not assessable | |
| EMSR705 | AOI04_GRA_PRODUCT | Pléiades | no | −5.2 | 0.83 | 0.088 | 0.00 | 0.551 | 0.00 | coherent | |
| EMSR705 | AOI06_GRA_PRODUCT | Pléiades | no | +19.4 | 3.16 | 0.007 | 0.00 | 0.772 | 0.00 | **to check** | little confirmed |
| EMSR705 | AOI08_DEL_MONIT01 | Sentinel-1 | yes | +0.0 | 4.39 | 0.709 | 0.00 | 0.589 | 0.00 | not assessable | |
| EMSR762 | AOI04_GRA_MONIT01 | Drone, GeoEye-1 | no | −1.7 | 11.04 | 0.309 | 0.00 | 0.594 | 0.00 | coherent | |
| EMSR762 | AOI07_GRA_PRODUCT | GeoEye-1 | no | −4.7 | 0.83 | 0.000 | 0.00 | 0.137 | 0.00 | **to check** | little confirmed |
| EMSR795 | AOI02_DEL_PRODUCT | Sentinel-1 | yes | −0.0 | 2.31 | 0.531 | 0.00 | 0.634 | 0.00 | not assessable | |
| EMSR795 | AOI02_GRA_PRODUCT | Pléiades | no | −16.7 | 1.22 | 0.158 | 0.00 | 0.768 | 0.00 | coherent | |
| EMSR795 | AOI06_DEL_PRODUCT | Sentinel-1 | yes | +0.0 | 15.49 | 0.885 | 0.00 | 0.429 | 0.00 | not assessable | |
| EMSR800 | AOI01_GRA_PRODUCT | Pléiades | no | −5.1 | 0.29 | 0.055 | 0.00 | 0.488 | 0.00 | coherent | |
| EMSR843 | AOI01_DEL_MONIT01 | SPOT-6/7 | no | −4.6 | 0.47 | 0.109 | 0.00 | 0.422 | 0.00 | coherent | |
| EMSR843 | AOI01_DEL_PRODUCT | WorldView-2 | no | −4.4 | 0.26 | 0.085 | 0.00 | 0.167 | 0.00 | coherent | |
| EMSR855 | AOI01_DEL_MONIT01 | Sentinel-1 | yes | −0.0 | 0.59 | 0.260 | 0.00 | 0.351 | 0.00 | not assessable | |
| EMSR855 | AOI01_DEL_PRODUCT | Sentinel-1 | yes | −0.0 | 1.82 | 0.401 | 0.00 | 0.491 | 0.00 | not assessable | |

The known suspect, EMSR664 AOI01 (Pléiades), is flagged possibly partial and
much unmapped: its mapped water spans 1.3 % of the area, and the radar sees 4.5
times as much in large patches outside it. That fits a map that looked at only
part of the area; the check does not prove it. The two other maps "to check"
are those where the radar confirms almost nothing (0.7 % and 0.0 %), which
water that receded, or lay under vegetation or in towns, could also explain.

## Limits

!!! note "A screening, not a classifier"
    One known suspect, 16 assessable maps, one country and this detector.
    Treat the thresholds as a starting point.

- **One known suspect.** The thresholds were fitted so that it is flagged. The
  "much unmapped water" flag fires only on it on these 24 maps (the next map
  is at 0.42 against 3.0), so that threshold has no other example.
- **An Italian lowland sample.** Other terrain, other countries and other
  seasons are untested.
- **40 m only.** Another resolution is not assessable.
- **Radar is blind** under trees, in towns, and to shallow or receded water.
  A map that shows such flooding can look unconfirmed without being at fault.
- **Radar cannot tell a flood from a lake.** Water that is always there is
  removed with the pre-event image, but only where that image is valid.
