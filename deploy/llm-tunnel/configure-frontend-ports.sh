#!/usr/bin/env bash
# The box's own frontend ports (:8080 and :80, which the edge reaches directly)
# as an app-router server that forwards to whichever frontend slot serves.
# Prepare only; deploy-docker.sh validates and reloads app-router.
set -euo pipefail
ACTIVE_DIR="${1:?usage: configure-frontend-ports.sh ACTIVE_DIRECTORY PORT DIRECT_LISTEN}"
PORT="${2:?port required}"
# host:port, as FRONTEND_PORT_DIRECT names it for the compose frontend.
DIRECT="${3:?direct listen address required}"
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT > 0 && PORT < 65536)) || { echo "Invalid port: $PORT" >&2; exit 1; }
[[ "$DIRECT" =~ ^[0-9.]+:[0-9]+$ ]] || { echo "Invalid listen address: $DIRECT" >&2; exit 1; }
CONFIG_TMP="$(mktemp "$ACTIVE_DIR/.frontend-ports.XXXXXX")"
trap 'rm -f "$CONFIG_TMP"' EXIT
# Limits are the frontend's own (it still enforces them: 2g bodies and 15 min
# for git and forge, an hour for the office editor), so none are added here.
cat > "$CONFIG_TMP" <<CONF
server {
  listen 0.0.0.0:$PORT;
  listen $DIRECT;
  client_max_body_size 0;
  location / {
    proxy_pass http://frontend_active;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_request_buffering off;
  }
}
CONF
chmod 644 "$CONFIG_TMP"
mv -f "$CONFIG_TMP" "$ACTIVE_DIR/frontend-ports.conf"
