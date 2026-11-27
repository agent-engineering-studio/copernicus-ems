# Descriptions

## What it does

Every activation has an official English text (`reason`): what happened,
where, how many people were hurt. Until now nothing used it. `describe` reads
it, with the library's numbers, and writes a file for each activation:

- a short description in Italian and English;
- places, rivers, related events and casualties named in the official text,
  each with the exact sentence it comes from.

The demo and Limen read the file. The model is not a chatbot and the demo
never calls it: it runs once per activation, and the result is saved.

```bash
pip install 'copernicus-ems[ai]'
copernicus-ems describe EMSR664          # one activation
copernicus-ems describe --all            # every activation saved by init
copernicus-ems describe EMSR664 --force  # ask the model again
```

It prints the provider, the model and what was dropped. `--json` prints the
file. With `--all` a failure of one activation does not stop the others, and
the exit code is 1 if any call failed. A description that is `null` is a
normal outcome, not a failure.

## The checks

The model writes, then the code checks the answer. The model is never trusted.

- **Quotes.** Every place, river, event and casualty count needs a `quote`,
  and the quote must be in the official text (spaces and case do not
  matter). If it is not, the field is dropped and listed in `dropped`.
- **Numbers and dates.** Every number in the description (digits, numbers
  written out up to twenty, dates as day and month) must be one of the
  numbers the library gave the model, or be in the official text.
- **Names.** A capitalised word in the description must be in the official
  text, in the facts (area names, sensors) or be a generic word (Italia,
  Copernicus, Sentinel-1, month names). Translated place names are rejected
  by design.
- **Words.** The description never judges a map as wrong or in error, and
  never calls radar water "missed". A radar candidate is only a candidate.

If the description fails a check, the model gets its answer back with the
problems and tries once more. If it fails again, the file has
`description: null` and the `reason`; the checked fields are still written.
The result is cached like any other: a rerun does not call the model again.

## Providers

| Variable | What it does | Default |
|---|---|---|
| `COPERNICUS_EMS_LLM` | `claude` or `local`; unset picks Claude if credentials exist, else the local server if its URL is set | unset |
| `ANTHROPIC_API_KEY` | credentials for Claude (the official `anthropic` SDK) | none |
| `COPERNICUS_EMS_CLAUDE_MODEL` | the Claude model | `claude-opus-5-5` |
| `COPERNICUS_EMS_LLM_URL` | an OpenAI-compatible server, ending in `/v1` | none |
| `COPERNICUS_EMS_LLM_MODEL` | the model name sent to that server | the server's |

Claude answers with structured output (a JSON schema) and the server-side
refusal fallback is on; if the fallback model answers, `model` says so.
The local provider calls `/chat/completions` with the same schema as
`response_format`, so a llama.cpp server enforces it. Limen points the URL at
its own LiteLLM gateway and runs no second server. With neither provider the
command says how to get one.

## The local model

For whoever has no key, `compose.yaml` has a service `llm` (profile `ai`):
llama.cpp server with Qwen2.5-3B-Instruct (Q4_K_M) on the CPU.

```bash
make llm                      # start it; the first time it downloads about 2 GB
make describe CODE=EMSR664    # or all activations, without CODE
```

It listens on `127.0.0.1:8780` (`COPERNICUS_EMS_LLM_PORT`) and the model can
be changed with `COPERNICUS_EMS_LLM_HF`. On a CPU, one activation takes tens
of seconds. The `cli` service already has `COPERNICUS_EMS_LLM_URL` pointing at
it.

## Privacy

Only public EMS text (the official `reason`, the bulletins' titles) and the
library's numbers are sent to a model. Nothing else leaves the machine; with
the local model, not even that.

## The file

`<data>/<CODE>/description.json`, written to a `.part` file and renamed.
This is a format contract: Limen reads it. A change of shape is a new version.

| Key | Content |
|---|---|
| `activation` | the code |
| `generated_at` | when it was written (UTC) |
| `provider` | `claude` or `local` |
| `model` | the model that answered |
| `prompt_version`, `inputs_hash` | what the cache compares |
| `source` | the code, the name, the official text, the bulletins' titles and links, the related activations |
| `facts` | the numbers given to the model |
| `description` | `{"it", "en"}`, or `null` |
| `reason` | why the description is `null`, else `null` |
| `places`, `rivers` | `[{name, quote}]` |
| `related_events` | `[{date, what, quote}]` |
| `casualties` | `{deaths, quote}`, or `null` |
| `dropped` | `[{field, value, why}]` |

An example, written by hand (not the output of a model; the numbers and the
text are made up):

```json
{
  "activation": "EMSR000",
  "generated_at": "2026-11-27T10:00:00Z",
  "provider": "local",
  "model": "example-model",
  "prompt_version": 1,
  "inputs_hash": "0000…",
  "source": {
    "code": "EMSR000",
    "name": "Flood in Example",
    "reason": "Heavy rain caused the Example river to flood on 2 May 2023.",
    "bulletins": [],
    "related": []
  },
  "facts": {"example": "the numbers given to the model"},
  "description": {
    "it": "Dopo forti piogge il fiume Example è esondato il 2 maggio 2023.",
    "en": "After heavy rain the Example river flooded on 2 May 2023."
  },
  "reason": null,
  "places": [],
  "rivers": [{"name": "Example", "quote": "the Example river to flood on 2 May 2023"}],
  "related_events": [],
  "casualties": null,
  "dropped": [{"field": "places", "value": "Esempio", "why": "quote not in the official text"}]
}
```

## Limits

- A description is written by a model from the official text. It is not the
  official word of Copernicus or of the Civil Protection, and the demo says so
  under it.
- A 3B model fails the checks more often than Claude: expect more `null`
  descriptions. The fields are kept.
- Place names must appear as the official text writes them. A translated name
  is dropped on purpose.
- It does not geocode places and does not name municipalities: that is
  Limen's job.
