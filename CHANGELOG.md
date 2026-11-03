# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
