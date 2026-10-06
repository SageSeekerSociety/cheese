#!/bin/sh
# Where the docs site is served, decided at container start (docker-entrypoint.sh).
#
#   docs-mode.sh NGINX_DIR HTML_ROOT DOCS_HOST_ROOT
#
# DOCS_ORIGIN empty: the platform serves the /docs build under /docs/.
# DOCS_ORIGIN set:   the docs host's own server answers its host
#                    (NGINX_DIR/docs/host.conf) and the platform's /docs/
#                    redirects there (NGINX_DIR/docs/platform.conf).
#
# Both docs builds carry placeholders for what differs per deployment, filled
# here so no deployment's domain is built into the image: the docs' public
# origin (in llms.txt, the .md pages, RSS) and, in the docs host's build, the
# platform's origin (links back to the product, the demo stages). The app
# bundle's VITE_DOCS_ORIGIN is filled the same way.
#
# Origins are written as browsers write them in Origin (lower case, no default
# port, no trailing slash) and a blank value is unset, as the backend reads
# them (app/core/config.py). Anything else stops the container: a bad value has
# to fail the rollout, not quietly send readers to a host that answers nothing.
set -eu
NGINX_DIR="$1"
HTML_ROOT="$2"
DOCS_HOST_ROOT="$3"

origin_of() {
  printf '%s' "$1" | tr -d '[:space:]' | tr 'A-Z' 'a-z' |
    sed -E 's|/+$||; s|^(https://[^/:]+):443$|\1|; s|^(http://[^/:]+):80$|\1|'
}
host_of() { printf '%s' "$1" | sed -E 's|^https?://||; s|:[0-9]+$||'; }
is_origin() { printf '%s' "$1" | grep -Eq '^https?://[a-z0-9.-]+(:[0-9]+)?$'; }
refuse() { echo "$*" >&2; exit 1; }
# GNU and BSD sed disagree on -i; a temp file works under both.
fill() { # file, sed program
  sed -e "$2" "$1" > "$1.fill" && mv "$1.fill" "$1"
}

docs=$(origin_of "${DOCS_ORIGIN:-}")
platform=$(origin_of "${FRONTEND_URL:-}")

mkdir -p "$NGINX_DIR/docs"
if [ -n "$docs" ]; then
  is_origin "$docs" || refuse "DOCS_ORIGIN must be an origin such as https://docs.okcheese.com, not '$DOCS_ORIGIN'"
  is_origin "$platform" || refuse "DOCS_ORIGIN is set, so FRONTEND_URL must name the platform's origin (the backend's FRONTEND_URL), not '${FRONTEND_URL:-}'"
  [ "$(host_of "$docs")" != "$(host_of "$platform")" ] ||
    refuse "DOCS_ORIGIN '$DOCS_ORIGIN' is the platform's own host: its server would answer every platform request. Use a host of its own, or leave DOCS_ORIGIN empty for /docs/."
  sed "s|__DOCS_ORIGIN__|$docs|g" "$NGINX_DIR/docs-modes/redirect.conf" > "$NGINX_DIR/docs/platform.conf"
  sed "s|__DOCS_HOST__|$(host_of "$docs")|g" "$NGINX_DIR/docs-modes/host.conf" > "$NGINX_DIR/docs/host.conf"
else
  cp "$NGINX_DIR/docs-modes/under-platform.conf" "$NGINX_DIR/docs/platform.conf"
  : > "$NGINX_DIR/docs/host.conf"
fi

# The /docs build's public origin is the platform's; with FRONTEND_URL unset its
# absolute links become root-relative, which still resolve on the platform.
find "$HTML_ROOT/docs" -type f \( -name '*.html' -o -name '*.js' -o -name '*.json' -o -name '*.md' -o -name '*.txt' -o -name '*.xml' \) |
  while read -r f; do fill "$f" "s|__DOCS_SITE__|$platform|g"; done
find "$DOCS_HOST_ROOT" -type f \( -name '*.html' -o -name '*.js' -o -name '*.json' -o -name '*.md' -o -name '*.txt' -o -name '*.xml' \) |
  while read -r f; do fill "$f" "s|__DOCS_SITE__|$docs|g; s|__DOCS_PLATFORM__|$platform|g"; done
find "$HTML_ROOT/assets" -name '*.js' | while read -r f; do fill "$f" "s|__VITE_DOCS_ORIGIN__|$docs|g"; done
