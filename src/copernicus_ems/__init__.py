"""Client for Copernicus EMS Rapid Mapping."""

from importlib import import_module
from importlib.metadata import version
from typing import Any

from copernicus_ems.catalogue import (
    ACTIVATIONS_URL,
    CATALOGUE_FROM_YEAR,
    DETAIL_URL,
    Activation,
    ActivationsPage,
    Image,
    Layer,
    Product,
    activations_url,
    attribution,
    detail_url,
    parse_activations,
    parse_detail,
    parse_time,
    select_activations,
    wkt_to_geojson,
)
from copernicus_ems.client import (
    DATA_ROOT_FOLDER,
    ENV_DATA_DIR,
    ENV_DATA_ROOT,
    ProductEvents,
    RapidMapping,
    RetryTransport,
    Send,
    SendTransport,
    default_directory,
    fetch_activations,
    fetch_layer,
    fetch_observed_events,
    fetch_products,
    layer_path,
)
from copernicus_ems.errors import EmsError
from copernicus_ems.events import (
    FLOOD_EVENT_TYPE,
    FLOOD_NOTATIONS,
    MASS_MOVEMENT_EVENT_TYPE,
    OBSERVED_EVENT,
    parse_layer,
    select_features,
)
from copernicus_ems.ids import (
    aoi_label,
    delivered_product_ids,
    is_monitoring,
    label_product_id,
    polygon_id,
    polygon_product_id,
    product_id,
)
from copernicus_ems.offline import (
    ACTIVATIONS_FILE,
    DETAIL_FILE,
    InitReport,
    activations_path,
    detail_path,
    init,
    read_activations,
    read_products,
)
from copernicus_ems.scenes import (
    SENTINEL_1_GRD,
    SENTINEL_2_L2A,
    STAC_SEARCH_URL,
    Scene,
    SceneMatch,
    ScenePass,
    fetch_scenes,
    find_scenes,
    parse_scenes,
    passes,
    scene_search_body,
)

__version__ = version("copernicus-ems")

# Names of the descriptions (the geo extra; Claude needs the ai extra), imported on first use
# so that ``import copernicus_ems`` needs neither. ``describe`` itself is not among them: the
# name is the module's (``from copernicus_ems.describe import describe``).
_DESCRIBE = frozenset(
    {
        "PROMPT_VERSION",
        "ClaudeProvider",
        "Description",
        "LocalProvider",
        "Provider",
        "description_path",
        "provider_from_env",
        "read_description",
    }
)


def __getattr__(name: str) -> Any:
    if name in _DESCRIBE:
        return getattr(import_module("copernicus_ems.describe"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ACTIVATIONS_FILE",
    "ACTIVATIONS_URL",
    "CATALOGUE_FROM_YEAR",
    "DATA_ROOT_FOLDER",
    "DETAIL_FILE",
    "DETAIL_URL",
    "ENV_DATA_DIR",
    "ENV_DATA_ROOT",
    "FLOOD_EVENT_TYPE",
    "FLOOD_NOTATIONS",
    "MASS_MOVEMENT_EVENT_TYPE",
    "OBSERVED_EVENT",
    "SENTINEL_1_GRD",
    "SENTINEL_2_L2A",
    "STAC_SEARCH_URL",
    "Activation",
    "ActivationsPage",
    "EmsError",
    "Image",
    "InitReport",
    "Layer",
    "Product",
    "ProductEvents",
    "RapidMapping",
    "RetryTransport",
    "Scene",
    "SceneMatch",
    "ScenePass",
    "Send",
    "SendTransport",
    "__version__",
    "activations_path",
    "activations_url",
    "aoi_label",
    "attribution",
    "default_directory",
    "delivered_product_ids",
    "detail_path",
    "detail_url",
    "fetch_activations",
    "fetch_layer",
    "fetch_observed_events",
    "fetch_products",
    "fetch_scenes",
    "find_scenes",
    "init",
    "is_monitoring",
    "label_product_id",
    "layer_path",
    "parse_activations",
    "parse_detail",
    "parse_layer",
    "parse_scenes",
    "parse_time",
    "passes",
    "polygon_id",
    "polygon_product_id",
    "product_id",
    "read_activations",
    "read_products",
    "scene_search_body",
    "select_activations",
    "select_features",
    "wkt_to_geojson",
]
