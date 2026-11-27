---
name: copernicus-ems
description: Read the satellite maps of disasters from Copernicus EMS Rapid Mapping in Python with the copernicus-ems library - activations (EMSR codes), their products, the observed flooded / burnt / landslide areas as GeoJSON with their observation masks, km² of water seen, label rasters and mask-aware scores for flood models, open Sentinel-1/2 passes over each area, an offline copy (init). Use when code or an answer needs where a flood actually was in Europe, ground truth for a flood model, or mentions Copernicus EMS, Rapid Mapping, EMSR, observedEventA or copernicus-ems.
---

# copernicus-ems

`copernicus-ems` is a typed, async Python client for
[Copernicus EMS Rapid Mapping](https://rapidmapping.emergency.copernicus.eu/),
the European service that maps floods, fires, landslides and earthquakes
from satellite images within hours. Use it rather than raw HTTP or the
product zips: the API behind the viewer is undocumented and every trap
below fails quietly. The library already handles them.

Full documentation: https://agentengineering.it/copernicus-ems/

## Setup

```bash
pip install copernicus-ems                 # the client: one dependency, httpx
pip install "copernicus-ems[geo]"          # + km², observed dry area, summaries
pip install "copernicus-ems[ml]"           # + label rasters, CSI/POD/FAR inside the mask
pip install "copernicus-ems[cdse]"         # + Sentinel-1 backscatter on the label grid (CDSE account)
pip install "copernicus-ems[mcp]"          # + the MCP server:  copernicus-ems mcp
```

Python 3.12 or newer. No account and no key for Rapid Mapping: the data are
open (credit "Copernicus Emergency Management Service © European Union").

## The data, in one paragraph

A disaster gets an **activation** with a code (`EMSR664`: Emilia-Romagna,
May 2023) and one or more **areas of interest** (AOI). For each AOI the
analysts publish **products**: a first delineation (`DEL`), monitoring
updates (`DEL_MONIT01`, …), grading (`GRA`), first estimate (`FEP`). The
layer that says where the event actually was is `observedEventA`. The AOI
extent is the **observation mask**: inside it, no event means it was
observed and not there; outside it, nobody looked. The public API lists
activations from 2023 on.

## Recipes

```python
import asyncio
from copernicus_ems import RapidMapping


async def main() -> None:
    async with RapidMapping() as ems:  # own client, retries, disk cache
        floods = await ems.activations(country="Italy", categories=["Flood", "Storm"])
        events = await ems.observed_events("EMSR664")  # one ProductEvents per product
        for e in events:
            print(e.product.label, e.product.observed_time, len(e.features))


asyncio.run(main())
```

Areas, with the mask (`[geo]`):

```python
from copernicus_ems.geo import summary

for s in summary(events):
    print(s.product, f"{s.flooded_km2:.2f} km² of water in {s.observed_km2:.1f} km² observed")
```

Labels and scores for a flood model (`[ml]`):

```python
from copernicus_ems.ml import evaluate, rasterize

labels = rasterize(events[0], resolution_m=20)  # 1 flooded, 0 dry, 255 not observed
scores = evaluate(my_prediction, labels)  # CSI, POD, FAR only where observed
```

Other hazards: `ems.observed_events(code, event_types=["6-Mass Movement"], notations=None)`;
everything: `event_types=None, notations=None`.

Offline, once, then without the network:

```python
from copernicus_ems import init, read_activations, read_products

report = await init()  # Italy, Flood + Storm, observedEventA → $DATA_ROOT/copernicus-ems
rows = read_activations()  # from disk, no network
products = read_products("EMSR664")
```

Data go to `$DATA_ROOT/copernicus-ems` (else `~/.cache/copernicus-ems`);
`RapidMapping(directory=...)` or `init(directory=...)` override it.

## Rules (each one is a real failure)

1. **The AOI is not the event.** On EMSR762 AOI01 the AOI is ~1,850 km² and
   the flood 1.19 km². Report water from `observedEventA`, never the AOI area.
2. **Outside the mask is unknown, not dry.** Never score a prediction or
   state "no flooding" outside `Product.aoi_extent`.
3. **Do not filter activations on category `Flood` only.** Many Italian
   floods are filed as `Storm` (EMSR928, EMSR930). Ask for both, and let
   the features' `event_type` decide (the default keeps `5-Flood`).
4. **Do not add up monitoring products.** `DEL`, `DEL_MONIT01`, `DEL_MONIT02`
   show the same place on different days. Per AOI, take the product with
   the most water (or show the series with `compare_products`), never the sum.
5. **`event_time` is when the event started**, hours or days before the
   image. The time of the observation is `Product.observed_time`.
6. **Do not download `{code}_products.zip`.** ~100 MB of zips of zips, and
   403 for some activations (EMSR664). The GeoJSON layers hold the same
   features.
7. **A satellite map is a lower bound.** Short, urban or under-canopy floods
   may not show. It is not an alert service.
8. **Some products have no `observedEventA`** (ortho-images only, or not
   delivered). Say so; do not report them as zero water.
9. **A quality verdict is a screening, not a judgment.** "to check" means a human should look; never call a map wrong.
10. **Radar-only water is a candidate, never ground truth.** A `candidate` patch may be unmapped water or ploughed, wet soil; never score it as a missed flood. `map_only` is not a map error: radar cannot see water under trees, in towns, or after it receded. A report with `independent: false` (the map was drawn on Sentinel-1) proves nothing: never quote its `confirmed` area or its scores as confirmation.

## Without writing code

```bash
copernicus-ems list --country Italy --category Flood --category Storm
copernicus-ems fetch EMSR664                   # the layers, into the data folder
copernicus-ems export EMSR664 -o emsr664.geojson --masks masks.geojson  # for GIS tools
copernicus-ems scenes EMSR664 --aoi 1          # open Sentinel-1/2 passes over AOI 1
copernicus-ems init                            # the offline copy of Italy
copernicus-ems radar EMSR664                   # where maps and radar agree (ml extra)
```

As tools for an agent (MCP): `copernicus-ems mcp` (stdio), or in Docker
`make up` → `http://127.0.0.1:8770/mcp`. Tools: `list_activations`,
`activation_summary`, `compare_products`, `export_observed_events`,
`find_scenes`.

```bash
claude mcp add copernicus-ems -- uvx --from 'copernicus-ems[mcp]' copernicus-ems mcp
claude mcp add --transport http copernicus-ems http://127.0.0.1:8770/mcp
```

## When reporting results

- Give the activation code, the product and the AOI each number comes from,
  and `observed_time`, not `event_time`.
- Give water together with the area observed: "12.4 km² of water in
  310 km² observed", never a bare km².
- Say which products had no observed-event layer.
- Credit: "Copernicus Emergency Management Service © European Union".
