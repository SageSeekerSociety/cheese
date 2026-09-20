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

# Where nginx sends /api and /connector (see nginx.conf). Substituted at start,
# like the VITE placeholders above, because nginx reads no environment itself.
sed -i "s|__API_UPSTREAM__|${API_UPSTREAM:-backend:8081}|g" /etc/nginx/nginx.conf
sed -i "s|__DEVICE_CONNECTION_UPSTREAM__|${DEVICE_CONNECTION_UPSTREAM:-device-connection:8082}|g" /etc/nginx/nginx.conf
sed -i "s|__GIT_UPSTREAM__|${GIT_UPSTREAM:-git:8084}|g" /etc/nginx/nginx.conf

/usr/local/bin/check-static-assets /usr/share/nginx/html

exec "$@"
