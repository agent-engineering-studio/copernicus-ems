# Recipes

## Your own HTTP client

Timeouts, proxies, retries stay yours; `RapidMapping` leaves your client
open:

```python
async with httpx.AsyncClient(timeout=120, proxy="http://proxy:3128") as client:
    ems = RapidMapping(client, directory=Path("data/ems"))
    events = await ems.observed_events("EMSR762")
```

Or the functions directly:

```python
from copernicus_ems import fetch_layer, fetch_observed_events, fetch_products

events = await fetch_observed_events(client, "EMSR762", directory=Path("data/ems"))
products = await fetch_products(client, "EMSR762")
depth = await fetch_layer(client, products[0], "floodDepthA")  # None if absent
```

## A season, not the whole archive

```python
from datetime import date

autumn = await ems.activations(country="Italy", since=date(2024, 9, 1), until=date(2024, 11, 30))
```

## Other hazards: landslides, everything

```python
from copernicus_ems import MASS_MOVEMENT_EVENT_TYPE

landslides = await ems.observed_events(
    "EMSR762", event_types=[MASS_MOVEMENT_EVENT_TYPE], notations=None
)
everything = await ems.observed_events("EMSR762", event_types=None, notations=None)
```

## The observation mask as a geometry

```python
import shapely  # pip install shapely
from shapely.geometry import shape

mask = shape(event.product.aoi_extent)  # GeoJSON; or shapely.from_wkt(aoi_extent_wkt)
flooded = shapely.union_all([shape(f["geometry"]) for f in event.features])
observed_dry = mask.difference(flooded)
```

## Handle errors

```python
from copernicus_ems import EmsError

try:
    events = await ems.observed_events("EMSR762")
except EmsError as exc:
    print("Rapid Mapping said no:", exc)  # HTML instead of GeoJSON, …
except httpx.HTTPError as exc:
    print("network:", exc)  # after the retries of RapidMapping
```

## Bring your own HTTP (sync)

```python
import httpx

from copernicus_ems import detail_url, parse_detail, parse_layer, select_features

with httpx.Client(timeout=60, follow_redirects=True) as http:
    products = parse_detail(http.get(detail_url("EMSR762")).content)
    for product in products:
        if (layer := product.layers.get("observedEventA")) is not None:
            floods = select_features(parse_layer(http.get(layer.url).content))
            print(product.label, len(floods))
```

## A truth set for a store: polygons, masks, and what is known

`fetch_activation_truth` (`[geo]` extra) gives the flood polygons and the
observation masks of an activation with stable ids, and tells apart the
products it read from those it could not.

```python
import httpx

from copernicus_ems import RapidMapping
from copernicus_ems.truth import fetch_activation_truth

async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
    activation = (await RapidMapping(http).activations(country="Italy"))[-1]
    truth = await fetch_activation_truth(http, activation, area_crs=3035)

for flood in truth.floods:  # id EMSR762-aoi01-DEL0-12-0: the same at every run
    print(flood.id, f"{flood.area_km2:.3f} km²", flood.observed_time)
print(truth.read_products)  # only for these a missing polygon was removed at the source
print(truth.errors)  # layers that did not answer: what you stored of them stays
```

A mask is in `truth.masks` only where the observed layer was read: with the
layer lost, a mask would call dry a place nobody knows about.

## Observed water over your own polygons

`copernicus_ems.overlay` (`[geo]` extra) takes geometries already in one
metric CRS (EPSG:3035 for Europe) and knows no layer by name: the places and
the zones are yours.

```python
from copernicus_ems.ids import is_monitoring, polygon_product_id
from copernicus_ems.overlay import WaterPolygon, places_hit, split_by_zones

# floods: ObservedFlood records projected to EPSG:3035 by you (to_3035).
water = [
    WaterPolygon(polygon_product_id(f.id), to_3035(f.geom), f.event_time, f.observed_time)
    for f in truth.floods
    if not is_monitoring(polygon_product_id(f.id))  # the first issue: no water counted twice
]

for hit in places_hit(water, municipalities):  # [(code, polygon), ...]
    print(hit.key, f"{hit.water_km2:.2f} km²", hit.products)

split = split_by_zones(
    [w.geom for w in water],
    hazard_zones,  # [(class, polygon), ...]: any layer with a class
    classes=("high", "medium", "low"),  # from the highest: it wins where zones overlap
)
print(split.by_class_m2, split.share(split.outside_filtered_m2))
```

It is water observed afterwards, from satellite: a lower bound of what
happened, never an alert or a forecast. Quote `attribution(year)` with it.

## The radar reports, read back

```python
from copernicus_ems import default_directory
from copernicus_ems.reports import TO_CHECK, collect_reports

scan = collect_reports(default_directory())  # <dir>/<CODE>/sentinel-1/*_quality.json, …
for report in scan.reports:
    if report.verdict == TO_CHECK:  # look again: never "wrong"
        print(report.product_id, report.quality.flag_codes if report.quality else ())
for note in scan.notes:  # files skipped, pairs replaced by a newer one
    print(note.kind, note.subject, note.detail)
```
