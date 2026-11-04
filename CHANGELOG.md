# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Pre-event reference images for change detection: `plan_reference` picks
  the latest Sentinel-1 pass of the event pass's relative orbit before the
  flood began, `make_pair(..., reference=)` fetches it on the same grid and
  records it in the `_pair.json` (`"reference"`, plus `relative_orbit`).
  CLI: `pairs --reference [--reference-days N]`, priced and cached like
  the event image. `RapidMapping.search_scenes` for any time range.
- `build_dataset(..., reference=True)` / `dataset --reference`: chips with
  `VV, VH, VV_pre, VH_pre, dataMask` bands (mask = both passes have data),
  `reference_time` in `index.csv`. `Pair.reference`.

## [0.5.2] - 2026-11-04

### Added

- `notebooks/01_flood_segmentation_s1.ipynb`: Italian EMS floods as labels,
  Sentinel-1 from the Data Space as images, a VV threshold and a per-pixel
  classifier trained on some areas and scored on others with `evaluate`;
  ~94 PU once. The `notebook` workflow runs it on GitHub with the CDSE
  secrets and a cached data directory. Dependency group `notebooks`.

## [0.5.1] - 2026-11-04

### Fixed

- `copernicus-ems pairs` labelled every feature of a product as flooded,
  landslides and other events included (it asked `observed_events` with
  `event_types=None`): the labels now hold floods only, as documented. A
  product with no flood feature is no longer paired. The labels and the
  `_pair.json` are now always rewritten (they are local and free), so
  running `pairs` again over 0.5.0 pairs corrects them, and costs no PU:
  the Sentinel-1 images are read from the cache.

### Added

- `copernicus_ems.dataset` and `copernicus-ems dataset -o DIR`: the pairs
  already fetched become a training dataset, offline: aligned image and
  label chips (GeoTIFF), `index.csv` with the provenance of each chip,
  `dataset.json` with bands, label values, splits, statistics and credits.
  Train / val / test are assigned by area (activation + AOI), never by
  chip; label pixels without image data become `255`.

## [0.5.0] - 2026-11-04

### Added

- `copernicus_ems.sentinelhub` (extra `cdse`) and `copernicus-ems pairs`:
  Sentinel-1 backscatter (VV, VH, dataMask; calibrated, orthorectified)
  from the Sentinel Hub Process API of the Copernicus Data Space, cut to
  exactly the grid of the label raster, with the labels and a
  `_pair.json` manifest, in the data directory (a Docker volume; no
  bucket). Credentials from `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` or
  `CDSE_CREDENTIALS_FILE`; token reused until it expires; `estimate_pu`
  and `budget_pu` before any request; large grids tiled and mosaicked;
  answers off the asked grid refused; one image per pass and grid, shared
  by the products it serves; `--dry-run` without credentials.
- A live test of the Process API (~1 PU) when the repository has the
  `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` secrets.

## [0.4.0] - 2026-11-04

### Added

- GeoParquet 1.1 output (extra `parquet`: pyarrow): `copernicus-ems export
  -o x.parquet` (and `--masks`), `export_observed_events(file_format=
  "geoparquet")` in the MCP server, `export.write_geoparquet`. EMSR664:
  17.5 MB instead of 68 MB of GeoJSON; reads in GeoPandas and DuckDB.
- GeoTIFF labels (the `ml` extra now includes rasterio):
  `LabelRaster.to_geotiff` / `from_geotiff`, `Chip.save(file_format="tif")`;
  `255` is nodata, the provenance is in the tags.
- `Grid.from_transform`, to rasterize labels on a scene's grid.
- `copernicus_ems.export`: `tagged_features`, `mask_features`,
  `write_geojson`, `write_features`, shared by the CLI and the MCP server.
- Weekly contract tests against the live services (`tests/test_live.py`,
  marker `live`, `COPERNICUS_EMS_LIVE=1`); a failure opens an issue.

## [0.3.0] - 2026-11-04

### Added

- Documentation site on GitHub Pages (Zensical, API reference from
  the docstrings) with a roadmap page: improvements and AI features.
- `copernicus-ems` command (`list`, `fetch`, `export`, also
  `python -m copernicus_ems`): export tags each feature with activation,
  product, AOI and observation time, and can write the observation masks.
- `Product.aoi_extent`: the observation mask as a GeoJSON geometry;
  `wkt_to_geojson` for WKT polygons and multipolygons.
- `since=` / `until=` on `select_activations` and
  `RapidMapping.activations`, both ends included.
- `MASS_MOVEMENT_EVENT_TYPE` (`6-Mass Movement`).
- `copernicus_ems.geo` (extra `geo`: shapely, pyproj): `flooded`,
  `observed_dry`, `mask`, geodesic `area_km2`, `summary` per product.
- `copernicus_ems.ml` (extra `ml`: + numpy): tri-state label rasters
  (`rasterize`: 1 flooded, 0 dry, 255 not observed) in the AOI's UTM zone,
  mask-aware scores (`evaluate`, `evaluate_geometry`: CSI, POD, FAR, F1,
  bias; predictions outside the AOI reported, not counted) and training
  `chips`.
- `copernicus-ems mcp` (extra `mcp`): an MCP server for LLM agents, with
  the tools `list_activations`, `activation_summary`, `compare_products`
  and `export_observed_events` and the prompt `situation_report`. Tools
  return computed facts and file paths, never raw geometry; changes over
  time are only computed between products of the same type.
- `Product.images`: the satellite images a product was made from (sensor,
  type, resolution class, time), often commercial (PAZ, COSMO-SkyMed,
  RADARSAT-2, Pléiades).
- `copernicus_ems.scenes`: open Sentinel-1/2 scenes over a product's AOI
  from the Copernicus Data Space STAC catalogue (no account to search):
  `find_scenes` / `RapidMapping.scenes` nearest first with the time `gap`,
  `passes` to group slices and tiles of one pass, pure `scene_search_body`
  and `parse_scenes`.
- `geo.coverage`: the share of an AOI a scene or a pass covers.
- `copernicus-ems scenes CODE` and the MCP tool `find_scenes`.

### Changed

- `fetch_observed_events` downloads the layers of an activation in
  parallel, `concurrency=4` at a time; the order of the products is kept.

## [0.2.0] - 2026-11-03

### Added

- Layers are saved as `.geojson` and read back from disk on the next call
  (`fetch_layer`, `fetch_observed_events`: `directory=`). Files are written
  to `.part` and renamed, so a file in the cache is whole.
- `default_directory()`: `COPERNICUS_EMS_DATA_DIR` (`ENV_DATA_DIR`) if set,
  else `$XDG_CACHE_HOME/copernicus-ems` or `~/.cache/copernicus-ems`. Point
  it at a volume in Docker.
- `RapidMapping`: the same calls with a client of its own (or yours) and a
  directory; its own client retries network errors and 429/502/503/504
  (`RetryTransport`).
- `layer_path`, and `examples/italy_floods.py`.

### Changed

- `fetch_layer` and `fetch_observed_events` now write to the default
  directory when none is given.
- README rewritten, with diagrams, images and a Docker section.

## [0.1.0] - 2026-11-03

### Added

- Activations (`fetch_activations`, `parse_activations`,
  `select_activations`), following the API's pages.
- Products of an activation (`fetch_products`, `parse_detail`) with label,
  observation time, AOI extent (the observation mask) and vector layers.
- Vector layers as GeoJSON (`fetch_layer`, `parse_layer`) and observed
  events (`fetch_observed_events`, `select_features`), floods by default.
- Extracted from Limen (`src/limen/integrations/copernicus_ems/` at
  9b977bb) and relicensed under Apache-2.0 by its sole author. New: the
  layers are read as GeoJSON instead of from the ~100 MB product packages,
  which also reaches activations whose package answers 403.
