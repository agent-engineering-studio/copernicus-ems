# Open imagery (Sentinel)

Labels need images. Rapid Mapping products are often made from
**commercial** satellites, which are not open:

```python
products = await ems.products("EMSR664")
[(i.sensor_name, i.resolution_class) for i in products[0].images]
# [('RADARSAT-2', 'HR+')]   others: COSMO-SkyMed, PAZ, Pléiades…
```

So copernicus-ems finds the **nearest open acquisitions** over each
product's AOI, Sentinel-1 (radar: sees through clouds, the usual choice for
floods) and Sentinel-2 (optical), in the public STAC catalogue of the
[Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu).
Searching needs no account and no extra dependency; **downloading a scene
needs a free Data Space account**.

## From the command line

```bash
copernicus-ems scenes EMSR664 --aoi 1 --optical --max-cloud 30 --days 2
```

```text
AOI01_DEL_MONIT01  2023-05-18 16:55  made from RADARSAT-2 (HR+)
     +0.1 h  sentinel-1-grd  1 scenes  AOI 14%   S1A_IW_GRDH_1SDV_20230518T165837_…
    -35.7 h  sentinel-1-grd  1 scenes  AOI 1%    S1A_IW_GRDH_1SDV_20230517T051134_…
AOI01_GRA_PRODUCT  2023-05-22 10:27  made from Pleiades (VHR1)
     -5.1 h  sentinel-1-grd  1 scenes  AOI 100%  S1A_IW_GRDH_1SDV_20230522T051946_…
    +23.7 h  sentinel-2-l2a  3 scenes  AOI 100%  cloud 8%  S2A_MSIL2A_20230523T100601_…
```

`--json` prints one object per pass, with the download URLs. The command
needs the `geo` extra for the AOI coverage.

## From Python

```python
from datetime import timedelta
from copernicus_ems import SENTINEL_1_GRD, SENTINEL_2_L2A, passes
from copernicus_ems.geo import coverage  # [geo] extra

events = await ems.observed_events("EMSR664")
for event in events:
    matches = await ems.scenes(
        event.product,
        collections=[SENTINEL_1_GRD, SENTINEL_2_L2A],
        within=timedelta(days=2),
        max_cloud_cover=30,
    )
    for p in passes(matches):  # slices of one satellite pass, grouped
        print(
            event.product.label,
            f"{p.gap_hours:+.1f} h",
            p.collection,
            f"{coverage(p.geometry, event):.0%} of the AOI",
            [s.download_url for s in p.scenes],
        )
```

- `find_scenes` / `RapidMapping.scenes` return **scenes**, nearest in time
  first; each `SceneMatch.gap` is scene time minus the product's
  `observed_time`.
- `passes()` groups them by **pass** (`eopf:datatake_id`): a catalogue scene
  is a slice (Sentinel-1) or a tile (Sentinel-2), and one pass often needs
  several to cover an AOI.
- `geo.coverage(footprint, event)` is the share of the AOI a scene or a
  pass covers.

## Choose by time *and* coverage

The same AOI on EMSR664 shows why both matter:

| Product | Nearest pass | AOI covered | Best full pass |
|---|---|---|---|
| AOI01_DEL_MONIT01 (18 May 16:55) | Sentinel-1, **+6 min** | 14% | none within ±2 days |
| AOI01_GRA_PRODUCT (22 May 10:27) | Sentinel-1, −5.1 h | 100% | the same |
| AOI01_DEL_MONIT03 (21 May 16:50) | Sentinel-1, +12.5 h | 100% | the same |

!!! warning "The gap is part of the label"
    Water moves. A scene 12 hours after the observation can show a flood
    that has grown or receded: keep `gap_hours` with each image–label pair,
    and pick a threshold for your use. Sentinel-2 `cloud_cover` is for the
    whole tile, not the AOI.

## Pairing with the label rasters

Read the scene (e.g. its `vv` / `vh` Cloud-Optimised GeoTIFF with
rasterio, after authenticating to the Data Space), then rasterize the
labels **on the scene's grid**, so pixels line up:

```python
from copernicus_ems.ml import Grid, rasterize

with rasterio.open(path) as src:  # your Sentinel-1 scene, reprojected to UTM
    t = src.transform
    grid = Grid(
        crs=str(src.crs), x0=t.c, y0=t.f, resolution_m=t.a, width=src.width, height=src.height
    )
labels = rasterize(event, grid=grid)  # 1 flooded, 0 dry, 255 not observed
```
