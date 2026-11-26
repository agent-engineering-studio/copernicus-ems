#!/bin/sh
# Download once what the demo and the library need to work offline: the
# activation list, the detail of each activation and its observed-event layers
# (copernicus-ems init), into $DATA_ROOT/copernicus-ems.
#
#   scripts/fetch-data.sh
#
#   DATA_ROOT               the root shared with Limen and the other libraries
#                           (default: ./data); the files go to $DATA_ROOT/copernicus-ems
#   COPERNICUS_EMS_COUNTRY  which country (default: Italy, about 200 MB)
#   COPERNICUS_EMS_RUN      what to run Python with (default: uv; empty inside Docker)
#
# Safe to run again: a layer already there is not downloaded twice. A run
# that fails is a warning, not an error: the demo then reads live.
set -u
cd "$(dirname "$0")/.."

ROOT="${DATA_ROOT:-$PWD/data}"
COUNTRY="${COPERNICUS_EMS_COUNTRY:-Italy}"
RUN="${COPERNICUS_EMS_RUN-uv run --all-extras --group demo}"

mkdir -p "$ROOT/copernicus-ems"
echo "The floods of $COUNTRY, into $ROOT/copernicus-ems"
DATA_ROOT="$ROOT" $RUN copernicus-ems init --country "$COUNTRY" ||
    echo "warning: something was not downloaded; run this script again later" >&2
echo "Done."
