# Radar agreement inside the observation mask — design

Date: 2026-11-27 · Status: approved 2026-11-27

Part 4 of the programme in
[2026-11-26-quality-check-design.md](2026-11-26-quality-check-design.md).
Parts 2 (PAI comparison) and 3 (event record) moved to Limen on 2026-11-27:
**copernicus-ems is a library Limen uses and must not compete with it.**
It improves the EMS data and offers general tools; Italian data, analyses
and their maps belong to Limen.

## Intent

The Sentinel-1 detector of notebooks 04/07 finds ~30 % of the mapped water
(POD 0.31) and about as much unmapped darkened area as mapped (CSI 0.23 on
independent maps). Alone it is not a flood map. Its value is **agreement
with the EMS map**, pixel by pixel, inside the area the map observed. For
each pair already on disk (0 PU) the library writes a separate layer that
splits the observed area into:

| Value | Class | Meaning | For Limen |
|---|---|---|---|
| 1 | `confirmed` | the map says water, the radar sees it | firmer ground truth |
| 2 | `map_only` | the map says water, the radar does not | normal under trees, in towns, receded water; never an error |
| 3 | `candidate` | the radar sees water the map does not, in patches ≥ 1 km² farther than 120 m from mapped water | candidate unmapped water: never ground truth, never a missed warning |
| 4 | `radar_scattered` | other radar-only water (small or next to the map) | noise or edges; reported, not used |
| 0 | `dry` | both say dry | — |
| 255 | not observed | outside the mask, or no valid radar in either pass | — |

## Goals

- `copernicus_ems.agreement` (`[ml]` extra), reusing `quality.detect`,
  `quality._sieve`, `quality._dilate` and the same constants (`CLUSTER_KM2`,
  `NEAR_M`, `RESOLUTION_M`), so the candidates are exactly the "clustered
  unmapped" water the quality check measures.
- A report per pair: km² per class, the scores of the detector against the
  map (`ml.Scores`: CSI, POD, FAR, bias, as `as_dict()`), whether the map is
  **independent** of this radar pass (`not quality.same_image(meta)`), the
  quality verdict of part 1 for the same pair, inputs and detector values.
- Files next to the pair, from its stem (`…_pair.json` →):
  `…_agreement.tif` (uint8 classes, nodata 255, deflate, the class legend
  in the band description, provenance in tags), `…_agreement.geojson`
  (lon/lat polygons of classes 1, 2, 3 with `class` and `km2`), and
  `…_agreement.json` (the report). Rewritten on every run (local, free).
- A command: `copernicus-ems radar CODE [--aoi N] [--product P] [--json]`,
  same filters and 40 m preference as `quality`; without pairs it points to
  `copernicus-ems quality CODE --fetch`.

## Not computed (an `EmsError`-free skip with a reason in the report)

- No pre-event reference (the detector needs it).
- Resolution other than 40 m (thresholds calibrated at 40 m).

A **same-image** map is computed but reported `independent: false`; its
scores and its `confirmed` class must not be quoted as confirmation (docs
and skill say so). A large time gap is not a skip here: the quality verdict
already carries it, and the report shows `gap_hours`.

## Non-goals

- No change to any EMS polygon or label raster.
- No water estimate outside the mask, nor on dates without a map.
- No new fetching: pairs come from `pairs` / `quality --fetch`.
- No demo UI and no MCP tool in this part (part 5 presents it).
- No Limen changes here (Limen may ingest `candidate` as "possible water").

## Anti-goals

- A `candidate` presented as missed water or as an EMS error.
- `map_only` presented as a map error.
- Same-image agreement presented as confirmation.

## Docs

`docs/radar.md` (in nav): the classes, why low agreement is normal, the
table of the Italian pairs on disk (km² per class, CSI/POD/FAR, verdict),
limits. README row, CHANGELOG, roadmap. Skill rules: 9 "a quality verdict
is a screening, not a judgment"; 10 "radar-only water is a candidate, never
ground truth".

## Left to the builder

Polygonizing (`rasterio.features.shapes`, then pyproj to EPSG:4326), the
report's dataclass layout, test fixtures (synthetic pairs from
`tests/pairs.py`).
