# Radar agreement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each Sentinel-1 pair on disk, write a separate layer that splits the EMS map's observed area into confirmed / map-only / candidate / scattered / dry, with a report of km² and scores.

**Architecture:** New module `src/copernicus_ems/agreement.py` (`[ml]` extra) reusing the detector and helpers of `quality.py`; pure array functions plus pair I/O; a `radar` CLI command mirroring `quality`; docs and skill rules.

**Tech Stack:** Python 3.12, numpy, rasterio (features.shapes, sieve), shapely, pyproj, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-radar-agreement-design.md`

## Global Constraints

- Class values exactly: `DRY = 0`, `CONFIRMED = 1`, `MAP_ONLY = 2`, `CANDIDATE = 3`, `RADAR_SCATTERED = 4`, `NOT_OBSERVED = 255`.
- Class names in reports/GeoJSON exactly: `"dry"`, `"confirmed"`, `"map_only"`, `"candidate"`, `"radar_scattered"`.
- Detector and constants come from `copernicus_ems.quality` (`detect`, `_sieve`, `_dilate`, `CLUSTER_KM2`, `NEAR_M`, `RESOLUTION_M`, `same_image`, `check_pair`); never redefine them.
- `candidate` must equal the quality check's "clustered unmapped" pixels: `_sieve(unmapped, round(CLUSTER_KM2 / px_km2)) & ~_dilate(mapped, round(NEAR_M / resolution_m))`.
- Not observed = `labels == 255` OR radar invalid in either pass.
- Skip (no files written except the report, `computed: false`, `reason` set) when: no reference → `"no pre-event reference (pairs --reference)"`; resolution not within 0.5 m of 40 → `"thresholds calibrated at 40 m"`.
- Same-image maps are computed with `independent: false`.
- Never write "wrong" in any text. Never modify the labels GeoTIFF or any EMS file.
- Files from the manifest name: `…_pair.json` → `…_agreement.tif`, `…_agreement.geojson`, `…_agreement.json`; written via a `.part` file then renamed.
- `ruff format`, `ruff check`, `mypy --strict` (as `make check` runs) and coverage gate must pass; run `make check` before each commit.

## Review Focus

- A pair whose radar is invalid everywhere: every pixel 255, all km² 0, scores with nan ratios serialize as JSON `NaN`-free (use `None` for non-finite) — test in Task 2.
- A map with no mapped water at all: no candidate clustering crash, `confirmed`/`map_only` 0 — test in Task 1.
- Same-image map: `independent` false in report and CLI text says "not independent" — tests in Tasks 2 and 3.
- Filters `--aoi/--product` matching nothing print the same hint style as `quality` — test in Task 3.
- GeoJSON coordinates are lon/lat (EPSG:4326), not UTM metres — test in Task 2.

---

### Task 1: Classes on arrays

**Files:**
- Create: `src/copernicus_ems/agreement.py`
- Test: `tests/test_agreement.py`

**Interfaces:**
- Produces: constants above; `CLASS_NAMES: dict[int, str]` (0..4 → names); `classify(detected: NDArray[bool], labels: NDArray[uint8], valid: NDArray[bool], resolution_m: float) -> NDArray[uint8]`; `class_km2(classes: NDArray[uint8], resolution_m: float) -> dict[str, float]` (all five names, always present).

- [ ] **Step 1: Write failing tests** in `tests/test_agreement.py`:
  - `test_classify_each_class`: 40 m grid ~60×60, labels with a mapped block (1), dry elsewhere (0), a 255 border row; `detected` covering half the mapped block, a ≥1 km² block (≥ 625 px at 40 m, e.g. 26×26) far (>3 px) from the mapped block, and a 4-px patch adjacent to the mapped block. Assert: confirmed = detected∩mapped; map_only = mapped∖detected; the big far block is `CANDIDATE`; the adjacent patch is `RADAR_SCATTERED`; border row `NOT_OBSERVED`; rest `DRY`.
  - `test_invalid_radar_is_not_observed`: `valid` False on a column → those pixels 255 even where mapped.
  - `test_candidate_matches_quality_clustered`: on the same arrays, `class_km2(...)["candidate"]` equals `quality.measure(...).unmapped_clustered_km2` (pytest.approx).
  - `test_no_mapped_water`: labels all 0 and a big detected block → candidate, no error; confirmed and map_only 0.
  - `test_class_km2_names_and_values`: keys exactly the five names; km² = count × (0.04²).
- [ ] **Step 2: Run** `uv run pytest tests/test_agreement.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `classify` and `class_km2` with the module docstring (what the classes mean; screening, not judgment; radar-only is a candidate, never ground truth). Import guard like `quality.py`.
- [ ] **Step 4: Run** tests → PASS; `make check` → green.
- [ ] **Step 5: Commit** `feat(agreement): classes of radar-map agreement`.

### Task 2: Pair I/O and report

**Files:**
- Modify: `src/copernicus_ems/agreement.py`
- Test: `tests/test_agreement.py` (use `tests/pairs.py` `write_pair`, `add_reference`; `labels` fixture in `tests/conftest.py`)

**Interfaces:**
- Consumes: Task 1; `quality.detect`, `quality._read`, `quality._resolution`, `quality.same_image`, `quality.check_pair`, `ml.evaluate`, `ml.LabelRaster`, `dataset.Pair`, `dataset.find_pairs`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class AgreementReport`: `activation: str`, `product: str`, `computed: bool`, `reason: str | None`, `independent: bool`, `gap_hours: float | None`, `resolution_m: float | None`, `km2: dict[str, float]`, `scores: dict[str, float | int | None]` (from `Scores.as_dict()` with non-finite → None, computed only on valid pixels: detected & valid vs labels where valid else 255), `quality_verdict: str`, `inputs: dict[str, str]`, `detector: dict[str, float]`, `files: dict[str, str]` (names of written tif/geojson); `as_dict() -> dict[str, Any]`.
  - `agree_pair(pair: Pair) -> tuple[AgreementReport, NDArray[uint8] | None]`.
  - `agreement_paths(pair: Pair) -> dict[str, Path]` with keys `"tif"`, `"geojson"`, `"json"`.
  - `write_agreement(pair: Pair, report: AgreementReport, classes: NDArray[uint8] | None) -> AgreementReport` (returns the report with `files` filled; writes json always, tif/geojson only when classes given).
  - `agree_pairs(directory: Path, codes: Iterable[str] | None = None, *, write: bool = True, select: Callable[[Pair], bool] | None = None) -> list[AgreementReport]`.

- [ ] **Step 1: Write failing tests:**
  - `test_agree_pair_writes_three_files` (40 m pair with reference): tif has 1 band uint8, nodata 255, same shape/transform as labels, band description lists the classes, tags include activation/product; geojson features have `class` in {"confirmed","map_only","candidate"} and `km2` > 0; json loads and equals `report.as_dict()`.
  - `test_geojson_is_lon_lat`: all coordinates within [-180,180]×[-90,90] and near the pair's location.
  - `test_same_image_not_independent`: `made_from=[{"sensor": "Sentinel-1"}]` → `computed` True, `independent` False.
  - `test_skip_without_reference` and `test_skip_not_40m` (e.g. 20 m grid): `computed` False, exact reasons from Global Constraints, no tif/geojson written, json written.
  - `test_all_radar_invalid`: dataMask 0 everywhere → all km² 0, json has no NaN (`json.loads` with `parse_constant` raising) .
  - `test_quality_verdict_carried`: report's `quality_verdict` equals `check_pair(pair).verdict`.
  - `test_agree_pairs_select_and_rewrite`: two pairs, select one; rerun rewrites (mtime/contents) without error; labels file bytes unchanged.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Polygonize with `rasterio.features.shapes(classes, mask=classes in {1,2,3}, transform=...)`, group by class into `shapely` geometries, reproject with `pyproj.Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)` + `shapely.ops.transform`; `km2` per feature = pixel count × px_km2 (or geometry area in metres before reprojection). GeoTIFF: reuse the shape of `ml._write_geotiff` (deflate, tiled ≥256, `.part` rename) with band description `"dry=0 confirmed=1 map_only=2 candidate=3 radar_scattered=4 not observed=255"`.
- [ ] **Step 4: Run** → PASS; `make check`.
- [ ] **Step 5: Commit** `feat(agreement): agreement layer and report next to each pair`.

### Task 3: `radar` command

**Files:**
- Modify: `src/copernicus_ems/cli.py` (parser after `quality`; dispatch; `_radar` handler)
- Test: `tests/test_agreement.py`

**Interfaces:**
- Consumes: `agree_pairs`, `CLASS_NAMES`.
- Produces: `copernicus-ems radar CODE [--aoi N] [--product P] [--json]`.

- [ ] **Step 1: Write failing tests** (use `main(["--dir", str(tmp_path), "radar", CODE, ...])` the way `test_quality_command_*` do):
  - text output: one line per product `PRODUCT\tconfirmed X km²\tmap only Y km²\tcandidate Z km²\tCSI c` (2 decimals km², CSI 2 decimals or `—`), plus `not independent` marker for same-image, or `skipped: <reason>`; summary on stderr `N products, K candidates km² total`.
  - `--json`: one object per product equal to `as_dict()`.
  - no pairs → exit 1, stderr `no pairs for CODE: run copernicus-ems quality CODE --fetch`; filter matching nothing with pairs present → exit 1, hint naming the filter (same wording style as `quality`).
  - prefers the 40 m pair when a product has several.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** mirroring `_quality` (same `wanted` filter and 40 m choice; factor shared filter/choice into small helpers used by both only if it stays simple).
- [ ] **Step 4: Run** → PASS; `make check`.
- [ ] **Step 5: Commit** `feat(cli): radar command`.

### Task 4: Docs and skill

**Files:**
- Create: `docs/radar.md`
- Modify: `zensical.toml` (nav after "Quality check": `{ "Radar agreement" = "radar.md" }`), `README.md` (row in "What this library does" next to the quality row), `CHANGELOG.md` (Unreleased), `docs/roadmap.md`, `skills/copernicus-ems/SKILL.md` (rules 9 and 10, plus `radar` in CLI list), `docs/quality.md` (one-line link to radar.md).

- [ ] **Step 1:** Write `docs/radar.md`: what each class means in plain words; why low agreement is normal (POD ~0.31, CSI 0.23 from notebooks 04/07); same-image is not confirmation; candidates are never ground truth; files and their format; Python and CLI usage; a results table placeholder headed "Results on the Italian pairs" that the controller fills from a real run.
- [ ] **Step 2:** Skill rules exactly:
  - `9. **A quality verdict is a screening, not a judgment.** "to check" means a human should look; never call a map wrong.`
  - `10. **Radar-only water is a candidate, never ground truth.** A `candidate` patch may be unmapped water or ploughed, wet soil; never score it as a missed flood. `map_only` is not a map error: radar cannot see water under trees, in towns, or after it receded.`
- [ ] **Step 3:** `make check` (ruff format covers markdown in skills) → green.
- [ ] **Step 4: Commit** `docs: radar agreement`.
