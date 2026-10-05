#!/bin/sh
set -e

# Replace build-time placeholders with runtime environment variables
# If an env var is unset, the placeholder remains (harmless — behaves like undefined)
find /usr/share/nginx/html/assets -name '*.js' -exec sed -i \
  -e "s|__VITE_API_BASE_URL__|${VITE_API_BASE_URL:-}|g" \
  -e "s|__VITE_NEW_API_BASE_URL__|${VITE_NEW_API_BASE_URL:-}|g" \
  -e "s|__VITE_AI_API_BASE_URL__|${VITE_AI_API_BASE_URL:-}|g" \
  -e "s|__VITE_CONNECTOR_WS_BASE__|${VITE_CONNECTOR_WS_BASE:-}|g" \
  -e "s|__VITE_DOCS_ORIGIN__|${DOCS_ORIGIN:-}|g" \
  {} +

# Where the docs site is (frontend/nginx/docs/). Empty DOCS_ORIGIN: under /docs/
# here. Set: the docs' own server answers its host, and /docs/ here redirects
# there. Checked before it goes into the config: a typo has to stop the
# container, not quietly send every reader to a host that does not exist.
mkdir -p /etc/nginx/docs
if [ -n "${DOCS_ORIGIN:-}" ]; then
  if ! printf '%s' "$DOCS_ORIGIN" | grep -Eq '^https?://[a-z0-9.-]+(:[0-9]+)?$'; then
    echo "DOCS_ORIGIN must be an origin such as https://docs.okcheese.com, not '$DOCS_ORIGIN'" >&2
    exit 1
  fi
  docs_host=$(printf '%s' "$DOCS_ORIGIN" | sed -E 's|^https?://||; s|:[0-9]+$||')
  sed "s|__DOCS_ORIGIN__|$DOCS_ORIGIN|g" /etc/nginx/docs-modes/redirect.conf > /etc/nginx/docs/platform.conf
  sed "s|__DOCS_HOST__|$docs_host|g" /etc/nginx/docs-modes/host.conf > /etc/nginx/docs/host.conf
else
  cp /etc/nginx/docs-modes/under-platform.conf /etc/nginx/docs/platform.conf
  : > /etc/nginx/docs/host.conf
fi

# Where nginx sends /api and /connector (see nginx.conf). Substituted at start,
# like the VITE placeholders above, because nginx reads no environment itself.
sed -i "s|__API_UPSTREAM__|${API_UPSTREAM:-backend:8081}|g" /etc/nginx/nginx.conf /etc/nginx/docs/platform.conf /etc/nginx/docs/host.conf
sed -i "s|__DEVICE_CONNECTION_UPSTREAM__|${DEVICE_CONNECTION_UPSTREAM:-device-connection:8082}|g" /etc/nginx/nginx.conf
sed -i "s|__FORGEJO_UPSTREAM__|${FORGEJO_UPSTREAM:-backend:8081}|g" /etc/nginx/nginx.conf
sed -i "s|__FORGE_EVENTS_UPSTREAM__|${FORGE_EVENTS_UPSTREAM:-backend:8081}|g" /etc/nginx/nginx.conf
sed -i "s|__COLLAB_UPSTREAM__|${COLLAB_UPSTREAM:-collab:8902}|g" /etc/nginx/nginx.conf
sed -i "s|__OFFICE_EDITOR_UPSTREAM__|${OFFICE_EDITOR_UPSTREAM:-cheese-office-editor:80}|g" /etc/nginx/nginx.conf

/usr/local/bin/check-static-assets /usr/share/nginx/html

exec "$@"
