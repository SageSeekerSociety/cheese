#!/usr/bin/env bash
# Build the self-hosted connector (cheesehost) for all supported targets.
#
# The connector is the main repo's frozen `cli/` Go thin client, built here as
# `cheesehost` — renamed to avoid colliding with the in-sandbox `cheese`
# platform-action CLI (see docs/convergence-plan.md §3). Zero source change.
#
# CLI_SRC: path to the cli/ source (the main repo cheese-backend-py/cli). If
# unset, this auto-extracts it from git: the frozen cli/ is NOT on any working
# branch — it lives at ref CLI_REF below (branch origin/design/cheese-agent-layer).
set -euo pipefail
DIST="$(cd "$(dirname "$0")/.." && pwd)/connector-dist"

# Where the main repo lives (override with MAIN_REPO); and the ref holding cli/.
MAIN_REPO="${MAIN_REPO:-$HOME/Projects/cheese-backend-py}"
CLI_REF="${CLI_REF:-1c9f542}"  # feat(cli): terminal-hosting substrate (design/cheese-agent-layer)

if [ -z "${CLI_SRC:-}" ]; then
  # Auto-extract the frozen cli/ tree from git into a build cache next to DIST.
  CLI_SRC="$DIST/../.cli-src"
  echo "CLI_SRC unset — extracting cli/ from $MAIN_REPO @ $CLI_REF"
  rm -rf "$CLI_SRC"; mkdir -p "$CLI_SRC"
  git -C "$MAIN_REPO" archive "$CLI_REF" cli/ | tar -x -C "$CLI_SRC" --strip-components=1
fi
[ -f "$CLI_SRC/go.mod" ] || { echo "no go.mod under CLI_SRC=$CLI_SRC" >&2; exit 1; }
rm -rf "$DIST"; mkdir -p "$DIST"
cd "$CLI_SRC"
for target in darwin-arm64 darwin-amd64 linux-arm64 linux-amd64; do
  os=${target%-*}; arch=${target#*-}
  GOFLAGS=-mod=mod GOOS=$os GOARCH=$arch \
    go build -ldflags "-X main.version=cheesex-connector" \
    -o "$DIST/$target/cheesehost" .
  echo "built $target/cheesehost"
done
