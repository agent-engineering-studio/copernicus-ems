# Radar agreement

## What it answers

The [quality check](quality.md) gives one verdict per map. The radar agreement
shows *where*: for each pair it splits the observed area into classes, so you
can open the result in a GIS and see the water the map and the radar share,
the water only the map has, and the water only the radar has.

This is a **screening, not a judgment**. A radar-only patch is something to
look at; it is never ground truth.

```bash
pip install 'copernicus-ems[ml]'
```

## The classes

Everything is counted only where the map observed (label other than 255) and
the radar is valid. The radar detector is the one of the quality check.

| Class | Value | Plain meaning |
|---|---:|---|
| `dry` | 0 | Neither the map nor the radar shows water. |
| `confirmed` | 1 | Mapped as flood, and the radar sees water too. |
| `map_only` | 2 | Mapped as flood, the radar does not see it. |
| `candidate` | 3 | Radar water outside the map, away from it, in a patch of at least 1 km². |
| `radar_scattered` | 4 | Any other radar water outside the map: small or close to mapped water. |
| not observed | 255 | The map did not look here, or the radar is not valid. |

`map_only` is not a map error: radar cannot see water under trees, in towns,
or after it receded. A `candidate` may be unmapped water, or ploughed, wet
soil. Never score it as a missed flood.

## Why a low agreement is normal

Radar and the analysts' map are two independent looks, often hours or days
apart, at different resolutions, and radar sees only open water. On
independent maps the radar finds only about a third of the mapped water
(POD 0.31 and CSI 0.23 in notebooks 04 and 07). A low score is the usual
case, not a finding.

Maps drawn on Sentinel-1 itself agree with the radar because they are the
radar. The report marks them `independent: false`; their scores prove nothing
and must not be quoted as confirmation.

## Files

Beside each pair, with the same prefix as its `_pair.json`:

| File | Content |
|---|---|
| `…_agreement.tif` | The classes on the pair's grid: uint8, one band, 255 is nodata, deflate. |
| `…_agreement.geojson` | `confirmed`, `map_only` and `candidate` as one polygon feature each, in EPSG:4326, with `class`, `km2` and `independent`. The GeoTIFF carries the same `independent` tag (`true` or `false`). |
| `…_agreement.json` | The report: km² per class, scores (CSI, POD, FAR), the quality verdict, `independent`, the inputs, the detector thresholds and the files. |

A pair that cannot be compared (not at 40 m, or no pre-event image)
still gets a report with `computed: false` and the reason.

## Run it

```bash
copernicus-ems radar EMSR664              # pairs already on disk
copernicus-ems radar EMSR664 --aoi 1      # only this AOI
copernicus-ems radar EMSR664 --json       # one JSON object per product
```

| Option | Meaning |
|---|---|
| `--aoi`, `--product` | only this AOI number, or this product |
| `--json` | one JSON object per product |

Fetch the pairs first with `copernicus-ems quality CODE --fetch` (or
`copernicus-ems pairs CODE --resolution 40 --reference`): `pairs` alone defaults
to 20 m and no pre-event image, so radar skips those pairs. In Python:

```python
from pathlib import Path

from copernicus_ems.agreement import agree_pairs

for report in agree_pairs(Path("data"), ["EMSR664"]):
    print(report.product, report.independent, report.km2)
```

## Results on the Italian pairs

The 24 pairs of the other notebooks, at 40 m. *Independent* is "no" where the
map was made from Sentinel-1.

| Map | Independent | Quality verdict | confirmed km² | map only km² | candidate km² | scattered km² | CSI | POD | FAR |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| EMSR659 AOI04_FEP_PRODUCT | no (same image) | not assessable | 1.3 | 9.8 | 0.0 | 0.1 | 0.11 | 0.12 | 0.08 |
| EMSR664 AOI01_DEL_MONIT03 | yes | coherent | 27.0 | 17.9 | 18.9 | 20.5 | 0.32 | 0.60 | 0.59 |
| EMSR664 AOI01_GRA_PRODUCT | yes | to check | 0.2 | 7.5 | 34.6 | 31.6 | 0.00 | 0.02 | 1.00 |
| EMSR664 AOI03_GRA_PRODUCT | yes | coherent | 0.1 | 5.7 | 0.0 | 0.0 | 0.02 | 0.02 | 0.05 |
| EMSR664 AOI04_GRA_PRODUCT | yes | coherent | 0.0 | 1.9 | 0.0 | 0.0 | 0.02 | 0.02 | 0.00 |
| EMSR664 AOI05_GRA_PRODUCT | yes | coherent | 0.0 | 0.1 | 0.0 | 0.0 | 0.15 | 0.15 | 0.06 |
| EMSR664 AOI06_GRA_PRODUCT | yes | coherent | 1.3 | 1.7 | 0.0 | 0.7 | 0.36 | 0.44 | 0.33 |
| EMSR664 AOI07_GRA_PRODUCT | yes | coherent | 9.3 | 54.6 | 0.0 | 0.6 | 0.14 | 0.15 | 0.06 |
| EMSR705 AOI01_DEL_PRODUCT | yes | coherent | 1.2 | 2.7 | 0.0 | 0.4 | 0.28 | 0.31 | 0.26 |
| EMSR705 AOI02_DEL_MONIT01 | no (same image) | not assessable | 1.5 | 1.1 | 0.0 | 0.1 | 0.55 | 0.57 | 0.06 |
| EMSR705 AOI03_DEL_MONIT02 | no (same image) | not assessable | 1.3 | 11.4 | 0.0 | 0.1 | 0.10 | 0.10 | 0.05 |
| EMSR705 AOI04_GRA_PRODUCT | yes | coherent | 0.1 | 0.8 | 0.0 | 0.0 | 0.08 | 0.09 | 0.32 |
| EMSR705 AOI06_GRA_PRODUCT | yes | to check | 0.0 | 3.1 | 0.0 | 0.0 | 0.01 | 0.01 | 0.00 |
| EMSR705 AOI08_DEL_MONIT01 | no (same image) | not assessable | 3.1 | 1.3 | 0.0 | 0.1 | 0.69 | 0.71 | 0.04 |
| EMSR762 AOI04_GRA_MONIT01 | yes | coherent | 3.4 | 7.6 | 0.0 | 0.7 | 0.29 | 0.31 | 0.18 |
| EMSR762 AOI07_GRA_PRODUCT | yes | to check | 0.0 | 0.8 | 0.0 | 0.0 | 0.00 | 0.00 | — |
| EMSR795 AOI02_DEL_PRODUCT | no (same image) | not assessable | 1.2 | 1.1 | 0.0 | 0.1 | 0.50 | 0.53 | 0.09 |
| EMSR795 AOI02_GRA_PRODUCT | yes | coherent | 0.2 | 1.0 | 0.0 | 0.0 | 0.15 | 0.16 | 0.15 |
| EMSR795 AOI06_DEL_PRODUCT | no (same image) | not assessable | 13.7 | 1.8 | 0.0 | 5.4 | 0.66 | 0.89 | 0.28 |
| EMSR800 AOI01_GRA_PRODUCT | yes | coherent | 0.0 | 0.3 | 0.0 | 0.2 | 0.03 | 0.06 | 0.92 |
| EMSR843 AOI01_DEL_MONIT01 | yes | coherent | 0.1 | 0.4 | 0.0 | 0.0 | 0.11 | 0.11 | 0.06 |
| EMSR843 AOI01_DEL_PRODUCT | yes | coherent | 0.0 | 0.2 | 0.0 | 0.0 | 0.08 | 0.08 | 0.59 |
| EMSR855 AOI01_DEL_MONIT01 | no (same image) | not assessable | 0.2 | 0.4 | 0.0 | 0.0 | 0.26 | 0.26 | 0.03 |
| EMSR855 AOI01_DEL_PRODUCT | no (same image) | not assessable | 0.7 | 1.1 | 0.0 | 0.0 | 0.39 | 0.40 | 0.04 |

### How to read them

- Candidates appear only on EMSR664 AOI01: the Pléiades `GRA_PRODUCT` (34.6
  km²), the map the quality check flags as possibly partial, and
  `DEL_MONIT03` (18.9 km²). Everywhere else there are none.
- `map_only` dominates on most independent maps: the radar misses water under
  vegetation, in towns and after it receded. POD is mostly below 0.2, and 0.6 at best.
- Maps made from the same image can score much higher (CSI up to about 0.69), though not always: EMSR659 AOI04 and EMSR705 AOI03 score about 0.1. That
  proves nothing: the map and the radar are the same data.
- Candidates are not ground truth. They say where to look again.

## Limits

- **40 m only**, and a pre-event image is needed, as for the quality check.
- **Radar is blind** under trees, in towns, and to shallow or receded water.
- **Radar cannot tell a flood from wet soil** or a lake the pre-event image
  does not cover.
- **An Italian lowland sample**; other terrain and seasons are untested.
