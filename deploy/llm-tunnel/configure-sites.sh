#!/usr/bin/env bash
# Write the optional content-host server into api-front's existing active mount.
# This prepares configuration only; the operator validates and reloads nginx.
set -euo pipefail

DOMAIN="${1:?usage: configure-sites.sh DOMAIN|--disable [ACTIVE_DIRECTORY]}"
ACTIVE_DIR="${2:-${ACTIVE_BACKEND_DIR:-$(dirname "$0")/active}}"
if [[ "$DOMAIN" != "--disable" && ! "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]]; then
  echo "Use a lowercase domain without a scheme, wildcard or port." >&2
  exit 1
fi

mkdir -p "$ACTIVE_DIR"
CONFIG_TMP="$(mktemp "$ACTIVE_DIR/.sites.XXXXXX")"
trap 'rm -f "$CONFIG_TMP"' EXIT
if [[ "$DOMAIN" != "--disable" ]]; then
  cat > "$CONFIG_TMP" <<EOF
server {
  listen 8081;
  server_name $DOMAIN *.$DOMAIN;

  location / {
    proxy_pass http://backend_active;
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
