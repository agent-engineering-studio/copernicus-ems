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

## From the command line

The package installs a `copernicus-ems` command (also `python -m copernicus_ems`):

```bash
copernicus-ems list --country Italy --since 2025-01-01        # code, date, category, name
copernicus-ems list --category Flood --json                   # one JSON object per line
copernicus-ems fetch EMSR762 EMSR664                          # layers into the cache
copernicus-ems export --country Italy -o italy.geojson --masks italy_masks.geojson
copernicus-ems export EMSR762 -o emsr762.geojson --all-events # landslides too
copernicus-ems export EMSR664 -o emsr664.parquet              # GeoParquet ([parquet] extra)
```

`export` tags every feature with its `activation`, `product`, `aoi_name`
and `observed_time`; `--masks` writes the observation mask of each product
as a second GeoJSON. By country it reads the `Flood` and `Storm`
activations (`--category` to change it), and keeps the flood features.
`--dir` sets where the layers are saved.

A `.parquet` output (or `--masks`) is written as **GeoParquet 1.1**, with
the `parquet` extra (`pip install 'copernicus-ems[parquet]'`): EMSR664 is
17.5 MB instead of 68 MB of GeoJSON, opens in GeoPandas, DuckDB and QGIS,
and `observed_time` is a real UTC timestamp.

## Every flood of a country, from Python

[`examples/italy_floods.py`](https://github.com/agent-engineering-studio/copernicus-ems/blob/main/examples/italy_floods.py)
writes one GeoJSON with every flood polygon of a country, each tagged with
its activation, product and observation time:

```bash
uv run python examples/italy_floods.py              # -> italy_floods.geojson
uv run python examples/italy_floods.py Spain spain.geojson
```

Open the result in QGIS or drop it on [geojson.io](https://geojson.io).

## Where downloads are saved

Everything the library downloads is saved in one directory, which is also
the cache. If you don't pass one (`RapidMapping(directory=...)`,
`directory=` on the functions, `--dir` on the command line), it is, in
this order:

1. `$DATA_ROOT/copernicus-ems`: `DATA_ROOT` is the data root shared by the
   agent-engineering-studio libraries and Limen, each in a folder of its own;
2. otherwise `~/.cache/copernicus-ems` (or `$XDG_CACHE_HOME/copernicus-ems`).

`COPERNICUS_EMS_DATA_DIR`, if set, still wins over both, for compatibility;
new setups use `DATA_ROOT` (see `.env.example`).

```text
$DATA_ROOT/copernicus-ems/
├── activations.json                     # copernicus-ems init: every activation
├── EMSR664/
│   ├── detail.json                      # copernicus-ems init: products and AOIs
│   ├── EMSR664_AOI01_DEL_PRODUCT_observedEventA_v1.geojson
│   ├── EMSR664_AOI01_DEL_MONIT01_observedEventA_v1.geojson
│   ├── sentinel-1/                      # copernicus-ems pairs (cdse extra)
│   └── …
├── EMSR762/
│   └── …
└── exports/                             # the MCP server's export_* tools
```

Every flood mapped in Italy since 2023 takes about **200 MB** (14
activations, November 2026). Each layer is a plain GeoJSON
FeatureCollection, exactly as Rapid Mapping publishes it: you can open the
folder in QGIS directly.

### Download once, work offline: `init`

```bash
copernicus-ems init                       # Italy, Flood + Storm, observedEventA layers
copernicus-ems init --all-countries --since 2025-01-01 --json
```

```python
from copernicus_ems import init, read_activations, read_products

report = await init(country="Italy", client=my_client, retries=0)
print(report.downloaded, report.skipped, report.failed, report.bytes)
read_activations(country="Italy")  # from disk, no network
read_products("EMSR762")
```

`init(directory=None, *, country="Italy", categories=("Flood", "Storm"),
since=None, until=None, kinds=("observedEventA",), client=None, retries=3,
concurrency=4)` saves the activation list, the detail of each selected
activation and its layers. Run it again whenever you like: layers already
there and valid are skipped (their URL carries their version), the list and
the details are asked again and rewritten only if they changed, and every
file is written as `.part` and renamed. A file that fails is listed in
`report.failed` and the rest goes on; a disk that cannot be written raises.
Pass your own `httpx.AsyncClient`, and `retries=0` if you retry the whole
call yourself. Afterwards `RapidMapping.layer()` and `fetch_layer()` read
the layers from disk.

### In Docker

In a container, mount a **persistent folder** under `DATA_ROOT`, or the cache
is lost with the container and every start downloads everything again:

```yaml
services:
  app:
    environment:
      DATA_ROOT: /data
    volumes:
      - ${DATA_ROOT:-./data}/copernicus-ems:/data/copernicus-ems
```

The folder is created if missing; the container user must be able to write
it.

