#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CHECKER="$SCRIPT_DIR/check-static-assets.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/check-static-assets.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT HUP INT TERM

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_contains() {
  haystack="$1"
  needle="$2"
  case "$haystack" in
    *"$needle"*) ;;
    *) fail "expected output to contain '$needle', got: $haystack" ;;
  esac
}

make_complete_fixture() {
  fixture="$1"
  mkdir -p "$fixture/assets"
  cat > "$fixture/index.html" <<'HTML'
<!doctype html>
<html>
  <head>
    <link rel="stylesheet" href="assets/app.css#theme">
    <link rel="modulepreload" href="./assets/vendor.js">
    <link rel="preload" href="/assets/font.woff2" as="font">
  </head>
  <body><script type="module" src="/assets/app.js?v=1"></script></body>
</html>
HTML
  : > "$fixture/assets/app.css"
  : > "$fixture/assets/vendor.js"
  : > "$fixture/assets/font.woff2"
  : > "$fixture/assets/app.js"
  mkdir -p "$fixture/docs"
  : > "$fixture/docs/index.html"
  : > "$fixture/docs/llms.txt"
}

complete="$TEST_ROOT/complete"
make_complete_fixture "$complete"
output="$(sh "$CHECKER" "$complete")"
assert_contains "$output" "STATIC ASSETS OK: 4 local references, manual present"
echo "PASS: complete asset tree"

missing="$TEST_ROOT/missing"
make_complete_fixture "$missing"
rm "$missing/assets/app.css"
if output="$(sh "$CHECKER" "$missing" 2>&1)"; then
  fail "checker accepted an index with a missing stylesheet"
fi
assert_contains "$output" "missing assets/app.css"
echo "PASS: missing asset rejected"

no_entries="$TEST_ROOT/no-entries"
mkdir -p "$no_entries/assets"
cat > "$no_entries/index.html" <<'HTML'
<!doctype html><link rel="preload" href="/assets/font.woff2" as="font">
HTML
: > "$no_entries/assets/font.woff2"
if output="$(sh "$CHECKER" "$no_entries" 2>&1)"; then
  fail "checker accepted an index without JavaScript and CSS entrypoints"
fi
assert_contains "$output" "no JavaScript asset reference"
assert_contains "$output" "no CSS asset reference"
echo "PASS: missing entrypoints rejected"

no_manual="$TEST_ROOT/no-manual"
make_complete_fixture "$no_manual"
rm -r "$no_manual/docs"
if output="$(sh "$CHECKER" "$no_manual" 2>&1)"; then
  fail "checker accepted an image with no user manual under /docs"
fi
assert_contains "$output" "missing docs/index.html"
echo "PASS: missing manual rejected"

no_index="$TEST_ROOT/no-index"
mkdir -p "$no_index"
if output="$(sh "$CHECKER" "$no_index" 2>&1)"; then
  fail "checker accepted a static root without index.html"
fi
assert_contains "$output" "index.html not found"
echo "PASS: missing index rejected"
