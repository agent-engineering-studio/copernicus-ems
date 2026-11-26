# Quality check of the EMS maps — design

Date: 2026-11-26 · Status: approved 2026-11-26

## The programme this belongs to

copernicus-ems exists to check Limen: Rapid Mapping is the open record of
where flood water actually was, and Limen's flood backtest scores its
warnings against it. Three further uses matter to the owner, both for anyone
reading the demo and for Limen:

1. **Hazard maps against reality** — observed water outside the ISPRA PAI
   flood-hazard zones.
2. **The record of an event** — which municipalities flooded, how much, on
   which day, with the official source.
3. **Models that see water** — first a quality check of the EMS maps against
   Sentinel-1, then a radar estimate of the water *inside the observed area*,
   as a separate, scored layer.

Agreed order, one PR (or more) each: **(1) quality check** (this document),
(2) PAI comparison, (3) event record, (4) radar estimate inside the mask,
(5) the demo split into a plain-language page and a technical page that
present all of it.

Agreed architecture (approach A): **copernicus-ems stays general.** It
offers functions such as "compare the water with any set of zones" and
"split by any boundaries"; the demo and Limen bring the Italian data (PAI
from ispra-idrogeo, ISTAT municipalities). No new dependency of the
library on Italian sources.

## Intent

Give each EMS map a **screening verdict with its numbers**, computed from the
Sentinel-1 radar inside the area the map observed, so that:

- Limen knows which ground-truth maps to trust, and reports its backtest
  both with all maps and without the doubtful ones;
- the demo can say, in plain words, "the radar confirms this map" or "this
  map may be partial";
- nothing about the map itself is changed: no polygon is added, removed or
  edited.

## What we know before starting (notebooks 01–07)

- Detector that carries best (notebook 04): **VV < −15.5 dB, VV before the
  event ≥ −14 dB, patches of at least 4 pixels**, at 40 m.
- On maps drawn on **other** sensors, that detector finds only ~30 % of the
  mapped water (POD 0.31) and about as much unmapped darkened area as
  mapped area (notebook 07, any-map rule: CSI 0.23). So **a low agreement is
  normal** and cannot by itself flag a map.
- On maps drawn on the **same** Sentinel-1 image, agreement measures the
  analyst's own reading of that image (CSI 0.40): it proves nothing.
- One known suspect: **EMSR664 AOI01, the Pléiades map** (7.7 km²), whose water
  lies in 55–64 % of the area's columns and 55–69 % of its rows while its
  label says dry everywhere else; combining it with the COSMO-SkyMed map of
  the same pass halves the false area.
- The 23 images and 24 references of the notebooks (40 m, ~190 PU) are in the
  `ems-data` artifact of the notebook workflow, kept 90 days from
  4 November 2026 (until about 2 February 2027).

## Goals

- `copernicus_ems.quality`: one function that, given a product's pair
  (event image, pre-event reference, labels, manifest), returns a
  `QualityReport` with measurements, flags and a verdict.
- Three verdicts: **coherent**, **to check**, **not assessable**. Never
  "wrong".
- Every flag carries its reason in words and the numbers behind it.
- A command, `copernicus-ems quality CODE [--aoi N] [--product P]`, that
  reads existing pairs (0 PU) and, with `--fetch`, makes the missing ones
  through `pairs`' planning and budget (`--budget-pu`, `--dry-run`).
- The report is saved next to the pair:
  `<code>/sentinel-1/<pair stem>_quality.json`, and rewritten when its
  inputs change (it is local and free).
- Calibrated and documented on the 24 Italian maps of the notebooks.

## Non-goals

- Not a flood detector the library returns as data (that is part 4, and
  only inside the mask, as a separate layer).
- No foundation models, no GPU.
- No Sentinel-2 / optical check in this part (notebook 06 is a possible
  later flag).
- No MCP tool and no demo UI in this part: the demo shows it in part 5;
  an MCP tool can follow once the verdict proves useful.

## Anti-goals (a failure even if it runs)

- A verdict that reads as "this map is wrong". The radar cannot see water
  under trees, in towns, shallow or receded; the official map stays the
  official map.
- A verdict on a same-image map presented as confirmation.
- Flags that fire on most maps: a screening that flags everything is noise.
- Changing a single EMS polygon, or Limen silently dropping maps.

## The measurements (per product, inside the observation mask)

On the pair's grid, with `observed = labels != 255`, `mapped = labels == 1`,
and the detector `D = (VV_dB < −15.5) & (VVpre_dB ≥ −14)`, sieved to
patches ≥ 4 pixels:

| Name | Meaning |
|---|---|
| `gap_hours` | radar pass minus the map's observation time (from the manifest) |
| `coverage` | share of the observed area with valid radar (dataMask) |
| `same_image` | the map was made from this Sentinel-1 pass (manifest `made_from`) |
| `mapped_km2` | mapped water |
| `confirmed_share` | share of mapped water the detector also marks |
| `unmapped_km2` | detector water where the map says dry |
| `unmapped_clustered_km2` | the part of it in patches ≥ 1 km², farther than 120 m from mapped water |
| `mapped_extent` | share of the observed area's rows and columns spanned by mapped water (bounding box) |
| `unmapped_outside_extent_km2` | clustered unmapped water outside that bounding box |

All areas in km² at the pair's resolution; all shares in [0, 1].

## Resolution and same image (builder's calls, settled)

- **40 m only.** The detector and every threshold are calibrated at 40 m.
  A pair at another resolution is *not assessable* ("thresholds calibrated
  at 40 m"). `--fetch` asks for 40 m (a quarter of the PU of 20 m). When a
  product has pairs at several resolutions, the 40 m one is used. The
  pixel parameters are stated as pixels at 40 m (4 px = 0.0064 km², 120 m =
  3 px). *Our call, from the calibration data.*
- **Same image = the notebooks' rule:** a map whose sensor list
  (`made_from`) includes Sentinel-1 counts as same-image, whatever the pass,
  including mixed-sensor maps. It is the conservative reading — a
  same-image map is never presented as confirmation (an anti-goal) — and
  it reproduces the notebooks' 8 same-image / 16 independent split.
  *Our call.*

## The flags and the verdict

**Not assessable** when any of: `same_image`; `|gap_hours|` above 24;
`coverage` below 0.9; no reference pass (the pre-event mask is what makes the
detector usable); `mapped_km2` under 0.1 (too little water to say anything).
The reason is given.

Otherwise, flags (each with its numbers):

- **possibly partial** — mapped water spans a small part of the area
  (`mapped_extent` below a calibrated threshold) **and** clustered
  unmapped water outside that extent is large compared with the mapped
  water (`unmapped_outside_extent_km2 / mapped_km2` above a calibrated
  threshold).
- **much unmapped water** — `unmapped_clustered_km2 / mapped_km2` above a
  calibrated threshold.
- **little confirmed** — `confirmed_share` far below the typical value of
  independent maps (below a calibrated threshold, e.g. half the median).

Verdict: **to check** if any flag fires, **coherent** otherwise.

**Calibration.** The thresholds are set on the 24 maps of the notebooks so
that the known suspect (EMSR664 AOI01 Pléiades) is flagged as possibly
partial and no more than about a fifth of the assessable maps are "to
check". They are module constants, documented with the table of all 24
maps and their measurements; a notebook (`08_quality_check_s1.ipynb`)
produces that table. With one known suspect this is a screening, not a
validated classifier, and the documentation says so.

**If the two calibration aims conflict, few reliable alarms win** (owner's
decision, 2026-11-26). The cap of about a fifth of the assessable maps holds;
to stay under it, the weaker flags ("little confirmed", then "much unmapped
water") are tightened or dropped first. If the known suspect still cannot be
flagged without breaking the cap, it ships unflagged and the documentation
says so, with its measurements.

## Where things go

- `src/copernicus_ems/quality.py` in the `[ml]` extra (numpy, rasterio;
  patches with `rasterio.features.sieve` and connected components from
  rasterio/shapely, no scipy dependency). Pure functions on arrays plus a
  reader of the pair files.
- `QualityReport` (frozen dataclass): the measurements above, `flags:
  list[Flag]` (`code`, `text`, numbers), `verdict`, `reason` (for not
  assessable), the inputs used (image, reference, labels, detector
  parameters), `as_dict()`.
- CLI `quality` in `cli.py`; `--json` for one object per product.
- Exported from `copernicus_ems` lazily, like the other `[ml]` names.
- Docs: a "Quality check" page (what it measures, why low agreement is
  normal, the table of the 24 maps, the limits); README "What this library
  does" row; CHANGELOG; the agent skill gets a rule ("a quality verdict is
  a screening, not a judgment").
- Tests: synthetic pairs written to a tmp directory (no network): a
  same-image map, a large gap, a coherent map, a partial map, a map with
  much unmapped water; plus the verdict rules on hand-built arrays.

## Limen (separate PR, in the Limen repo)

- Store the verdict with each EMS product it ingests (column on the
  product/event table or a side table keyed by product), refreshed when
  `copernicus-ems quality` runs.
- `limen backtest-flood` reports its scores twice: all maps, and without
  "to check" maps; it never drops them silently.
- Coordinated with the Limen maintainers (limen-v3); not in this repo.

## What is left to the builder

- Exact threshold values (from the calibration), the 1 km² / 120 m cluster
  parameters if the table shows better ones, the report's file layout
  details, test fixtures.
- Whether `--fetch` reuses `pairs`' option parsing or calls it.
