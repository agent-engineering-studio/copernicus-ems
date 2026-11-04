# Getting started

## Install

```bash
pip install copernicus-ems
```

Python 3.12 or newer. No key to create, nothing to configure.

## The floods of an activation

```python
import asyncio

from copernicus_ems import RapidMapping


async def main() -> None:
    async with RapidMapping() as ems:
        italy = await ems.activations(country="Italy", categories=["Flood", "Storm"])
        print(len(italy), "activations in Italy, first", italy[0].code, italy[0].event_time.date())

        events = await ems.observed_events("EMSR664")
        for e in events[:3]:
            print(e.product.label, e.product.observed_time, len(e.features), "polygons")
        print(sum(len(e.features) for e in events), "flood polygons in", len(events), "products")
        print("saved in", ems.directory)


asyncio.run(main())
```

```text
14 activations in Italy, first EMSR659 2023-05-02
AOI01_DEL_MONIT01 2023-05-18 16:55:00+00:00 462 polygons
AOI01_GRA_PRODUCT 2023-05-22 10:27:00+00:00 92 polygons
AOI01_DEL_PRODUCT 2023-05-17 16:56:00+00:00 1153 polygons
6334 flood polygons in 11 products
saved in /home/you/.cache/copernicus-ems
```

Run it again: the 11 layers are read from disk and the only request left is
the activation detail (12.7 s the first time, 2.1 s the second, on EMSR664).

## Every flood of a country, in one file

[`examples/italy_floods.py`](https://github.com/agent-engineering-studio/copernicus-ems/blob/main/examples/italy_floods.py)
writes one GeoJSON with every flood polygon of a country, each tagged with
its activation, product and observation time:

```bash
uv run python examples/italy_floods.py              # -> italy_floods.geojson
uv run python examples/italy_floods.py Spain spain.geojson
```

Open the result in QGIS or drop it on [geojson.io](https://geojson.io).

## Where downloads are saved

The GeoJSON layers are saved in a directory, which is also the cache. If
you don't pass one (`RapidMapping(directory=...)`, or `directory=` on the
functions):

1. `COPERNICUS_EMS_DATA_DIR`, if set;
2. otherwise `~/.cache/copernicus-ems` (or `$XDG_CACHE_HOME/copernicus-ems`).

```text
$COPERNICUS_EMS_DATA_DIR/
├── EMSR664/
│   ├── EMSR664_AOI01_DEL_PRODUCT_observedEventA_v1.geojson
│   └── …
└── EMSR762/
    └── EMSR762_AOI01_DEL_PRODUCT_observedEventA_v1.geojson
```

Every flood mapped in Italy since 2023 takes about **200 MB** (14
activations, November 2026).

### In Docker

Point the variable at a **persistent volume**, or the cache is lost with
the container:

```yaml
services:
  app:
    environment:
      COPERNICUS_EMS_DATA_DIR: /data/copernicus-ems
    volumes:
      - ems-data:/data/copernicus-ems
volumes:
  ems-data:
```
