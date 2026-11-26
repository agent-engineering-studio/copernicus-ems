# Quality check of the EMS maps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every EMS map that has a Sentinel-1 pair with a pre-event reference, compute measurements inside the observation mask and a screening verdict (coherent / to check / not assessable), saved as `_quality.json`, from Python and from `copernicus-ems quality`.

**Architecture:** A new module `copernicus_ems.quality` (`[ml]` extra) with three layers: pure array measurements, pure verdict rules over the manifest and the measurements, and a reader that runs both on a pair from `dataset.find_pairs` and writes the report. The CLI command reuses `_pairs` for `--fetch`. Thresholds are module constants calibrated in a notebook on the 24 Italian maps of notebooks 01–07.

**Tech Stack:** Python 3.12+, numpy, rasterio (`rasterio.features.sieve`), pytest, uv, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-11-26-quality-check-design.md`

## Global Constraints

- Lives in the `[ml]` extra: numpy and rasterio only, **no scipy**.
- Not exported from `copernicus_ems/__init__.py` (no `[ml]` name is): imported as `copernicus_ems.quality`. *(Corrects the spec, which assumed lazy exports exist.)*
- Detector: `VV_dB < −15.5` and `VV_pre_dB ≥ −14.0`, 8-connected patches of at least 4 pixels.
- Calibrated at **40 m only**; any other resolution is not assessable with the reason `"thresholds calibrated at 40 m"`.
- Same image: any `made_from[*].sensor` containing `"Sentinel-1"` → not assessable.
- Verdict words: `"coherent"`, `"to check"`, `"not assessable"`. Never "wrong" in code, output or docs.
- No EMS polygon is ever changed; the module only reads pairs and writes `<pair stem>_quality.json`.
- Calibration: at most about a fifth of the assessable maps "to check"; if that conflicts with flagging EMSR664 AOI01 Pléiades, the cap wins and the docs say so.
- Mypy strict, ruff, coverage gate 90 %, as the rest of the package.

## Review Focus

1. A pair **without a reference** image (most pairs fetched without `--reference`) → not assessable with a reason, never a crash. Test in Task 2 and Task 3.
2. A manifest **without `made_from`** or with an empty list (older pairs) → treated as not same-image; must not crash. Test in Task 2.
3. Labels with **no observed pixel**, or mapped water but **zero valid radar** pixels → not assessable (coverage 0), no division by zero. Test in Task 1 and Task 2.
4. A **reference on a different grid shape** (corrupted or mismatched file) → `EmsError` with the file name, not a numpy broadcast error. Test in Task 3.
5. Running `quality` twice → the same `_quality.json`, rewritten (not appended); a code with **no pairs** → message and exit code 1. Test in Task 4.

---

### Task 1: Measurements on arrays

**Files:**
- Create: `src/copernicus_ems/quality.py`
- Test: `tests/test_quality.py`

**Interfaces:**
- Produces:
  - constants `VV_DB = -15.5`, `VV_PRE_DB = -14.0`, `MIN_PIXELS = 4`, `CLUSTER_KM2 = 1.0`, `NEAR_M = 120.0`, `RESOLUTION_M = 40.0`
  - `detect(vv: NDArray[float32], vv_pre: NDArray[float32]) -> NDArray[bool_]` — inputs in **linear power** (as the GeoTIFFs store them); converts to dB with `10*log10(max(x, 1e-6))`, applies both thresholds, then `rasterio.features.sieve` to drop 8-connected patches under `MIN_PIXELS`.
  - `@dataclass(frozen=True, slots=True) class Measurements` with fields `coverage: float`, `mapped_km2: float`, `confirmed_share: float`, `unmapped_km2: float`, `unmapped_clustered_km2: float`, `mapped_extent: float`, `unmapped_outside_extent_km2: float`, and `as_dict() -> dict[str, float]` (rounded to 4 decimals).
  - `measure(detected: NDArray[bool_], labels: NDArray[uint8], valid: NDArray[bool_], resolution_m: float) -> Measurements`

Definitions (`observed = labels != 255`, `mapped = labels == 1`, `px_km2 = (resolution_m/1000)**2`, everything below restricted to `observed & valid`):
- `coverage` = `(observed & valid).sum() / observed.sum()`, 0 when nothing observed.
- `confirmed_share` = `(detected & mapped).sum() / mapped.sum()`, 0 when no mapped water.
- `unmapped` = `detected & ~mapped`; `unmapped_km2` its area.
- `unmapped_clustered` = pixels of `unmapped` in 8-connected patches ≥ `CLUSTER_KM2 / px_km2` pixels (sieve) **and** farther than `round(NEAR_M / resolution_m)` pixels from mapped water (8-neighbour dilation of `mapped` by repeated `np.roll`-free slicing; write `_dilate(mask, pixels) -> NDArray[bool_]`).
- `mapped_extent` = (row span of mapped / row span of observed) × (column span of mapped / column span of observed), spans as `max - min + 1`; 0 with no mapped water.
- `unmapped_outside_extent_km2` = area of `unmapped_clustered` outside the bounding box of mapped water.

- [ ] **Step 1: Write the failing tests**

```python
def test_detect_thresholds_and_min_size():
    vv = np.full((10, 10), 10 ** (-1.0), dtype=np.float32)        # -10 dB: dry
    pre = np.full((10, 10), 10 ** (-1.0), dtype=np.float32)
    vv[0:2, 0:2] = 10 ** (-2.0)                                     # -20 dB, 4 px: kept
    vv[8, 8] = 10 ** (-2.0)                                         # 1 px: sieved out
    vv[5:7, 5:7] = 10 ** (-2.0); pre[5:7, 5:7] = 10 ** (-2.0)       # dark before: not flood
    got = quality.detect(vv, pre)
    assert got[0:2, 0:2].all() and not got[8, 8] and not got[5:7, 5:7].any()

def test_measure_counts_inside_the_mask_only():
    labels = np.full((100, 100), 255, np.uint8); labels[:, :50] = 0; labels[10:20, 10:20] = 1
    detected = np.zeros((100, 100), bool); detected[10:20, 10:15] = True   # half confirmed
    detected[:, 60:] = True                                                 # outside mask: ignored
    m = quality.measure(detected, labels, np.ones_like(detected), 40.0)
    assert m.coverage == 1.0
    assert m.mapped_km2 == pytest.approx(100 * 0.0016)
    assert m.confirmed_share == pytest.approx(0.5)
    assert m.unmapped_km2 == 0.0

def test_clustered_unmapped_and_extent():
    labels = np.zeros((200, 200), np.uint8); labels[0:10, 0:10] = 1        # water in a corner
    detected = np.zeros((200, 200), bool); detected[100:140, 100:140] = True  # 1600 px = 2.56 km²
    m = quality.measure(detected, labels, np.ones_like(detected), 40.0)
    assert m.mapped_extent == pytest.approx((10 / 200) ** 2)
    assert m.unmapped_clustered_km2 == pytest.approx(2.56)
    assert m.unmapped_outside_extent_km2 == pytest.approx(2.56)

def test_nothing_observed_or_no_valid_radar():
    labels = np.full((10, 10), 255, np.uint8)
    m = quality.measure(np.zeros((10, 10), bool), labels, np.ones((10, 10), bool), 40.0)
    assert m.coverage == 0.0 and m.mapped_km2 == 0.0 and m.confirmed_share == 0.0
    labels[:] = 1
    m = quality.measure(np.zeros((10, 10), bool), labels, np.zeros((10, 10), bool), 40.0)
    assert m.coverage == 0.0
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_quality.py -v`
Expected: FAIL, `ModuleNotFoundError: copernicus_ems.quality`

- [ ] **Step 3: Implement `detect`, `_dilate`, `Measurements`, `measure` in `src/copernicus_ems/quality.py`**

Module docstring states what the check is (a screening, not a judgment) and the ImportError guard of `ml.py` for numpy/rasterio.

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_quality.py -v && uv run mypy && uv run ruff check`
Expected: PASS, no mypy or ruff errors

- [ ] **Step 5: Commit**

```bash
git add src/copernicus_ems/quality.py tests/test_quality.py
git commit -m "feat(quality): radar measurements inside the observation mask"
```

### Task 2: Flags and verdict

**Files:**
- Modify: `src/copernicus_ems/quality.py`
- Test: `tests/test_quality.py`

**Interfaces:**
- Consumes: `Measurements` (Task 1).
- Produces:
  - constants `MAX_GAP_HOURS = 24.0`, `MIN_COVERAGE = 0.9`, `MIN_MAPPED_KM2 = 0.1`, and the calibrated thresholds, provisional until Task 5: `PARTIAL_EXTENT = 0.1`, `PARTIAL_RATIO = 2.0`, `UNMAPPED_RATIO = 3.0`, `LITTLE_CONFIRMED = 0.1`
  - `COHERENT = "coherent"`, `TO_CHECK = "to check"`, `NOT_ASSESSABLE = "not assessable"`
  - `@dataclass(frozen=True, slots=True) class Flag`: `code: str` (`"possibly_partial"`, `"much_unmapped"`, `"little_confirmed"`), `text: str`, `numbers: dict[str, float]`
  - `@dataclass(frozen=True, slots=True) class QualityReport`: `activation: str`, `product: str`, `verdict: str`, `reason: str | None`, `flags: tuple[Flag, ...]`, `measurements: Measurements | None`, `gap_hours: float | None`, `same_image: bool`, `resolution_m: float | None`, `inputs: dict[str, str]`, `detector: dict[str, float]`; `as_dict() -> dict[str, Any]` (JSON-ready)
  - `same_image(meta: Mapping[str, Any]) -> bool`
  - `assess(meta: Mapping[str, Any], measurements: Measurements | None, *, resolution_m: float | None, has_reference: bool) -> QualityReport`

Rules, in this order; the first not-assessable reason wins (copy these texts exactly):
1. `same_image(meta)` → `"the map was made from Sentinel-1: agreement would prove nothing"`
2. `resolution_m` not 40 (±0.5) → `"thresholds calibrated at 40 m"`
3. `abs(gap_hours) > 24` or missing → `"radar pass {gap:+.1f} h from the map, over ±24 h"` / `"no pass time"`
4. `not has_reference` → `"no pre-event reference (pairs --reference)"`
5. `measurements.coverage < 0.9` → `"radar covers {cov:.0%} of the observed area, under 90 %"`
6. `measurements.mapped_km2 < 0.1` → `"too little mapped water to compare ({km2:.2f} km²)"`

Otherwise flags: `possibly_partial` if `mapped_extent < PARTIAL_EXTENT` and `unmapped_outside_extent_km2 / mapped_km2 > PARTIAL_RATIO`; `much_unmapped` if `unmapped_clustered_km2 / mapped_km2 > UNMAPPED_RATIO`; `little_confirmed` if `confirmed_share < LITTLE_CONFIRMED`. Texts are plain sentences with the numbers ("mapped water spans 1 % of the area; the radar sees 3.4× as much darkened water outside it"). Verdict `TO_CHECK` if any flag, else `COHERENT`. `gap_hours` comes from `meta["gap_hours"]`; `same_image` reads `meta.get("made_from") or []`.

- [ ] **Step 1: Write the failing tests**

```python
GOOD = quality.Measurements(coverage=1.0, mapped_km2=10.0, confirmed_share=0.4, unmapped_km2=5.0,
                            unmapped_clustered_km2=2.0, mapped_extent=0.5, unmapped_outside_extent_km2=1.0)
META = {"activation": "EMSR1", "product": "AOI01_DEL_PRODUCT", "gap_hours": 5.0,
        "made_from": [{"sensor": "Pleiades-1A", "time": "2024-01-01T00:00:00+00:00"}]}

def test_coherent(): ...            # assess(META, GOOD, resolution_m=40, has_reference=True).verdict == "coherent", flags == ()
def test_possibly_partial(): ...    # mapped_extent=0.01, unmapped_outside_extent_km2=30 → "to check", flag code "possibly_partial"
def test_much_unmapped(): ...       # unmapped_clustered_km2=40 → flag "much_unmapped"
def test_little_confirmed(): ...    # confirmed_share=0.05 → flag "little_confirmed"
@pytest.mark.parametrize("change, reason_start", [
    ({"made_from": [{"sensor": "Sentinel-1A", "time": "x"}]}, "the map was made from Sentinel-1"),
    ({"gap_hours": 30.0}, "radar pass +30.0 h"),
    ({"gap_hours": None}, "no pass time"),
])
def test_not_assessable_from_the_manifest(change, reason_start): ...
def test_not_assessable_without_reference_resolution_coverage_or_water(): ...
    # has_reference=False; resolution_m=20; coverage=0.5; mapped_km2=0.05 — each its reason, verdict "not assessable", flags ()
def test_missing_made_from_is_not_same_image(): ...   # meta without "made_from" → same_image False, verdict "coherent"
def test_report_as_dict_is_json(): ...                # json.dumps(report.as_dict()) works; keys verdict, reason, flags, measurements
```

(The implementer writes each body as the comment says; assertions are on `verdict`, `reason.startswith(...)` and `[f.code for f in flags]`.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_quality.py -v`
Expected: the new tests FAIL with `AttributeError: ... has no attribute 'assess'`

- [ ] **Step 3: Implement the constants, `Flag`, `QualityReport`, `same_image`, `assess`**

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_quality.py -v && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/copernicus_ems/quality.py tests/test_quality.py
git commit -m "feat(quality): flags and the screening verdict"
```

### Task 3: Check a pair on disk

**Files:**
- Modify: `src/copernicus_ems/quality.py`
- Create: `tests/pairs.py` (move `write_pair` and `add_reference` out of `tests/test_dataset.py`, which then imports them)
- Test: `tests/test_quality.py`

**Interfaces:**
- Consumes: `detect`, `measure` (Task 1), `assess` (Task 2), `copernicus_ems.dataset.Pair` and `find_pairs` (existing).
- Produces:
  - `check_pair(pair: Pair) -> QualityReport` — reads the event image (bands VV, VH, dataMask), the reference (`pair.reference`, bands VV, VH, dataMask) when present, and the labels (`LabelRaster.from_geotiff`); `valid = event dataMask > 0 & reference dataMask > 0`; resolution from `abs(meta["grid"]["transform"][0])`, else from the image's transform; shapes that differ → `EmsError(f"{path.name}: grid {shape} differs from the labels {shape}")`. Without a reference it does not read pixels and returns `assess(..., measurements=None, has_reference=False)`. `inputs` = file names of image, reference, labels.
  - `quality_path(pair: Pair) -> Path` = `pair.manifest` with `_pair.json` replaced by `_quality.json`.
  - `check_pairs(directory: Path, codes: Iterable[str] | None = None, *, write: bool = True) -> list[QualityReport]` — `find_pairs`, then `check_pair` on each; with `write`, `quality_path(pair).write_text(json.dumps(report.as_dict(), indent=2))`.

- [ ] **Step 1: Move the helpers and write the failing tests**

`tests/pairs.py` gets `write_pair(root, activation, product, labels, *, blind_rows=0, made_from=None, gap_hours=35.5)` and `add_reference(manifest, value, *, blind_cols=0)` (existing bodies; `made_from` and `gap_hours` written into the manifest; manifest also gets `"grid": {"crs": grid.crs, "transform": list(grid.transform)}`).

```python
def test_check_pair_with_reference(tmp_path, labels):   # labels at 100 m from the EMSR762 fixture
    # resolution 100 → not assessable "thresholds calibrated at 40 m"; quality json written next to the pair
def test_check_pair_at_40m_coherent(tmp_path):
    # a 40 m LabelRaster built by hand (Grid crs "EPSG:32633", transform (40,0,x0,0,-40,y0)),
    # event VV dark (0.01) exactly on mapped pixels, reference bright (0.1), gap_hours 5 → verdict "coherent"
def test_check_pair_without_reference(tmp_path, labels):
    # write_pair only → verdict "not assessable", reason starts "no pre-event reference"
def test_reference_on_another_grid(tmp_path):
    # reference written with one row fewer → pytest.raises(EmsError, match="_pre.tif")
def test_check_pairs_writes_and_rewrites(tmp_path):
    # two runs → one _quality.json per pair, identical content
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_quality.py tests/test_dataset.py -v`
Expected: test_dataset PASS (helpers moved), new quality tests FAIL (`check_pair` missing)

- [ ] **Step 3: Implement `check_pair`, `quality_path`, `check_pairs`**

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest -q && uv run mypy`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/copernicus_ems/quality.py tests/pairs.py tests/test_dataset.py tests/test_quality.py
git commit -m "feat(quality): check the pairs in the data directory"
```

### Task 4: The `quality` command

**Files:**
- Modify: `src/copernicus_ems/cli.py` (parser next to `pairs`, dispatch next to `"dataset"`)
- Test: `tests/test_quality.py`

**Interfaces:**
- Consumes: `check_pairs`, `QualityReport` (Task 3); `_pairs(ems, args)` (existing, `cli.py:346`).
- Produces: `copernicus-ems quality CODE [--aoi N] [--product P] [--fetch] [--budget-pu PU] [--dry-run] [--json]`.

Behaviour:
- Without `--fetch`: `check_pairs(ems.directory, [code])`, filtered by `--aoi`/`--product` on `report.product`; 0 PU, offline.
- With `--fetch`: first `await _pairs(ems, ns)` with `ns = argparse.Namespace(code=…, aoi=…, product=…, resolution=40.0, days=2.0, max_gap_hours=24.0, min_coverage=0.9, budget_pu=args.budget_pu, reference=True, reference_days=36.0, dry_run=args.dry_run)`; with `--dry-run` stop after it.
- Several pairs of one product: keep the one whose resolution is 40 m; if none, the first.
- Text output, one line per product: `f"{product}\t{verdict}\t{reason or '; '.join(flag texts) or ''}"`, then on stderr `f"{n} coherent, {n} to check, {n} not assessable"`. `--json`: one `as_dict()` per line.
- No pair at all for the code → stderr `"no pairs for {code}: run copernicus-ems quality {code} --fetch"`, exit 1. Otherwise exit 0 (a "to check" verdict is not an error).
- `ImportError` of the `[ml]` extra → message `pip install 'copernicus-ems[ml]'`, exit 2 (as `_pairs` does for cdse).

- [ ] **Step 1: Write the failing tests**

```python
def test_quality_command_text(tmp_path, capsys):   # main(["--dir", str(tmp_path), "quality", "EMSR1"]) == 0; a line per product; summary on stderr
def test_quality_command_json(tmp_path, capsys):   # --json → each stdout line json.loads with "verdict"
def test_quality_command_no_pairs(tmp_path, capsys):  # == 1 and "run copernicus-ems quality EMSR9 --fetch" in stderr
def test_quality_command_twice_same_file(tmp_path):   # run twice, _quality.json identical
```

(Pairs written with `tests/pairs.py`; check how existing CLI tests pass `--dir` and an injected client in `tests/test_cli.py` and follow it.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_quality.py -k command -v`
Expected: FAIL, argparse `invalid choice: 'quality'`

- [ ] **Step 3: Add the parser and `_quality(ems, args) -> int` to `cli.py`**

- [ ] **Step 4: Run the whole suite**

Run: `make check`
Expected: ruff, mypy, pytest PASS, coverage ≥ 90 %

- [ ] **Step 5: Commit**

```bash
git add src/copernicus_ems/cli.py tests/test_quality.py
git commit -m "feat(cli): copernicus-ems quality"
```

### Task 5: Calibration on the 24 Italian maps

**Files:**
- Create: `notebooks/08_quality_check_s1.ipynb`
- Modify: `src/copernicus_ems/quality.py` (the four calibrated constants), `notebooks/README.md`

**Interfaces:**
- Consumes: `check_pairs`, `Measurements`, `assess`, the constants.

- [ ] **Step 1: Get the data, 0 PU**

```bash
export DATA_ROOT="$PWD/data"
gh run download 42070548998 -R agent-engineering-studio/copernicus-ems -n ems-data -D "$DATA_ROOT/copernicus-ems"
ls "$DATA_ROOT"/copernicus-ems/*/sentinel-1/*_pair.json | wc -l
```
Expected: about 24 manifests, most with `"reference"`. If that run's artifact has no references, use run 41853533004 (notebook 02). If both expired, stop and report: re-fetching costs ~190 PU and needs the owner's go-ahead.

- [ ] **Step 2: Write the notebook**

Cells: load `check_pairs(directory, write=False)`; a table (pandas not required; plain list of dicts printed with f-strings) of every map: product, same image, gap, coverage, mapped km², confirmed share, unmapped clustered km², mapped extent, outside-extent km², verdict, flags. Then a sweep over the four thresholds showing, for each setting, how many assessable maps are "to check" and whether EMSR664 AOI01 Pléiades (the 7.7 km² map, `made_from` Pléiades) is flagged `possibly_partial`. Figure saved to `docs/images/notebook-quality-s1.png` (bars of confirmed share per map, coloured by verdict). Closing markdown "Reading the verdicts" with the limits.

- [ ] **Step 3: Choose the constants**

Rule, from the spec: at most ⌈assessable / 5⌉ maps "to check"; within that, flag the Pléiades map if any setting allows it. If not, tighten `LITTLE_CONFIRMED`, then `UNMAPPED_RATIO`, and if needed leave the suspect unflagged. Write the chosen values in `quality.py` with a comment naming the notebook.

- [ ] **Step 4: Rerun the tests with the new constants**

Run: `make check`
Expected: PASS (adjust only test inputs that sat between the provisional and the calibrated thresholds; never assertions about rules)

- [ ] **Step 5: Record and commit**

Add "Results of notebook 08" to `notebooks/README.md`: the table, the chosen thresholds, the count per verdict, whether the suspect is flagged. Commit notebook (stored without outputs, as the others), figure, README, constants.

```bash
git add notebooks/08_quality_check_s1.ipynb notebooks/README.md docs/images/notebook-quality-s1.png src/copernicus_ems/quality.py
git commit -m "feat(quality): thresholds calibrated on the 24 Italian maps"
```

### Task 6: Documentation

**Files:**
- Create: `docs/quality.md`
- Modify: `zensical.toml` (nav, after the backscatter page), `README.md` ("What this library does" table: a row "Quality check"), `CHANGELOG.md` (Unreleased → Added), `skills/copernicus-ems/SKILL.md` if it exists on this branch (rule 9: "A quality verdict is a screening, not a judgment: 'to check' means look again, never 'wrong'"; otherwise note it for the PR of the skill), `docs/roadmap.md` (the quality check under AI features, done)

- [ ] **Step 1: Write `docs/quality.md`**

Sections: what it answers (plain words first); why a low agreement is normal (POD ~0.3 on independent maps, same-image maps prove nothing); the measurements table (from the spec); the verdict rules and the calibrated thresholds; the command and the Python (`check_pairs`); the table of the 24 maps from Task 5; limits (one known suspect, Italian lowland sample, 40 m only, radar blind under trees, in towns, to shallow or receded water); how Limen uses it (both scores).

- [ ] **Step 2: Build the docs and run the checks**

Run: `uv run --group docs zensical build && make lint`
Expected: build without warnings about the new page; ruff format clean (run `uv run ruff format` if a code block in Markdown is reformatted)

- [ ] **Step 3: Commit**

```bash
git add docs/quality.md zensical.toml README.md CHANGELOG.md docs/roadmap.md skills 2>/dev/null
git commit -m "docs: the quality check"
```
