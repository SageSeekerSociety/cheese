#!/usr/bin/env bash
# Build the self-hosted connector (cheesehost) for all supported targets.
#
# The connector is the main repo's frozen `cli/` Go thin client, built here as
# `cheesehost` — renamed to avoid colliding with the in-sandbox `cheese`
# platform-action CLI (see docs/convergence-plan.md §3). Zero source change.
#
# CLI_SRC: path to the cli/ source (the main repo cheese-backend-py/cli).
set -euo pipefail
CLI_SRC="${CLI_SRC:?set CLI_SRC to the cheese-backend-py cli/ dir}"
DIST="$(cd "$(dirname "$0")/.." && pwd)/connector-dist"
rm -rf "$DIST"; mkdir -p "$DIST"
cd "$CLI_SRC"
for target in darwin-arm64 darwin-amd64 linux-arm64 linux-amd64; do
  os=${target%-*}; arch=${target#*-}
  GOFLAGS=-mod=mod GOOS=$os GOARCH=$arch \
    go build -ldflags "-X main.version=cheesex-connector" \
    -o "$DIST/$target/cheesehost" .
  echo "built $target/cheesehost"
done
