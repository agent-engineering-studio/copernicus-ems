"""Write ``tests/fixtures/limen_parity.json``: what Limen's code returns on the fixed inputs.

The functions of ``copernicus_ems.ids``, ``truth``, ``reports`` and ``overlay``
were moved from Limen. This script runs **Limen's** implementation on the
inputs of ``tests/parity_inputs.py``; ``tests/test_limen_parity.py`` gives the
same inputs to this library and compares. Run it in Limen's environment, from
this repository, only while Limen still has that code:

    /path/to/limen/.venv/bin/python scripts/limen_parity.py /path/to/limen

It reads Limen and writes one file here; no network, no database.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))

import parity_inputs as inputs
from limen.core import ems_pai, ems_registro
from limen.integrations.copernicus_ems import client as limen_client
from limen.integrations.copernicus_ems import verdetti

from copernicus_ems import OBSERVED_EVENT, layer_path, parse_detail


def _ids() -> dict[str, Any]:
    return {
        "labels": [verdetti.mask_id(code, label) for code, label in inputs.LABELS],
        "aoi_labels": [verdetti.aoi_label(label) for _, label in inputs.LABELS],
        "polygon_products": [ems_pai.prodotto(p) for p in inputs.POLYGON_IDS],
        "monitoring": [ems_pai.di_monitoraggio(p) for p in inputs.PRODUCT_IDS],
        "attribution": ems_registro.attribuzione(2023),
        "catalogue_from_year": limen_client.CATALOGUE_FROM_YEAR,
    }


async def _truth(scenario: str) -> dict[str, Any]:
    async def fetch(method: str, url: str, **_: Any) -> httpx.Response:
        if scenario == "detail-down":
            raise httpx.ConnectError("no route")
        status, body = inputs.respond(url, scenario)
        return httpx.Response(status, content=body, request=httpx.Request(method, url))

    async def shared() -> None:
        return None

    limen_client.fetch_with_retry = fetch  # type: ignore[assignment]
    limen_client.SharedHttpClient.get = shared  # type: ignore[method-assign,assignment]
    with tempfile.TemporaryDirectory() as tmp:
        if scenario == "corrupt-cache":
            product = parse_detail(inputs.DETAIL.read_bytes())[0]
            inputs.damage_cache(layer_path(product, product.layers[OBSERVED_EVENT], Path(tmp)))
        truth = await limen_client.fetch_activation_truth(inputs.activation(), cache_dir=Path(tmp))
    return {
        "floods": [
            {
                "id": f.id,
                "area_km2": f.area_km2,
                # The whole record once is enough: the other scenario reads the same layer.
                **(
                    {
                        "activation_code": f.activation_code,
                        "aoi_label": f.aoi_label,
                        "event_time": inputs.iso(f.event_time),
                        "observed_time": inputs.iso(f.observed_time),
                        "event_type": f.event_type,
                        "detection_method": f.detection_method,
                        "wkt": f.geom.wkt,
                        "attributes": f.attributes,
                    }
                    if scenario == "layers"
                    else {}
                ),
            }
            for f in truth.floods
        ],
        "masks": [
            {
                "id": m.id,
                "activation_code": m.activation_code,
                "aoi_label": m.aoi_label,
                "product_type": m.product_type,
                "observed_time": inputs.iso(m.observed_time),
                "wkt": m.geom.wkt,
                "area_km2": m.area_km2,
            }
            for m in truth.masks
        ],
        "absent_masks": truth.absent_masks,
        "read_products": truth.prodotti_letti,
    }


async def _delivered() -> list[str]:
    async def fetch(method: str, url: str, **_: Any) -> httpx.Response:
        _status, body = inputs.respond(url, "layers")
        return httpx.Response(200, content=body, request=httpx.Request(method, url))

    limen_client.fetch_with_retry = fetch  # type: ignore[assignment]
    _, delivered = await limen_client._fetch_detail(inputs.CODE)
    return sorted(delivered)


def _reports() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        inputs.write_reports(Path(tmp))
        found = verdetti.raccogli(Path(tmp))
    return [
        {
            "activation_code": r.activation_code,
            "product": r.product,
            "product_id": r.mask_id,
            "stem": r.stem,
            "verdict": r.verdict,
            "independent": r.independent,
            "quality": None
            if r.qualita is None
            else {**asdict(r.qualita), "flag_codes": list(r.qualita.flag_codes)},
            "agreement": None if r.accordo is None else asdict(r.accordo),
            "classes": {
                name: {"name": c.classe, "area_km2": c.area_km2, "wkt": c.geom.wkt}
                for name, c in r.classi.items()
            },
        }
        for r in found
    ]


def _places() -> list[dict[str, Any]]:
    water = [
        ems_registro.Poligono(
            prodotto=p["product"], geom=p["geom"], inizio=p["start"], osservato=p["observed"]
        )
        for p in inputs.water_polygons()
    ]
    comuni = [
        ems_registro.Comune(istat_code=code, nome=f"Comune {code}", aoi_id="aoi", geom=geom)
        for code, geom in inputs.places()
    ]
    return [
        {
            "key": r.istat_code,
            "water_km2": r.acqua_km2,
            "start": inputs.iso(r.inizio),
            "observed_from": inputs.iso(r.osservato_dal),
            "observed_to": inputs.iso(r.osservato_al),
            "products": list(r.prodotti),
        }
        for r in ems_registro.comuni_colpiti(water, comuni)
    ]


def _split() -> list[dict[str, Any]]:
    assert ems_pai.CLASSI == inputs.CLASSES
    names = {
        "tolerance_m": "tolleranza_m",
        "min_patch_m2": "patch_minima_m2",
        "tile_m": "lato_tessera_m",
    }
    out = []
    for case in inputs.SPLIT_CASES:
        r = ems_pai.ripartisci(
            inputs.split_water(), inputs.zones(), **{names[k]: v for k, v in case.items()}
        )
        out.append(
            {
                "water_m2": r.acqua_m2,
                "by_class_m2": r.per_classe_m2,
                "outside_m2": r.fuori_m2,
                "outside_filtered_m2": r.fuori_filtrato_m2,
                "outside_patches": r.macchie_fuori,
                "share_outside": r.quota(r.fuori_m2),
            }
        )
    empty = ems_pai.ripartisci([], inputs.zones())
    out.append(
        {
            "water_m2": empty.acqua_m2,
            "by_class_m2": empty.per_classe_m2,
            "outside_m2": empty.fuori_m2,
            "outside_filtered_m2": empty.fuori_filtrato_m2,
            "outside_patches": empty.macchie_fuori,
            "share_outside": empty.quota(empty.fuori_m2),
        }
    )
    return out


def main() -> None:
    limen = Path(sys.argv[1])
    commit = subprocess.run(
        ["git", "-C", str(limen), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    golden = {
        "limen_commit": commit,
        "ids": _ids(),
        "truth": {
            scenario: asyncio.run(_truth(scenario))
            for scenario in ("layers", "corrupt-cache", "detail-down")
        },
        "delivered": asyncio.run(_delivered()),
        "reports": _reports(),
        "places": _places(),
        "split": _split(),
    }
    inputs.GOLDEN.write_text(json.dumps(golden, sort_keys=True) + "\n")
    print(f"{inputs.GOLDEN}: Limen {commit}")


if __name__ == "__main__":
    main()
