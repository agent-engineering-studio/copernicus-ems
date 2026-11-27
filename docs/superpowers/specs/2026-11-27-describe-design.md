# Readable, checked descriptions of each activation (step B) — design

Date: 2026-11-27 · Status: draft

The step after parts 1, 4 and 5 of
[2026-11-26-quality-check-design.md](2026-11-26-quality-check-design.md).
The owner's frame: the AI is **not a chatbot**; it improves the data that
copernicus-ems gives, and through it Limen's. copernicus-ems stays a library
Limen uses: no municipalities, no geocoding, no analysis (those are Limen's).

## Intent

Every activation carries an official English text, `reason` (EMSR664: "A
new wave of severe weather has again hit south-eastern areas of the
Emilia-Romagna region… floods already on 2 May 2023, which resulted in three
deaths…"), and the titles and links of the information bulletins. Nobody
uses them: the demo shows numbers and fixed phrases, Limen gets nothing.

A language model, run **once per activation** and saved, turns them into:

1. **a short description** in Italian and English of what happened, where,
   when, how much water was seen and how far the radar backs the maps —
   built on the library's numbers, never on numbers of its own;
2. **structured fields** read from the official text, each with the exact
   sentence it comes from: places and rivers named, related events with
   their date, casualties when the text gives them.

The demo and Limen read the result as a file, like the radar results.

## Approaches (owner's choice, 2026-11-27: the first)

1. **Claude, or any local OpenAI-compatible server** (chosen). Claude through
   the official `anthropic` SDK when credentials exist; otherwise the URL in
   `COPERNICUS_EMS_LLM_URL` (any `/v1/chat/completions` server). For whoever
   has neither, a `llm` service in `compose.yaml` (profile `ai`) runs
   **llama.cpp server** with a ~3B instruct model on the CPU. Limen points
   the URL at its LiteLLM gateway and runs no second server.
2. Ollama instead of llama.cpp — set aside: another engine than Limen's, and
   looser control of the JSON output.
3. The model in-process (llama-cpp-python) — set aside: a heavy compiled
   dependency, slow on the owner's Intel CPU, not shareable with Limen.

## Goals

### Library: `copernicus_ems.describe` (new `[ai]` extra: `anthropic`)

- `describe(code, directory=None, *, force=False) -> Description`: reads the
  activation and its products from disk (`init` data), the library's numbers
  (`geo.summary` per product: km² observed and of water, sensors, dates) and,
  when present, the radar results (verdict per product), builds the input,
  calls the model, **checks** the answer, writes
  `<directory>/<CODE>/description.json`, returns it.
- **Checks, in code, after the model answers:**
  - every extracted field carries a `quote`; the quote must occur in the
    official text (whitespace and case normalised). A field whose quote is
    not found is **dropped** and listed in `dropped` with the reason;
  - every number in the description must be one of the numbers given to the
    model (km², dates, counts; a tolerance for rounding to the displayed
    precision) or appear in the official text. A description with any other
    number is rejected; the model is asked once more; if it fails again, the
    file has no description and says why (`description: null`, `reason`);
  - the description never uses words that judge a map as wrong or in error,
    never calls a radar candidate water that was missed (checked by a small
    word list in it and en; a hit is a rejection as above).
- **Provider choice:** `COPERNICUS_EMS_LLM` = `claude` | `local` | unset.
  Unset: Claude if the `anthropic` SDK finds credentials, else local if
  `COPERNICUS_EMS_LLM_URL` is set, else an `EmsError` saying how to get one
  (`docker compose --profile ai up -d llm`, or an API key).
  - Claude: model `claude-opus-5-5` by default (`COPERNICUS_EMS_CLAUDE_MODEL`
    overrides), structured output (`output_config.format` with the JSON
    schema), server-side refusal fallback on, `stop_reason` checked.
  - Local: `COPERNICUS_EMS_LLM_URL` + `COPERNICUS_EMS_LLM_MODEL`, request
    with `response_format` = the same JSON schema (llama.cpp enforces it),
    through httpx (already a dependency); long timeout (CPU: tens of seconds).
- **Cached:** rewritten only when its inputs change (a hash of the official
  text, the numbers, the radar verdicts and a prompt version) or with
  `force`. Written atomically (`.part`, then rename).
- Only public EMS text and the library's numbers are sent to the model.

### The file `description.json` (a format contract for Limen)

`activation`, `generated_at`, `provider` (`claude` | `local`), `model`,
`prompt_version`, `inputs_hash`, `source` (the official `reason` text, the
bulletins' titles and URLs), `facts` (the numbers given to the model),
`description` (`{"it": …, "en": …}` or `null`) and `reason` when null,
`places` / `rivers` (`[{name, quote}]`), `related_events`
(`[{date, what, quote}]`, date ISO when the text gives a day),
`casualties` (`{deaths, quote}` or `null`), `dropped`
(`[{field, value, why}]`). Documented in the docs with an example; a change
of shape is a new version and a message to limen-v3.

### CLI, compose, Make

- `copernicus-ems describe CODE [--force] [--json]` and
  `copernicus-ems describe --all` (every activation saved by `init`); prints
  the provider, the model, and what was dropped.
- `compose.yaml`: service `llm`, profile `ai`, image
  `ghcr.io/ggml-org/llama.cpp:server`, a ~3B instruct GGUF downloaded on
  first start into a named volume, bound to 127.0.0.1; the `cli` and `api`
  services get `COPERNICUS_EMS_LLM_URL` pointing at it when the profile runs.
- `make describe CODE=…` (or all), `make llm` to start the local model.

### Demo (reads the file, never calls a model)

- Plain page: in the card, the description in the page's language, with
  one line under it: "scritto da un modello linguistico (Claude / un modello
  locale) a partire dal testo ufficiale e dai numeri della libreria". No
  file: nothing shown.
- Technical page: the official text, the extracted fields with their quotes,
  what was dropped and why, provider, model, date, and the command.
- API: `GET /api/activation/{code}/description`, read from disk like
  `/radar`, never a 500 on a malformed file.

## Non-goals

- No chat, no question box, no model call from the demo or the MCP server.
- No municipalities, no geocoding of the places named (Limen's job), no
  translation of other fields, no summary of the bulletins' web pages (only
  their titles and links).
- No change to EMS data or to the radar files.
- No MCP tool in this step (may follow once the file proves useful).

## Anti-goals

- A number, place, date or casualty that is not in the inputs.
- A description that judges a map, or presents radar-only water as missed
  flooding.
- Text that reads as the official word of Copernicus or of the Civil
  Protection: the line under it always says a model wrote it.
- Tests or CI that call a real model or the network.

## Constraints

- Tests: providers faked (httpx `MockTransport` for local, an injected fake
  for Claude); the checks tested on hand-made answers (a bad quote, an
  invented number, a forbidden word, a valid answer).
- `[ai]` is optional: the base library and the other extras do not import
  `anthropic`.
- `make check` green; docs page "Descriptions" in the nav; README row,
  CHANGELOG, skill rule ("a description is model-written from the official
  text; quote the fields with their quote").

## Settled after the builder check (our calls)

- **Names in the description are checked too.** Every capitalised word not
  at the start of a sentence must appear in the official text, in the facts
  (AOI names, sensors) or in a short allow-list (Italia/Italy, Copernicus,
  Sentinel-1, month names…). Otherwise the description is rejected like an
  invented number.
- **What counts as a number:** digits; numbers written out from zero to
  twenty (it and en); dates written as day + month name (it and en). Each
  must match the facts or the official text.
- **The retry** sends the model its previous answer and the list of problems
  found (the numbers, names or words, and why). One retry only.
- **Fields and description are independent.** If the description is
  rejected twice, the checked fields are still written; `description` is
  `null` with `reason`. A null result is cached like any other (same inputs
  hash): a rerun does not call the model again unless `--force`.
- **`describe --all`** goes on after a failure of one activation, prints a
  line per activation and a summary, and exits 1 if any call failed (the
  provider unreachable, a refusal, a malformed answer). A null description
  is a normal outcome (exit 0).
- **Facts are aggregated per area of interest**, not per product: the most
  water seen and the area observed, first and last map date, sensors, and
  how many maps the radar confirms, marks to look at or cannot assess. This
  keeps the input small for a 3B model; these are the allowed numbers.
  `relatedevents` codes from `detail.json` go in `source` as given.
- **Compose:** the `cli` service always has
  `COPERNICUS_EMS_LLM_URL=http://llm:8080/v1`; without `llm` running,
  `describe` fails with a clear message. `api` gets nothing: the demo never
  calls a model.
- **Claude API shape** checked against the current SDK reference: model
  `claude-opus-5-5`, `output_config.format` with a JSON schema,
  `fallbacks: "default"` with the `server-side-fallback-2026-07-01` beta,
  `stop_reason` checked for `refusal`. Fourteen activations cost cents.

## Left to the builder

The prompt's wording, the local model (Qwen2.5-3B-Instruct or
Llama-3.2-3B-Instruct, Q4 GGUF — whichever passes the checks more often on
the 14 Italian activations), the port of `llm`, the number tolerance, the
exact forbidden-word list, the card layout.
