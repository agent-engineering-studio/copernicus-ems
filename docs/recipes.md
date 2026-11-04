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

## Other hazards: landslides, everything

```python
landslides = await ems.observed_events("EMSR762", event_types=["6-Mass Movement"], notations=None)
everything = await ems.observed_events("EMSR762", event_types=None, notations=None)
```

## The observation mask as a geometry

```python
import shapely  # pip install shapely
from shapely.geometry import shape

mask = shapely.from_wkt(event.product.aoi_extent_wkt)
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
