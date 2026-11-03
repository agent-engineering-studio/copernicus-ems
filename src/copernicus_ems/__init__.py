"""Client for Copernicus EMS Rapid Mapping."""

from importlib.metadata import version

from copernicus_ems.catalogue import (
    ACTIVATIONS_URL,
    DETAIL_URL,
    Activation,
    ActivationsPage,
    Layer,
    Product,
    activations_url,
    detail_url,
    parse_activations,
    parse_detail,
    parse_time,
    select_activations,
)
from copernicus_ems.client import (
    ProductEvents,
    fetch_activations,
    fetch_layer,
    fetch_observed_events,
    fetch_products,
)
from copernicus_ems.errors import EmsError
from copernicus_ems.events import (
    FLOOD_EVENT_TYPE,
    FLOOD_NOTATIONS,
    OBSERVED_EVENT,
    parse_layer,
    select_features,
)

__version__ = version("copernicus-ems")

__all__ = [
    "ACTIVATIONS_URL",
    "DETAIL_URL",
    "FLOOD_EVENT_TYPE",
    "FLOOD_NOTATIONS",
    "OBSERVED_EVENT",
    "Activation",
    "ActivationsPage",
    "EmsError",
    "Layer",
    "Product",
    "ProductEvents",
    "__version__",
    "activations_url",
    "detail_url",
    "fetch_activations",
    "fetch_layer",
    "fetch_observed_events",
    "fetch_products",
    "parse_activations",
    "parse_detail",
    "parse_layer",
    "parse_time",
    "select_activations",
    "select_features",
]
