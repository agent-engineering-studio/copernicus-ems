# Sentinel-1 backscatter (CDSE)

With a free [Copernicus Data Space](https://dataspace.copernicus.eu)
account, copernicus-ems asks the Sentinel Hub **Process API** for
analysis-ready Sentinel-1 backscatter (calibrated, orthorectified on the
Copernicus 30 m DEM, optionally speckle-filtered) cut to **exactly the grid
of a label raster**. The result is an image–label pair that lines up pixel
for pixel, written to the data directory: no 1 GB GRD archive, no SNAP,
no bucket.

```bash
pip install 'copernicus-ems[cdse]'
```

## Credentials

Create an OAuth client: Dashboard → **User Settings** → **OAuth clients**
→ *Create*, and copy the secret (it is shown once). Then give it to the
library through the environment, never in code:

```bash
export CDSE_CLIENT_ID=sh-…
export CDSE_CLIENT_SECRET=…
# or a JSON file {"client_id": "…", "client_secret": "…"}, e.g. a Docker secret:
export CDSE_CREDENTIALS_FILE=/run/secrets/cdse
```

The token is asked once and reused until it is about to expire.

## From the command line

`pairs` chooses, for every product, the Sentinel-1 pass nearest to its
observation time among those that cover enough of the AOI, prices it, and
writes the pair. Start with `--dry-run`: it needs no credentials and spends
nothing.

```bash
copernicus-ems pairs EMSR664 --dry-run
```

```text
AOI01_DEL_MONIT01  skipped: no pass covers 90% of the AOI (3 passes, best 14%)
AOI01_GRA_PRODUCT  4494x3296 px  -5.1 h  AOI 100%  shares the image of AOI01_DEL_MONIT03
AOI01_DEL_MONIT03  4494x3296 px  +12.5 h AOI 100%  150.7 PU
AOI03_GRA_PRODUCT  524x458 px    -5.1 h  AOI 100%  2.4 PU
AOI08_GRA_PRODUCT  skipped: the nearest pass covering 90% of the AOI is +29.3 h away, over ±24 h
...
6 images to fetch, 215.2 PU estimated
```

```bash
copernicus-ems pairs EMSR664 --aoi 3                 # fetch: 2.4 PU
copernicus-ems pairs EMSR664 --resolution 40         # a quarter of the PU
copernicus-ems pairs EMSR664 --budget-pu 100         # refuse above 100 PU
```

| Option | Default | Meaning |
|---|---|---|
| `--resolution` | 20 | metres per pixel of labels and image |
| `--days` | 2 | Sentinel-1 search window, ± days around the observation |
| `--max-gap-hours` | 24 | farthest pass accepted from the observation time |
| `--min-coverage` | 0.9 | share of the AOI the pass must cover |
| `--budget-pu` | 500 | stop before sending if the estimate is higher |
| `--aoi`, `--product` | all | restrict to one AOI or one product |

Two products of one AOI seen by the same pass **share one image**, and it
is paid once.

## What is written

In the data directory (`COPERNICUS_EMS_DATA_DIR`), next to the layers:

```text
EMSR664/sentinel-1/
├── S1_20230522T0509_20m_3f1c9a…tif              # VV, VH, dataMask (float32, linear power)
├── AOI01_DEL_MONIT03_20m_8b2e…_labels.tif       # 1 flooded, 0 dry, 255 not observed
└── AOI01_DEL_MONIT03_20m_8b2e…_pair.json        # gap, coverage, scenes, sensors, credits
```

- The image's tags carry the pass, the scenes and the processing; the
  `_pair.json` says how far the pass is from the observation (`gap_hours`),
  how much of the AOI it covers, which (often commercial) images the map
  was made from, and the two credits to cite.
- A file already there is not asked again: running the command twice
  costs nothing the second time.
- Each answer is checked to be on the asked grid; a shifted tile is
  refused, not written.

## A pre-event reference, for change detection

One radar image shows dark, smooth surfaces: open water, but also tarmac,
dry bare soil, radar shadow. The usual way past that is to compare the
flood pass with a pass **before** the flood, from the **same relative
orbit** (same viewing geometry), and look at what became darker:

```bash
copernicus-ems pairs EMSR664 --reference --dry-run   # both images, and their PU
copernicus-ems pairs EMSR664 --reference             # about twice the PU of pairs alone
```

- The reference is the latest pass of the event pass's relative orbit
  acquired before the flood began: the activation's `event_time` minus a
  day, looked for `--reference-days` back (default 36, three repeat cycles).
- It is cut to the same grid, written next to the event image, and named in
  the `_pair.json` under `"reference"` (image, pass time, `lead_days`,
  orbit, coverage). Products of one AOI share it, and so do runs: a cached
  reference costs nothing.
- Products with no such pass say why (`no reference: …`) and keep their
  event image.

From Python: `plan_reference(ems, plan, before=…)` after `plan_pair`, then
`make_pair(hub, plan, reference=ref)`.

## From pairs to a training dataset

`dataset` reads the pairs already in the data directory, so it is free
and offline, and cuts them into aligned chips with an index and the splits:

```bash
copernicus-ems pairs EMSR664 EMSR762 …            # once: images (PU)
copernicus-ems dataset -o ./floods-s1 --size 256  # any number of times: free
```

```text
floods-s1/
├── images/EMSR664_AOI03_GRA_PRODUCT_r0_c256.tif  # VV, VH, dataMask
├── labels/EMSR664_AOI03_GRA_PRODUCT_r0_c256.tif  # 1 flooded, 0 dry, 255 ignore
├── index.csv                                     # split, provenance, shares, bounds
└── dataset.json                                  # bands, values, splits, credits
```

- **Splits by area, not by chip.** The products of one AOI (DEL, its
  monitoring updates, GRA) overlap in space: their chips all go to the
  same split, so no ground is in training and in test. Areas are given to
  train / val / test (`--split 0.7,0.15,0.15`, `--seed`) so that chip counts
  approach the ratios.
- **No image, no label.** Where the image has no data (`dataMask` 0) the
  label becomes `255`; chips with less than 90% of image data over their
  observed pixels are dropped.
- `--min-observed` (default 0.5) and `--min-flooded` (default 0) filter the
  chips, `--stride` makes them overlap, positional codes restrict to some
  activations.
- `--reference` keeps only the pairs with a pre-event image and writes
  five bands per chip: `VV, VH, VV_pre, VH_pre, dataMask`, the mask being
  1 only where both passes have data.
- `index.csv` keeps for each chip its activation, product, observation and
  pass times (`reference_time` too), `gap_hours`, observed and flooded shares, CRS and origin;
  `dataset.json` the flooded share per split, to check the balance.

## In Docker

The images go where the layers go: a persistent volume. The credentials
are a secret, not an environment line in the compose file:

```yaml
services:
  app:
    environment:
      COPERNICUS_EMS_DATA_DIR: /data/copernicus-ems
      CDSE_CREDENTIALS_FILE: /run/secrets/cdse
    volumes:
      - ems-data:/data/copernicus-ems
    secrets:
      - cdse
secrets:
  cdse:
    file: ./secrets/cdse.json   # {"client_id": "…", "client_secret": "…"}; keep it out of git
volumes:
  ems-data:
```

## Processing units

A general CDSE account has **10,000 PU and 10,000 requests a month**.
`estimate_pu` applies the documented formula before anything is sent:
area / (512 × 512) × bands / 3 × 2 (FLOAT32) × 2 (orthorectification),
× 2.5 for terrain correction (`GAMMA0_TERRAIN`), × 2 for speckle filtering.

| Grid | PU |
|---|---|
| 512 × 512 px, VV + VH | 2.7 |
| EMSR664 AOI03 at 20 m (524 × 458) | 2.4 |
| EMSR664 AOI01 at 20 m (4494 × 3296) | 150.7 |
| the same at 40 m | 37.7 |

Grids larger than 2,500 px a side are asked in tiles and mosaicked.

## From Python

```python
from copernicus_ems import RapidMapping
from copernicus_ems.sentinelhub import SentinelHub, make_pair, plan_pair

async with RapidMapping() as ems, SentinelHub() as hub:
    for event in await ems.observed_events("EMSR664"):
        plan = await plan_pair(ems, event, resolution_m=20, min_coverage=0.9)
        if plan.scene_pass is None:
            print(event.product.label, plan.reason)
            continue
        image, labels = await make_pair(hub, plan, budget_pu=200)
```

### Check a budget first, send through your own client

`pu_to_spend(plans, references)` totals what `make_pair` would spend:
cached images count zero, and an image shared by several products
counts once. It is free, so an application can check its monthly budget
before sending anything:

```python
from copernicus_ems.sentinelhub import plan_reference, pu_to_spend

plans = [await plan_pair(ems, e, resolution_m=40) for e in events]
refs = [await plan_reference(ems, p) for p in plans]
if pu_to_spend(plans, refs) > budget_left:
    ...
```

An application with its own HTTP policy (a shared client, its retries, a
rate limit) passes `send`, an `async (httpx.Request) -> httpx.Response`
callable, to both clients. Every request then goes through it: Rapid
Mapping, layers, STAC, token, Process API. The library adds no retry of
its own:

```python
async def send(request: httpx.Request) -> httpx.Response:
    return await my_fetch_with_retry(shared_client, request)

async with RapidMapping(send=send) as ems, SentinelHub(send=send) as hub:
    ...
```

Lower level: `fetch_backscatter(hub, grid, start, end, path=…)` for any
grid and time range, `backscatter_request` for the JSON the API receives,
`estimate_pu`, `tiles`.

!!! warning "The gap is part of the label"
    The pass is near the observation, not at it: keep `gap_hours` with
    each pair. Backscatter is linear power; convert to dB
    (`10 * log10(x)`) for display or for thresholds.

> Contains modified Copernicus Sentinel data, processed by Sentinel Hub on
> the Copernicus Data Space Ecosystem. Contains modified Copernicus
> Emergency Management Service information, © European Union.
