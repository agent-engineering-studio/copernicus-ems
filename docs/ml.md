# Geometry & ML

Two optional modules turn observed events into numbers, label rasters and
scores. They need extras, so the core keeps its single dependency:

```bash
pip install 'copernicus-ems[geo]'   # shapely, pyproj: areas, observed dry
pip install 'copernicus-ems[ml]'    # + numpy: label rasters, scores, chips
```

## Areas and summaries (`copernicus_ems.geo`)

```python
from copernicus_ems.geo import observed_dry, summary

async with RapidMapping() as ems:
    events = await ems.observed_events("EMSR664")

for s in summary(events):
    print(s.product, s.aoi_name, f"{s.flooded_km2:.2f} km² of {s.observed_km2:.1f}")
```

```text
AOI01_DEL_MONIT01 Forlì 112.06 km² of 3214.4
AOI02_GRA_PRODUCT Lugo 135.30 km² of 455.4
AOI07_GRA_PRODUCT Ravenna 63.81 km² of 419.4
...
```

Areas are **geodesic** (WGS84 ellipsoid), with the flooded union clipped to
the AOI. On EMSR762 AOI01 they match Rapid Mapping's own `area` attribute
within 1%. `flooded(event)`, `observed_dry(event)` and `mask(event)` return
shapely geometries in lon/lat.

## Tri-state label rasters (`copernicus_ems.ml`)

| Value | Meaning | Where |
|---|---|---|
| `1` | flooded | inside an `observedEventA` feature |
| `0` | dry | inside the AOI, outside every feature |
| `255` | ignore | outside the AOI: nobody looked |

```python
from copernicus_ems.ml import rasterize

raster = rasterize(events[0], resolution_m=20)  # UTM zone of the AOI by default
raster.labels  # numpy uint8, rows by columns
raster.grid.crs  # "EPSG:32632"
raster.grid.transform  # GDAL affine: rasterio.Affine(*raster.grid.transform)
raster.meta  # activation, product, observed_time, source credit
```

Pixels are labelled by their centre. On the 11 products of EMSR664, at
20 m, the flooded pixels add up to the published areas within 0.1%.

`255` goes straight into a training loss, so a model is never punished for
predicting water where nobody looked:

```python
loss = torch.nn.CrossEntropyLoss(ignore_index=255)
```

To align with a satellite scene, pass its grid:
`rasterize(event, grid=Grid(crs=..., x0=..., y0=..., resolution_m=10, width=..., height=...))`.

## Scoring a flood model, only where someone looked

```python
from copernicus_ems.ml import evaluate, evaluate_geometry, Scores

scores = evaluate(prediction, raster)  # boolean array on raster's grid
scores = evaluate_geometry(predicted_geojson, events[0], resolution_m=20)

scores.csi, scores.pod, scores.far, scores.f1, scores.bias
scores.unobserved_predicted_km2  # outside the AOI: reported, never a false alarm

overall = sum((evaluate_geometry(p, e) for p, e in pairs), Scores.zero())
```

| Score | Formula | Reads as |
|---|---|---|
| CSI (= IoU) | hits / (hits + misses + false alarms) | overall overlap |
| POD | hits / (hits + misses) | share of the observed flood found |
| FAR | false alarms / (hits + false alarms) | share of predicted flood that was observed dry |
| bias | (hits + false alarms) / (hits + misses) | above 1 over-predicts |

Ratios are `nan` when undefined. Scores add up, pooling pixels across
products, and refuse to mix resolutions.

!!! warning "A lower bound"
    Satellite delineation misses short urban floods and water under
    canopy: some "false alarms" inside the AOI may be floods nobody saw.

## Chips for training

```python
from pathlib import Path
from copernicus_ems.ml import chips

for chip in chips(raster, size=256, min_observed=0.5, min_flooded=0.01):
    chip.save(Path("dataset/labels"))  # .npy + .json sidecar (CRS, transform, provenance)
```

`stride=` makes overlapping tiles; edge tiles are padded with `255`. On
EMSR664, 256 × 20 m chips with at least 1% flood: 185.

The imagery to pair the labels with (Sentinel-1/2) is the next step of the
[roadmap](roadmap.md).
