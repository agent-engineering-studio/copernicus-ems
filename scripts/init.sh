#!/usr/bin/env bash
# Turn a fresh copy of the template into a named package, then delete itself.
#   ./scripts/init.sh fwi-chain "Canadian Fire Weather Index chain (Van Wagner 1987)"
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <dist-name> <one-line description>" >&2
  exit 2
fi

dist="$1"
module="${dist//-/_}"
description="$2"

cd "$(dirname "$0")/.."
git mv src/package_name "src/$module"
files=$(git ls-files | grep -v -e '^LICENSE$' -e '^scripts/init.sh$')
# shellcheck disable=SC2086
sed -i \
  -e "s/package-name/$dist/g" \
  -e "s/package_name/$module/g" \
  -e "s|PACKAGE_DESCRIPTION|${description//|/\\|}|g" \
  $files
sed -i '/^## Using this template/,/^<!-- end template -->$/d' README.md
git rm -q scripts/init.sh
rm -f uv.lock
uv lock
git add -A
echo "ready: $dist (import $module)"
