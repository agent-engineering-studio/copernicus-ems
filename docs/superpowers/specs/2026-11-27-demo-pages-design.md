# The demo in two pages: plain words and technical — design

Date: 2026-11-27 · Status: draft

Part 5 of the programme in
[2026-11-26-quality-check-design.md](2026-11-26-quality-check-design.md).
Parts 2 and 3 moved to Limen; **the demo shows only the EMS data itself**
(the maps, their masks, and how far the radar backs each map). No PAI, no
municipalities, no risk.

## Intent

The quality check (part 1) and the radar agreement (part 4) exist as files
beside each Sentinel-1 pair, but nobody sees them. The quality spec
promised the demo would say, in plain words, "the radar confirms this map"
or "this map may be partial". Today the demo is one page that mixes the
plain story (three steps, the card) with the technical parts (the Python,
the MCP tools). Split it:

- **The plain page** (`/`), for anyone: where the water was, where the
  satellite looked, and **how far to trust each map**, in words, with no
  scores and no code.
- **The technical page** (`/tecnica`), for those who build on it (Limen,
  researchers, agents): the numbers behind every word, the scores, the
  files, the Python and CLI that produce them, the MCP tools.

## Where the radar results come from (owner's choice, 2026-11-27)

**From disk, when present.** The API reads the files that
`copernicus-ems quality` and `copernicus-ems radar` wrote under
`<data directory>/<code>/sentinel-1/` (`*_quality.json`,
`*_agreement.json`, `*_agreement.geojson`). Where a product has none, the
page says "not yet checked with the radar". No Italian results are
committed or published; making them needs CDSE credentials and PU, so a new
`make radar CODE=…` target runs `quality CODE --fetch` then `radar CODE`
for whoever has them. Approaches set aside: publishing the 24 results with
the `init` data (an archive to maintain), and a static table on the
technical page only (no radar on the map).

These files are also **Limen's format contract** (it reads them as such,
pinned `~=0.9.0`): the demo reads them as they are and changes nothing in
them.

## Goals

### API (`demo/api.py`)

- `GET /api/activation/{code}/radar`: one entry per product that has
  results, matched to the card's product rows by `product` label
  (`AOI01_GRA_PRODUCT`); when a product has pairs at several resolutions,
  the 40 m one (as the CLI). Each entry: verdict, reason, flags (code,
  text, numbers), `independent` (from the agreement report, or
  `not same_image` from the quality report), `gap_hours`, km² per class,
  scores, file names. Plus `checked: n of m products` and the command that
  makes the missing ones. Read from disk on every call (small files; they
  change when someone runs the commands), not part of the cached `Built`.
- `GET /api/activation/{code}/radar/map`: the agreement polygons of classes
  `confirmed`, `map_only`, `candidate` for every product with results,
  tagged with `product` and `independent`, simplified like the EMS shapes.
- A product with a quality report and no agreement (or the reverse) shows
  what exists. Unreadable or malformed files: the product counts as not
  checked, logged, never a 500.
- No dependency on the `[ml]` extra: plain JSON and GeoJSON.

### The plain page (`/`)

- Map, list and card as today; the Python block and «Per gli agenti AI»
  move to the technical page; a link "Per chi sviluppa →" carries `?code=`.
- In the card's product table, a column **Radar** with one short phrase per
  product:
  - coherent → "il radar conferma" / "the radar agrees";
  - to check → "da guardare" / "worth a look", with the flag's reason
    rewritten in plain words (e.g. possibly partial → "forse la mappa copre
    solo una parte dell'acqua");
  - not assessable → "non valutabile", with the reason in plain words;
    same image → "mappa fatta con la stessa immagine radar: non si può
    usare per confermarla";
  - nothing on disk → "non ancora controllata".
- On the map, when one product is selected and has an agreement layer:
  `confirmed` (the water blue, solid), `map_only` (the water blue, lighter),
  `candidate` (hatched orange: "acqua possibile, non mappata"). The legend
  explains each in one line. For a non-independent map, no `confirmed`
  styling: the legend says the radar cannot confirm it.
- One paragraph "Quanto fidarsi di una mappa" in the side sheet: the radar
  sees only part of the water (not under trees, in towns, or after it
  recedes), so "da guardare" only means a person should look at the map: it
  is not a judgment on it.

### The technical page (`/tecnica`)

Same header, language switch and activation choice (`?code=`); no map
needed beyond what the plain page has (a link back "← mappa"). Per
activation:

- The card numbers and the Python that gives them (moved from the plain
  page).
- **Radar check table**, one row per product: verdict, flags with their
  numbers, `gap_hours`, independent, km² confirmed / map only / candidate /
  scattered, CSI, POD, FAR, bias; "—" where not checked.
- The commands: `make radar CODE=…`, or `copernicus-ems quality CODE
  --fetch` and `copernicus-ems radar CODE`; the Python of `check_pairs` and
  `agree_pairs`; the three file names per product and a link to
  `docs/radar.md` and `docs/quality.md`.
- The limits, stated as in the docs: a screening, POD mostly under 0.2 on
  independent maps, same-image maps prove nothing, candidates never ground
  truth.
- «Per gli agenti AI» (moved as is).
- With no activation chosen: what the page is, the list of activations with
  a mark on those that have radar results.

## Non-goals

- No new analysis, no PAI, no municipalities, no Limen data.
- No fetching or computing from the demo: it never spends PU and never runs
  the detector; it reads what the commands wrote.
- No MCP tool for the radar results (may follow).
- No LLM text (the later part B).
- No change to the library's file formats.

## Anti-goals

- Any word that reads as "this map is wrong" or "error" (it, en).
- A candidate shown as water that was missed, or with the same look as
  mapped water.
- A same-image map shown as confirmed.
- Scores on the plain page.

## Constraints

- Italian and English for every new string (`lib/i18n.ts`), Bootstrap
  Italia look, accessible (contrast, keyboard, legend in text).
- Tests in `tests/test_demo_api.py` with result files written to a tmp
  directory; CI's `web` job (build and the page answers) stays green and
  also asks `/tecnica`.
- `make check` green.

## Settled after the builder check (our calls)

- **Join and tie-break.** Results are keyed by the `product` label inside
  the files (`AOI01_GRA_PRODUCT`, the card's `product`). Among a product's
  files, the 40 m pair wins; among several 40 m pairs (another layer
  version), the most recently written. A product with only non-40 m results
  shows the report's own "not assessable" reason.
- **Verdict source.** `verdict`, `reason` and `flags` from `*_quality.json`;
  if it is missing, `quality_verdict` (no flags) and `independent` from
  `*_agreement.json`. When the agreement is `computed: false`, the map shows
  no radar layer and the reason is the agreement's.
- **The words.** The flag codes are `possibly_partial`, `much_unmapped`,
  `little_confirmed`; the reasons of "not assessable" are those of
  `quality._not_assessable` (same image, gap, coverage, no pass time, no
  reference, too little water, 40 m). Each gets one plain phrase in it/en;
  an unknown code falls back to the report's own text on the technical
  page and to "non valutabile" on the plain page.
- **Map loading.** `/radar/map` takes an optional `?product=`; the plain
  page asks for it only when a product is selected in the card (the
  existing selection), so a large activation never loads every layer.
- **Where.** The files are read from the demo's data directory, the same
  `Store.directory` (`$DATA_ROOT/copernicus-ems`), under
  `<code>/sentinel-1/`, where `pairs`/`quality --fetch` write by default.
  To see results locally at 0 PU, the 24 calibration pairs can be copied
  from `data/calibration/copernicus-ems/<code>/sentinel-1/` into it; the
  docs say how.
- **`make radar`** needs `CODE`; without CDSE credentials the CLI's own
  error stops it, non-zero.
- **One endpoint** serves both pages.

## Left to the builder

The exact Italian and English wording (within the anti-goals), colours and
hatch of the radar classes, whether the technical page reuses `Card` or has
its own table component, the Makefile target's argument handling.
