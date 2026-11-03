# copernicus-ems

Async Python client for [Copernicus EMS Rapid Mapping](https://rapidmapping.emergency.copernicus.eu/):
activations, their products, and the **observed event layers** (flooded
areas, burnt areas, landslides…) as GeoJSON, with the observation masks
that make them usable as ground truth.

[![ci](https://github.com/agent-engineering-studio/copernicus-ems/actions/workflows/ci.yml/badge.svg)](https://github.com/agent-engineering-studio/copernicus-ems/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/copernicus-ems)](https://pypi.org/project/copernicus-ems/)

No account and no key: the dashboard API is public. One dependency: `httpx`.

## Why a library

Rapid Mapping is the best open record of where water actually was after a
flood in Europe, but the obvious way to read it is the expensive and
fragile one:

| The obvious way | What this does instead |
|---|---|
| download `{code}_products.zip` (~100 MB per activation, zips of zips, ortho imagery) and read shapefiles | read each product's vector layer as **GeoJSON** (tens of KB), the same features |
| some product packages answer **403** (EMSR664, Emilia-Romagna, May 2023) | their GeoJSON layers are open: EMSR664 alone is 6,334 flood polygons |
| take the **area of interest** as the event | take `observedEventA`: on EMSR762 AOI01 the AOI is ~1,850 km², the flood 1.19 km² |
| filter activations on category `Flood` | many Italian floods are filed as `Storm`: filter features on `event_type` |
| keep every feature of a flood activation | the same rain's landslides are in it too (`6-Mass Movement`) |

The GeoJSON layers were checked against the shapefiles of the product
packages on 77 layers from 10 Italian activations: same features, same
attributes, same area.

## Install

```bash
pip install copernicus-ems
```

## Use

```python
import asyncio

import httpx

from copernicus_ems import fetch_activations, fetch_observed_events, select_activations


async def main() -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        italy = select_activations(
            await fetch_activations(client), country="Italy", categories=["Flood", "Storm"]
        )
        print(len(italy), "activations in Italy, first", italy[0].code, italy[0].event_time.date())

        events = await fetch_observed_events(client, "EMSR664")
        for e in events[:3]:
            print(e.product.label, e.product.observed_time, len(e.features), "polygons")
        print(sum(len(e.features) for e in events), "flood polygons in", len(events), "products")


asyncio.run(main())
```

```
14 activations in Italy, first EMSR659 2023-05-02
AOI01_DEL_MONIT01 2023-05-18 16:55:00+00:00 462 polygons
AOI01_GRA_PRODUCT 2023-05-22 10:27:00+00:00 92 polygons
AOI01_DEL_PRODUCT 2023-05-17 16:56:00+00:00 1153 polygons
6334 flood polygons in 11 products
```

Each `Product` carries:

- `label` — `AOI01_DEL_PRODUCT`, `AOI02_DEL_MONIT01`: delineation
  (`DEL`), grading (`GRA`) or first estimate (`FEP`), then each
  monitoring update;
- `observed_time` — the earliest satellite image it was made from. Use
  it, not the activation's `event_time`, to tell *when the water was
  seen*: as an anchor for a forecast lead time, `event_time` would credit
  hours in which the flood was already happening;
- `aoi_extent_wkt` — the **observation mask**. Inside it, no flood
  polygon means "observed dry"; outside it, nothing was observed, so a
  false alarm there cannot be told from an event nobody looked at;
- `layers` — every vector layer by kind (`observedEventA`,
  `floodDepthA`, `maximumFloodExtentA`, …), the latest version of each.

Features are GeoJSON in CRS84 (lon/lat WGS84), with the Rapid Mapping
attributes (`event_type`, `notation`, `obj_desc`, `det_method`, …).
`select_features` keeps `5-Flood` with "Flooded area" and "Flood trace"
by default; pass `event_types=None, notations=None` for everything.

### Limits

- The public API lists activations **from 2023**; older ones live on the
  legacy site, with no documented API.
- Satellite delineation is a lower bound: short urban floods and water
  under canopy may not show. A false alarm measured against it may be an
  event nobody saw.

### Bring your own HTTP

`activations_url` / `parse_activations`, `detail_url` / `parse_detail`,
`parse_layer` / `select_features` are pure functions for callers with
their own HTTP layer.

## Data licence

© European Union, Copernicus Emergency Management Service. Free to use,
with attribution: anything you publish from these layers must say so.
The test fixtures are small Rapid Mapping responses under the same terms.

Extracted from [Limen](https://github.com/agent-engineering-studio/limen),
which uses the observed flood extents as the ground truth of its flood
backtest.

## Development

```bash
uv sync
make check
```

## License

Code: [Apache-2.0](./LICENSE).
