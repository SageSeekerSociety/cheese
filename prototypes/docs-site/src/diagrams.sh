#!/usr/bin/env bash
# Renders diagrams/<slug>.<type>.json into diagrams/<slug>.html with archify.
# archify is not on npm (the npm package named "archify" is an unrelated project),
# so the pinned release is cloned from GitHub into node_modules/.
set -euo pipefail
cd "$(dirname "$0")/.."
tag=v2.16.0
dir="node_modules/.archify-$tag"
[ -d "$dir" ] || git clone -q --depth 1 --branch "$tag" https://github.com/tt-a1i/archify "$dir"
cli="$dir/archify/bin/archify.mjs"
for spec in diagrams/*.json; do
  base=$(basename "$spec" .json)
  type=${base##*.}
  slug=${base%.*}
  node "$cli" deliver "$type" "$spec" "diagrams/$slug.html" --quality showcase --json >"/tmp/archify-$slug.receipt.json" \
    || { cat "/tmp/archify-$slug.receipt.json"; exit 1; }
  echo "diagrams/$slug.html ($type)"
done
