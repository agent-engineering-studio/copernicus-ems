"""Every flood Copernicus EMS mapped in Italy, as one GeoJSON file.

    uv run python examples/italy_floods.py            # -> italy_floods.geojson
    uv run python examples/italy_floods.py Spain out.geojson

Open the result in QGIS, or drop it on https://geojson.io. Each polygon
carries the activation and the product it comes from, and when the
satellite saw it. The layers are cached (see ``default_directory``): run it
again and only the activation list and details are asked.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from copernicus_ems import RapidMapping


async def main(country: str, out: Path) -> None:
    features = []
    async with RapidMapping() as ems:
        # Many floods are filed as "Storm": the features are filtered on 5-Flood anyway.
        activations = await ems.activations(country=country, categories=["Flood", "Storm"])
        for activation in activations:
            events = await ems.observed_events(activation.code)
            count = sum(len(e.features) for e in events)
            print(
                f"{activation.code} {activation.event_time:%Y-%m-%d} {count:6d}  {activation.name}"
            )
            for event in events:
                observed = event.product.observed_time
                for feature in event.features:
                    feature["properties"] |= {
                        "activation": activation.code,
                        "product": event.product.label,
                        "observed_time": observed.isoformat() if observed else None,
                    }
                    features.append(feature)
        print(
            f"{len(features)} polygons from {len(activations)} activations; cache: {ems.directory}"
        )
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    print("written", out)


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1] if len(sys.argv) > 1 else "Italy",
            Path(sys.argv[2] if len(sys.argv) > 2 else "italy_floods.geojson"),
        )
    )
