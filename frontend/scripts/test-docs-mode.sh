#!/bin/sh
# docs-mode.sh against throwaway trees: which nginx config it writes, what it
# fills into the docs builds, and which DOCS_ORIGIN / FRONTEND_URL it refuses.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
MODES="$SCRIPT_DIR/../nginx/docs"
DOCS_MODE="$SCRIPT_DIR/docs-mode.sh"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/docs-mode.XXXXXX")"
trap 'rm -rf "$ROOT"' EXIT HUP INT TERM

fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$1 does not contain '$2'"; }
lacks() { if grep -qF -- "$2" "$1"; then fail "$1 still contains '$2'"; fi; }

# The image's layout: the two docs builds with their placeholders, the SPA bundle.
fixture() {
  d="$ROOT/$1"
  rm -rf "$d"
  mkdir -p "$d/etc" "$d/html/assets" "$d/html/docs" "$d/docs-host/dev"
  cp -R "$MODES" "$d/etc/docs-modes"
  echo 'const d="__VITE_DOCS_ORIGIN__"' > "$d/html/assets/main.js"
  echo '<a href="__DOCS_SITE__/docs/llms.txt">' > "$d/html/docs/index.html"
  echo '<a href="__DOCS_PLATFORM__/legal/terms"><iframe src="__DOCS_PLATFORM__/demo/turn?embed=1">' > "$d/docs-host/index.html"
  echo '- [x](__DOCS_SITE__/quickstart.md)' > "$d/docs-host/llms.txt"
  echo "$d"
}
run() { # dir, then env assignments
  d="$1"; shift
  env -i PATH="$PATH" "$@" sh "$DOCS_MODE" "$d/etc" "$d/html" "$d/docs-host"
}

# ---------- no docs host: /docs/ on the platform ----------
d=$(fixture unset)
run "$d" DOCS_ORIGIN="  " FRONTEND_URL=https://okcheese.com >/dev/null || fail "a blank DOCS_ORIGIN stopped the container"
contains "$d/etc/docs/platform.conf" "location /docs/ {"
[ ! -s "$d/etc/docs/host.conf" ] || fail "a docs server was written with no docs host"
contains "$d/html/docs/index.html" 'href="https://okcheese.com/docs/llms.txt"'
contains "$d/html/assets/main.js" 'const d=""'
echo "PASS: blank DOCS_ORIGIN is unset"

# ---------- a docs host ----------
d=$(fixture host)
run "$d" DOCS_ORIGIN="https://Docs.Example.test:443/" FRONTEND_URL="https://app.example.test/" >/dev/null
contains "$d/etc/docs/host.conf" "server_name docs.example.test frontend;"
contains "$d/etc/docs/platform.conf" "return 301 https://docs.example.test/"
lacks "$d/etc/docs/platform.conf" ":443"
contains "$d/html/assets/main.js" 'const d="https://docs.example.test"'
contains "$d/docs-host/index.html" 'href="https://app.example.test/legal/terms"'
contains "$d/docs-host/index.html" 'src="https://app.example.test/demo/turn?embed=1"'
contains "$d/docs-host/llms.txt" '(https://docs.example.test/quickstart.md)'
echo "PASS: a docs host, its default port dropped, its platform filled in"

# ---------- refused ----------
refused() {
  what="$1"; shift
  d=$(fixture refused)
  if out=$(run "$d" "$@" 2>&1); then fail "accepted $what"; fi
  echo "$out" | grep -q DOCS_ORIGIN || fail "refusing $what did not say why: $out"
  echo "PASS: refuses $what"
}
refused "the platform's own host" DOCS_ORIGIN=https://okcheese.com FRONTEND_URL=https://okcheese.com
refused "the platform's own host with :443" DOCS_ORIGIN=https://okcheese.com:443 FRONTEND_URL=https://OKCHEESE.com/
refused "a docs host with no FRONTEND_URL" DOCS_ORIGIN=https://docs.okcheese.com
refused "a path" DOCS_ORIGIN=https://docs.okcheese.com/docs FRONTEND_URL=https://okcheese.com
refused "no scheme" DOCS_ORIGIN=docs.okcheese.com FRONTEND_URL=https://okcheese.com
