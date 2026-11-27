# Demo pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the demo into a plain-language page (`/`) that says how far to trust each EMS map and a technical page (`/tecnica`) with the numbers, code and tools; both read the radar results already on disk.

**Architecture:** `demo/api.py` gains a pure reader of `<code>/sentinel-1/*_quality.json|_agreement.json|_agreement.geojson` and two routes; `demo/web` gains a Radar column, radar layers on the map, and a second Next.js route sharing header/footer; a `make radar` target and docs.

**Tech Stack:** Python 3.12, Starlette, shapely, pytest; Next.js (app router), React, Leaflet, Bootstrap Italia, TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-27-demo-pages-design.md`

## Global Constraints

- Radar files are read as they are, never written or changed (Limen's format contract). No `[ml]` import in `demo/api.py`; no PU, no detector run, no network for radar.
- File discovery: `<Store.directory>/<CODE>/sentinel-1/*_quality.json` and `*_agreement.json` (+ `.geojson` of the same stem). Key: the `product` field inside the JSON. Choice per product: `resolution_m` within 0.5 of 40 first, then the most recently modified file.
- Verdict/reason/flags from the quality report; if missing, `quality_verdict` from the agreement report (flags `[]`, reason `null`). `independent`: agreement's `independent`, else `not same_image` from quality.
- Unreadable/malformed file → that product not checked, logged with `log.warning`; never a 500.
- `GET /api/activation/{code}/radar` → `{"checked": int, "products": int, "command": "make radar CODE=<CODE>", "results": {<product>: {...}}}`; `GET /api/activation/{code}/radar/map[?product=P]` → FeatureCollection of classes `confirmed|map_only|candidate` with properties `class`, `km2`, `product`, `independent`, simplified with `SIMPLIFY_DEG`.
- Never "wrong"/"sbagliat*"/"errore"/"error" as a judgment of a map in any string (it, en). Candidate never styled like mapped water. Non-independent map: never the "confirmed" styling or wording. No scores on `/`.
- Every new UI string in both `it` and `en` of `demo/web/lib/i18n.ts`.
- `make check` green before each commit; `cd demo/web && npm run build` green for web tasks.

## Review Focus

- A product whose only result is not at 40 m → shows its "not assessable" reason, not "not checked" — test in Task 1.
- A malformed `_quality.json` next to a good one of another product → the good one still served, status 200 — test in Task 1.
- An activation with no `sentinel-1` folder → `checked: 0`, empty results, empty FeatureCollection — test in Task 1.
- `?product=` naming a product without a layer → empty FeatureCollection, 200 — test in Task 1.
- Same-image map selected on `/` → the legend says the radar cannot confirm it, the layer has no confirmed styling — checked in Task 2's manual check.

---

### Task 1: Radar results in the API

**Files:**
- Modify: `demo/api.py` (reader, two routes, docstring list of URLs)
- Test: `tests/test_demo_api.py`

**Interfaces:**
- Produces: `radar_results(directory: Path, code: str) -> dict[str, dict[str, Any]]` (product → entry with keys `verdict`, `reason`, `flags`, `independent`, `gap_hours`, `km2` (dict or None), `scores` (dict or None), `computed` (bool or None), `files` (list of names), `resolution_m`); `radar_shapes(directory: Path, code: str, product: str | None) -> dict[str, Any]`; routes above.

- [ ] **Step 1: Failing tests.** A helper in the test file writes result files into `saved / "EMSR762" / "sentinel-1"` (copy the shape of the real files: quality keys `activation, product, verdict, reason, flags, gap_hours, same_image, resolution_m`; agreement keys `activation, product, computed, reason, independent, gap_hours, resolution_m, km2, scores, quality_verdict, files`; a tiny GeoJSON with one `confirmed` and one `candidate` polygon inside the AOI). Tests:
  - `test_radar_reads_results_beside_pairs`: `/api/activation/EMSR762/radar` → `checked == 1`, `products == len(card products)`, `command == "make radar CODE=EMSR762"`, the entry for `AOI01_DEL_PRODUCT` has the quality verdict and flags and the agreement km2/scores/independent.
  - `test_radar_prefers_40m_then_newest`: a 20 m and a 40 m file of the same product → the 40 m one; two 40 m → the newer mtime.
  - `test_radar_only_not_40m_shows_its_reason`: only a 20 m quality report with `verdict "not assessable"`, reason `"thresholds calibrated at 40 m"` → that entry, not missing.
  - `test_radar_agreement_without_quality`: only agreement → verdict from `quality_verdict`, flags `[]`.
  - `test_radar_malformed_file_is_skipped`: one product malformed, another good → 200, only the good one.
  - `test_radar_none_on_disk`: no folder → `checked 0`, `results {}`; map → `features []`.
  - `test_radar_map_filter`: map without filter has both products' features with `product` and `independent`; `?product=AOI01_DEL_PRODUCT` only that one; unknown product → empty; coordinates lon/lat.
  - `test_radar_files_untouched`: bytes of the result files equal before and after the calls.
- [ ] **Step 2:** `uv run pytest tests/test_demo_api.py -q` → FAIL.
- [ ] **Step 3: Implement** the reader (json, pathlib; geojson via `shapely.geometry.shape` + `_feature` for simplification) and the routes with `guarded` and `_code`.
- [ ] **Step 4:** tests PASS; `make check` green.
- [ ] **Step 5: Commit** `feat(demo): radar results in the API`.

### Task 2: The plain page says how far to trust each map

**Files:**
- Modify: `demo/web/lib/types.ts`, `demo/web/lib/i18n.ts`, `demo/web/lib/format.ts` (colours), `demo/web/components/Card.tsx`, `demo/web/components/MapView.tsx`, `demo/web/components/Demo.tsx`, `demo/web/app/globals.css`

**Interfaces:**
- Consumes: Task 1 routes.
- Produces: types `RadarEntry`, `RadarData`, `RadarShape`; `t.radar.*` strings; a `phrase(entry, t)` helper (verdict/reason/flag → plain phrase) in `lib/format.ts` or a new `lib/radar.ts`, reused by Task 3 for the plain column of the technical table.

- [ ] **Step 1:** Types for the two answers. Strings it/en: column title "Radar"; phrases for coherent, to check (+ one per flag code `possibly_partial`, `much_unmapped`, `little_confirmed`), not assessable (+ one per reason family: same image, 40 m, gap, coverage, no pass time, no reference, too little water; unknown → generic), not yet checked; legend lines for confirmed, map only, candidate ("acqua possibile, non mappata"), and "non indipendente: il radar non può confermarla"; the paragraph "Quanto fidarsi di una mappa"; link "Per chi sviluppa →". Match reasons by prefix/keyword of the report text (`made from Sentinel-1`, `calibrated at`, `radar pass`, `radar covers`, `no pass time`, `no pre-event`, `too little`).
- [ ] **Step 2:** `Demo.tsx` asks `/radar` with the card and `/radar/map?product=` when a product is selected; passes them to `Card` (column) and `MapView` (layers). Remove the Python block from `Card` and `<Agents>` from the side sheet (they move in Task 3); add the trust paragraph and the link to `/tecnica?code=…`.
- [ ] **Step 3:** `MapView` draws radar layers in a pane above masks, below the flood outline: confirmed solid `WATER`, map_only `WATER` at low fill, candidate orange hatched (SVG pattern or dashed outline + light fill; never solid blue); for `independent: false` draw map_only and confirmed alike (no confirmed styling). Legend in `Demo.tsx` shows radar lines only when a radar layer is on the map.
- [ ] **Step 4:** `npm run build` green; `make check` green; manual check with the 24 calibration pairs copied into `data/copernicus-ems/<code>/sentinel-1/` (EMSR664 AOI01 GRA shows "da guardare" with candidates; a same-image product shows the not-independent legend).
- [ ] **Step 5: Commit** `feat(demo): radar column and layers on the plain page`.

### Task 3: The technical page

**Files:**
- Create: `demo/web/app/tecnica/page.tsx`, `demo/web/components/Technical.tsx`
- Modify: `demo/web/components/Demo.tsx` (extract shared `Header`/`Footer` into `demo/web/components/Frame.tsx` if it stays simple), `demo/web/lib/i18n.ts`, `demo/web/components/Card.tsx` (python moved), `.github/workflows/ci.yml` (also `curl -fs -o /dev/null http://localhost:3000/tecnica`)

**Interfaces:**
- Consumes: Task 1 routes, Task 2 types/strings/`phrase`.

- [ ] **Step 1:** `/tecnica?code=` server page like `app/page.tsx` (same `agents()` probe, moved to a shared lib function). Without code: what the page is, the activations list with a mark where `/radar` has `checked > 0` (ask lazily per row or show the mark only on the open activation — builder's call, no new API).
- [ ] **Step 2:** With code: card numbers + Python (`card.python`); radar table one row per card product: plain phrase, verdict, flags with `numbers`, `gap_hours`, independent, km² confirmed/map_only/candidate/radar_scattered (2 decimals), CSI/POD/FAR/bias (2 decimals, `—` when null), "—" row when not checked; `checked of products` and the command; the Python of `check_pairs` / `agree_pairs`; the file names; links to the docs pages `quality/` and `radar/` on the docs site; the limits paragraph; `<Agents>`. Link "← mappa" to `/?code=`.
- [ ] **Step 3:** CI line; `npm run build`; `make check`.
- [ ] **Step 4: Commit** `feat(demo): technical page`.

### Task 4: `make radar`, docs

**Files:**
- Modify: `Makefile` (target `radar`, in `.PHONY` and help comment), `docs/demo.md`, `demo/README.md`, `README.md` if it describes the demo page, `CHANGELOG.md` (Unreleased), `docs/roadmap.md` (part 5 Done)

- [ ] **Step 1:** `radar:` fails with a message when `CODE` is empty; runs `uv run copernicus-ems quality $(CODE) --fetch` then `uv run copernicus-ems radar $(CODE)` (with `--dir "$${DATA_ROOT:-data}/copernicus-ems"` if the CLI needs it to match the demo's folder — check `default_directory`).
- [ ] **Step 2:** Docs: the two pages; where radar results come from; `make radar CODE=…` needs CDSE credentials and PU; how to copy the calibration pairs at 0 PU; the plain words never judge a map.
- [ ] **Step 3:** `make check`; commit `docs: demo pages and make radar`.
