# Benchmark: known cases

Two small, fixed sets where the right answer is written down. Both run in CI
(job `benchmark`) and with `make bench`. Neither needs the network, an
account or a language model.

## 1. The 24 maps and their verdict

```bash
uv run python -m benchmarks.run --check
```

`cases.json` holds the 24 Italian flood maps of notebook 08: for each, what
the Sentinel-1 radar measured inside the observation mask, and the verdict
the [quality check](../docs/quality.md) gave when its thresholds were
calibrated. A change of threshold that moves one verdict fails here.

| Method | As expected | What it says |
|---|---|---|
| `accept-all`: trust every map | 13 of 24 | 24 coherent |
| `quality`: `copernicus_ems.quality.assess` | 24 of 24 | 13 coherent, 3 to check, 8 not assessable |

The 11 maps `accept-all` gets wrong are the reason for the check: 8 maps were
made from Sentinel-1 itself, so the radar agreeing with them proves nothing,
and 3 deserve a second look.

**What this set does not prove.** One map is a known suspect (EMSR664
`AOI01_GRA_PRODUCT`: mapped water spans 1.3 % of the area, the radar sees 4.5
times as much outside it). The thresholds were fitted so that it is flagged.
The other 23 cases only say the check has not changed. One country, one
detector, 16 assessable maps.

## 2. What the checks of `describe` catch

```bash
uv run python -m benchmarks.faithful --check
```

The rule of every AI feature here: **the numbers come from the data; a model
may explain them, never invent them.** `copernicus-ems describe` lets a model
write a description; `copernicus_ems.describe_checks.check` refuses a number,
a date or a name that is in neither the library's facts nor the official text.

`faithful.json` is the real official text and the real facts of EMSR664, and
15 answers written by hand: 2 faithful, 13 with one planted fault each.

| Fault | Answers | Caught |
|---|---|---|
| invented number (a figure, a wrong rounding, a death toll) | 3 | 3 |
| invented date | 1 | 1 |
| unknown place name | 1 | 1 |
| a map called wrong, possible water called missed | 2 | 2 |
| a field whose quote is not in the text, or does not say it | 4 | 4 |
| **two real figures swapped between places** | 2 | **0** |

11 of 13 faults caught, 2 of 2 faithful answers kept.

**The blind spot.** The check knows which numbers exist. It does not know
which place a number is said of: "135.3 km² of water in Faenza" passes,
because 135.3 is a real figure (of Lugo). The set records this on purpose, so
the limit stays visible. Closing it needs a check that ties each number to
its area: see the roadmap.
