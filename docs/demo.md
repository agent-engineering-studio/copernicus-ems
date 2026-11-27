# The demo

A map of Italy that says, in three steps, what copernicus-ems is for. One dot
for each Copernicus EMS emergency (floods and storms, from 2023). Click one
and the map shows the dashed boxes where the satellites looked (the
observation masks) and, in blue, the water they saw. A card gives the same
numbers, product by product, with the lines of Python that compute them.

![The demo: the emergencies in Italy, and what copernicus-ems is for](images/demo.png)

![The demo on EMSR664, Emilia-Romagna, May 2023](images/demo-emsr664.png)

## Run it

With Docker, from the root of the repository:

```bash
docker compose run --rm init   # once: the floods of Italy, about 200 MB, into ./data
make docker                    # the demo, in the background, on http://localhost:3000
make up                        # + the MCP server for AI agents, on http://127.0.0.1:8770/mcp
make down                      # stop everything; the data stays
```

With [uv](https://docs.astral.sh/uv/) and [Node](https://nodejs.org/) 20 or
later:

```bash
make init    # install everything and download the floods of Italy
make demo    # the demo, on http://localhost:3000
```

Without `init` it works all the same, reading Rapid Mapping live: each layer
read is saved, so the second visit is fast.

`/?code=EMSR664` opens the page on that activation, to link to it.

## What is in it

| Click | The page shows | From |
|---|---|---|
| the map, with nothing open | one dot per activation of the country | `read_activations`, `read_products` (`aoi_extent`) |
| a dot, or a row of the list | the masks and the water seen, the km² of each product | `RapidMapping.layer`, `select_features`, `geo.flooded`, `geo.area_km2` |
| a product in the card | that product alone on the map | the same, filtered |

The total of an activation takes, for each area, the product with the most
water: monitoring products show the same place on different days, and adding
them would count the same water twice.

The page is in Italian and in English: it opens in the language of the
browser, and **IT | EN** at the top changes it. The words are in
[`web/lib/i18n.ts`](https://github.com/agent-engineering-studio/copernicus-ems/blob/main/demo/web/lib/i18n.ts).

## How it is made

| Folder | What it is |
|---|---|
| [`api.py`](https://github.com/agent-engineering-studio/copernicus-ems/blob/main/demo/api.py) | a small API over the library (Starlette): the list, the card and the map of an activation |
| [`web/`](https://github.com/agent-engineering-studio/copernicus-ems/tree/main/demo/web) | the page (Next.js, Leaflet, [Bootstrap Italia](https://italia.github.io/bootstrap-italia/)): it draws the map and asks the API through its own `/api/*` |

Everything is read from the data directory (`$DATA_ROOT/copernicus-ems`)
first, and from Rapid Mapping when it is not there. The largest activations
are heavy (EMSR664: 1.7 million vertices), and joining their polygons takes
about half a minute. So each activation is prepared once, in a thread, and
kept in memory and in `$DATA_ROOT/copernicus-ems/demo/`. The key is the
layers' versions, so a new monitoring product is seen. At start, the API
prepares in the background every activation `init` saved. The map is
simplified to about 10 m and sent gzipped (EMSR664: 590 kB).

```bash
curl http://localhost:3000/api/health
curl http://localhost:3000/api/activations
curl http://localhost:3000/api/activation/EMSR762
curl http://localhost:3000/api/activation/EMSR762/map
```

| Variable | What it does | Default |
|---|---|---|
| `DATA_ROOT` | the data root; this library uses `$DATA_ROOT/copernicus-ems` | `./data` |
| `COPERNICUS_EMS_COUNTRY` | the country the page shows | `Italy` |
| `COPERNICUS_EMS_PORT` | the port of the page | `3000` |
| `COPERNICUS_EMS_API_PORT` | the port of the API, without Docker | `8000` |
| `COPERNICUS_EMS_API` | where the page finds the API | `http://127.0.0.1:8000` |

This is a demo of a library, not an alert service. A satellite map is a lower
bound: for danger, the official word is the Civil Protection's.
