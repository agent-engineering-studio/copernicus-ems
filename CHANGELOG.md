# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
