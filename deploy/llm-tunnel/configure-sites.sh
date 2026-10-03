#!/usr/bin/env bash
# Write the optional content-host server into api-front's existing active mount.
# This prepares configuration only; the operator validates and reloads nginx.
set -euo pipefail

DOMAIN="${1:?usage: configure-sites.sh DOMAIN|--disable [ACTIVE_DIRECTORY] [PREVIEW_OWNER_PORT]}"
ACTIVE_DIR="${2:-${ACTIVE_BACKEND_DIR:-$(dirname "$0")/active}}"
# Non-empty sends preview content hosts (preview-<uuid>.<DOMAIN>) to the
# independently released preview-connection owner instead of the business
# backend; empty keeps every content host on the backend, as before the cutover.
# The deploy passes it from the effective PREVIEW_CONNECTION_MODE.
PREVIEW_OWNER_PORT="${3:-}"
if [[ "$DOMAIN" != "--disable" && ! "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]]; then
  echo "Use a lowercase domain without a scheme, wildcard or port." >&2
  exit 1
fi
if [ -n "$PREVIEW_OWNER_PORT" ]; then
  [[ "$PREVIEW_OWNER_PORT" =~ ^[0-9]+$ ]] && ((PREVIEW_OWNER_PORT > 0 && PREVIEW_OWNER_PORT < 65536)) \
    || { echo "Invalid preview owner port: $PREVIEW_OWNER_PORT" >&2; exit 1; }
fi

mkdir -p "$ACTIVE_DIR"
CONFIG_TMP="$(mktemp "$ACTIVE_DIR/.sites.XXXXXX")"
trap 'rm -f "$CONFIG_TMP"' EXIT
if [[ "$DOMAIN" != "--disable" ]]; then
  # A preview content host (preview-<uuid>.<DOMAIN>) matches only this wildcard
  # server: a middle wildcard server_name is invalid and a regex server_name
  # loses to *.$DOMAIN, so the split has to happen INSIDE this server. The map
  # keeps the pre-cutover path as its default (the active backend through
  # app-router) and is written into this same file so it travels with the
  # routing it decides — a box that never re-runs this script keeps working.
  if [ -n "$PREVIEW_OWNER_PORT" ]; then
    cat > "$CONFIG_TMP" <<EOF
map \$host \$content_upstream {
  default "127.0.0.1:18085";
  "~^preview-[0-9a-f]{32}(-[0-9a-f]{22})?\." "127.0.0.1:$PREVIEW_OWNER_PORT";
}

EOF
    CONTENT_PROXY_PASS='proxy_pass http://$content_upstream;'
  else
    CONTENT_PROXY_PASS='proxy_pass http://backend_active;'
  fi
  cat >> "$CONFIG_TMP" <<EOF
server {
  listen 8081;
  server_name $DOMAIN *.$DOMAIN;

  location / {
    $CONTENT_PROXY_PASS
    proxy_http_version 1.1;
    proxy_set_header Host \$http_host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    client_max_body_size 100m;
    proxy_buffering off;
  }
}
EOF
fi
# The mounted directory observes this rename without recreating the container.
mv -f "$CONFIG_TMP" "$ACTIVE_DIR/sites.conf"
printf 'Prepared %s/sites.conf; nginx has not been reloaded.\n' "$ACTIVE_DIR"
