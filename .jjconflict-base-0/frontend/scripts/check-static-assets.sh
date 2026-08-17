#!/bin/sh
set -eu

static_root="${1:-/usr/share/nginx/html}"
index_file="$static_root/index.html"

error() {
  echo "STATIC ASSETS ERROR: $*" >&2
}

if [ ! -f "$index_file" ]; then
  error "index.html not found at $index_file"
  exit 1
fi

references="$({
  grep -oE '(src|href)="[^"]+"' "$index_file" || true
} | sed -E 's/^(src|href)="([^"]+)"$/\2/')"

local_count=0
javascript_count=0
css_count=0
failures=0

for reference in $references; do
  clean_reference="$(printf '%s\n' "$reference" | sed 's/[?#].*$//')"
  case "$clean_reference" in
    /assets/*) relative_path="${clean_reference#/}" ;;
    assets/*) relative_path="$clean_reference" ;;
    ./assets/*) relative_path="${clean_reference#./}" ;;
    *) continue ;;
  esac

  local_count=$((local_count + 1))
  case "$relative_path" in
    *.js|*.mjs) javascript_count=$((javascript_count + 1)) ;;
    *.css) css_count=$((css_count + 1)) ;;
  esac

  case "/$relative_path/" in
    */../*|*/./*)
      error "invalid asset path $reference"
      failures=$((failures + 1))
      continue
      ;;
  esac

  if [ ! -f "$static_root/$relative_path" ]; then
    error "missing $relative_path (referenced as $reference)"
    failures=$((failures + 1))
  fi
done

if [ "$javascript_count" -eq 0 ]; then
  error "index.html has no JavaScript asset reference"
  failures=$((failures + 1))
fi

if [ "$css_count" -eq 0 ]; then
  error "index.html has no CSS asset reference"
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  exit 1
fi

echo "STATIC ASSETS OK: $local_count local references"
