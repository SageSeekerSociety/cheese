#!/bin/sh
set -e

# Replace build-time placeholders with runtime environment variables
# If an env var is unset, the placeholder remains (harmless — behaves like undefined)
find /usr/share/nginx/html/assets -name '*.js' -exec sed -i \
  -e "s|__VITE_API_BASE_URL__|${VITE_API_BASE_URL:-}|g" \
  -e "s|__VITE_NEW_API_BASE_URL__|${VITE_NEW_API_BASE_URL:-}|g" \
  -e "s|__VITE_AI_API_BASE_URL__|${VITE_AI_API_BASE_URL:-}|g" \
  {} +

exec "$@"
