# Water over one place

A municipality asks one question: **how much of our territory was seen under
water, and when?** `copernicus_ems.places` (`[geo]` extra) answers it for any
polygon. The boundary is yours: the library knows no place by name.

```python
import json

from copernicus_ems.places import water_over_place

place = json.load(open("faenza.geojson"))  # your boundary, lon/lat
for a in water_over_place(place):  # the copy saved by `copernicus-ems init`
    print(
        a.activation_code,
        f"{a.water_km2:.2f} km² of water,",
        f"{a.observed_share:.0%} of the place observed",
    )
```

On the floods of May 2023 and the boundary of Faenza (215.7 km²) it prints:

```text
EMSR664 12.26 km² of water, 100% of the place observed
EMSR659 0.40 km² of water, 7% of the place observed
```

## Three answers, never two

The two lines do not mean "much water" and "little water". For one place an
activation says three different things, and `PlaceWater` keeps them apart:

- **observed** (`observed_km2`, `observed_share`): the part of the place
  inside the observation mask of a product. Only there "no water" means
  *seen dry*;
- **water** (`water_km2`): the part of the place inside an observed flood
  polygon;
- **not observed**: the rest. Nobody looked there.

On 2 May 2023 (EMSR659) the satellite looked at 14.6 km² of Faenza and found
0.40 km² of water. Of the other 201 km² nothing is known. Two weeks later
(EMSR664) the whole municipality was inside the masks: 12.26 km² of water,
5.7 % of the territory.

An activation whose masks do not touch the place is **not in the list**. An
empty list means "no saved activation looked here". It never means "no water".

## What is counted

- The water is the **union** of the first issues (`DEL_PRODUCT`,
  `GRA_PRODUCT`, `FEP_PRODUCT`): two maps that draw the same field count it
  once.
- Monitoring products are later views of the same place. They are listed in
  `later_products` and **not added**: for EMSR664 over Faenza there are three.
- Areas are geodesic, in km², so they do not depend on a projection.
- `verdicts` carries the [radar screening](quality.md) of each product used,
  when its report is saved: how far the radar backs that map.

## How far to trust a figure

- **A lower bound.** Short urban floods and water under canopy may be
  missing from a satellite map.
- **Only where someone looked.** Read `observed_share` before `water_km2`.
- **A screening, when there is one.** A verdict "to check" means "look at
  this map again", never "this map is wrong".
- **Observed afterwards.** It is not an alert, not a forecast and not a
  hazard level. Quote `a.attribution` with every figure.

## One activation, live

`place_water(place, activation, events)` is the pure function behind it: give
it the products of one activation (`RapidMapping.observed_events`) and it
needs no saved copy. The MCP tool [`water_over_place`](mcp.md) does this for
an agent.

## In the demo

The plain page of the [demo](demo.md) has one panel made of this function:
"the water observed over our municipality". Set the boundary and the name:

```bash
COPERNICUS_EMS_PLACE_GEOJSON=./my-municipality.geojson \
COPERNICUS_EMS_PLACE="Comune di …" make docker
```

Without them it shows the example boundary, Faenza (© OpenStreetMap
contributors). The panel shows the water and how much of the place was
observed, and nothing else: no hazard layers, no risk, no alert. That is the
job of [Limen](https://github.com/agent-engineering-studio/limen).
