# Example notebooks

| Notebook | What it shows | Data | Cost |
|---|---|---|---|
| [`01_flood_segmentation_s1.ipynb`](https://github.com/agent-engineering-studio/copernicus-ems/blob/main/notebooks/01_flood_segmentation_s1.ipynb) | Every Italian flood mapped by Copernicus EMS since 2023 as labels, the nearest Sentinel-1 pass as image: a VV threshold and a per-pixel classifier, trained on some areas and scored on others, only where EMS observed | 8 activations, 23 Sentinel-1 images at 40 m | ~94 PU once, then nothing |

## Run them

```bash
pip install "copernicus-ems[cdse]" scikit-learn matplotlib jupyter
export CDSE_CLIENT_ID=sh-…  CDSE_CLIENT_SECRET=…     # Copernicus Data Space OAuth client
export COPERNICUS_EMS_DATA_DIR=~/copernicus-ems-data # where images and labels are kept (optional)
jupyter lab
```

or, from a clone, `uv sync --extra cdse --group notebooks`. The images are
kept in the data directory: a second run spends no processing units.

On GitHub, the **notebook** workflow (Actions → notebook → *Run workflow*)
runs it with the repository's CDSE secrets, keeps the data directory in
the Actions cache, and uploads the executed notebook and its figure.

### Reuse the images elsewhere, at 0 PU

Each run of the workflow also uploads the data directory as the
`ems-data` artifact (kept 90 days): Sentinel-1 images, labels, pair
manifests and EMS layers, in the layout copernicus-ems expects. Download
it into another project's data directory (a Docker volume, for example)
and `pairs` / `make_pair` find the images there and send nothing:

```bash
gh run download <run id> -R agent-engineering-studio/copernicus-ems \
  -n ems-data -D "$COPERNICUS_EMS_DATA_DIR"
```

An image is reused only for the same request: same grid (AOI and
resolution, 40 m here) and same pass choice (`min_coverage`, `max_gap`,
`within` at their defaults). Labels are always rewritten locally, for free.

The notebooks are stored without outputs. Each one saves its figure in
`docs/images/`.

*Data: Copernicus EMS Rapid Mapping, © European Union; Copernicus Sentinel-1,
processed by Sentinel Hub on the Copernicus Data Space Ecosystem.*
