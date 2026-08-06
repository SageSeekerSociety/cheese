#!/usr/bin/env bash
# Build the self-hosted connector (cheesehost) for all supported targets.
#
# The connector is the `cli/` Go thin client (now vendored in THIS repo at the
# root `cli/`), built here as `cheesehost` — renamed to avoid colliding with the
# in-sandbox `cheese` platform-action CLI (see docs/convergence-plan.md §3).
#
# CLI_SRC: path to the cli/ source. Defaults to the in-repo `cli/` (self-contained
# — no external ref/repo needed). Override to build from a different checkout.
set -euo pipefail
DIST="$(cd "$(dirname "$0")/.." && pwd)/connector-dist"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Build from the vendored cli/ by default; override CLI_SRC to point elsewhere.
CLI_SRC="${CLI_SRC:-$REPO_ROOT/cli}"
[ -f "$CLI_SRC/go.mod" ] || { echo "no go.mod under CLI_SRC=$CLI_SRC" >&2; exit 1; }
rm -rf "$DIST"; mkdir -p "$DIST"
cd "$CLI_SRC"
for target in darwin-arm64 darwin-amd64 linux-arm64 linux-amd64; do
  os=${target%-*}; arch=${target#*-}
  GOFLAGS=-mod=mod GOOS=$os GOARCH=$arch \
    go build -buildvcs=false -ldflags "-s -w -X main.version=cheesex-connector" \
    -o "$DIST/$target/cheesehost" .
  echo "built $target/cheesehost"
done
