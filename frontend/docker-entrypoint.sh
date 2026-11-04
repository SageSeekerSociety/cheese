#!/bin/sh
set -e

# Replace build-time placeholders with runtime environment variables
# If an env var is unset, the placeholder remains (harmless — behaves like undefined)
find /usr/share/nginx/html/assets -name '*.js' -exec sed -i \
  -e "s|__VITE_API_BASE_URL__|${VITE_API_BASE_URL:-}|g" \
  -e "s|__VITE_NEW_API_BASE_URL__|${VITE_NEW_API_BASE_URL:-}|g" \
  -e "s|__VITE_AI_API_BASE_URL__|${VITE_AI_API_BASE_URL:-}|g" \
  -e "s|__VITE_CONNECTOR_WS_BASE__|${VITE_CONNECTOR_WS_BASE:-}|g" \
  {} +

# Where the docs site is, and the origins its pages and the app's links name
# (scripts/docs-mode.sh). It stops the container on a DOCS_ORIGIN it cannot use.
/usr/local/bin/docs-mode /etc/nginx /usr/share/nginx/html /usr/share/nginx/docs-host

# Where nginx sends /api and /connector (see nginx.conf). Substituted at start,
# like the VITE placeholders above, because nginx reads no environment itself.
sed -i "s|__API_UPSTREAM__|${API_UPSTREAM:-backend:8081}|g" /etc/nginx/nginx.conf /etc/nginx/docs/platform.conf /etc/nginx/docs/host.conf
sed -i "s|__DEVICE_CONNECTION_UPSTREAM__|${DEVICE_CONNECTION_UPSTREAM:-device-connection:8082}|g" /etc/nginx/nginx.conf
sed -i "s|__FORGEJO_UPSTREAM__|${FORGEJO_UPSTREAM:-backend:8081}|g" /etc/nginx/nginx.conf
sed -i "s|__FORGE_EVENTS_UPSTREAM__|${FORGE_EVENTS_UPSTREAM:-backend:8081}|g" /etc/nginx/nginx.conf
sed -i "s|__COLLAB_UPSTREAM__|${COLLAB_UPSTREAM:-collab:8902}|g" /etc/nginx/nginx.conf
sed -i "s|__OFFICE_EDITOR_UPSTREAM__|${OFFICE_EDITOR_UPSTREAM:-cheese-office-editor:80}|g" /etc/nginx/nginx.conf

# How many connections one visitor's address may hold
# (GATEWAY_CONNECTIONS_PER_ADDRESS). The visitor's address is the one
# mainland/main.conf resolves from X-Forwarded-For. Generous on purpose: a
# campus is one address behind NAT and every open tab holds a socket. Unset,
# nothing is limited per address.
if [ -n "${GATEWAY_CONNECTIONS_PER_ADDRESS:-}" ]; then
  case "$GATEWAY_CONNECTIONS_PER_ADDRESS" in
    *[!0-9]*) echo "GATEWAY_CONNECTIONS_PER_ADDRESS must be a number, not '$GATEWAY_CONNECTIONS_PER_ADDRESS'" >&2; exit 1 ;;
  esac
  {
    echo "limit_conn_zone \$binary_remote_addr zone=per_address:10m;"
    echo "limit_conn per_address $GATEWAY_CONNECTIONS_PER_ADDRESS;"
    echo "limit_conn_status 429;"
  } > /etc/nginx/snippets/per-address.conf
fi

# Who this deployment sends from one of its origins to another, and where
# (scripts/mainland-redirect.sh). Unset — the default — redirects nobody; a box
# that turns it on needs no rebuild. nginx.conf includes both files either way.
/usr/local/bin/mainland-redirect /etc/nginx

/usr/local/bin/check-static-assets /usr/share/nginx/html

exec "$@"
