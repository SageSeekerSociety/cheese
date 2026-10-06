#!/usr/bin/env bash
# Writes app-router's frontend.conf: the upstream app-router sends frontend
# traffic to and, once app-router owns them, the box's own frontend ports
# (:8080 and :80, which the edge reaches directly) forwarding to it.
#
# The ports live in this file, not a file of their own, because every
# app-router.conf there has been includes frontend.conf: a release of an older
# commit installs its own app-router.conf and reloads, and the ports stay up.
# Prepare only; deploy-docker.sh validates and reloads app-router.
set -euo pipefail
ACTIVE_DIR="${1:?usage: configure-frontend-upstream.sh ACTIVE_DIRECTORY UPSTREAM_PORT [PORT DIRECT_LISTEN]}"
UPSTREAM="${2:?upstream port required}"
PORT="${3:-}"
# ip:port or [ipv6]:port; deploy-docker.sh turns FRONTEND_PORT_DIRECT into one.
DIRECT="${4:-}"
for port in "$UPSTREAM" ${PORT:+"$PORT"}; do
  [[ "$port" =~ ^[0-9]+$ ]] && ((port > 0 && port < 65536)) || { echo "Invalid port: $port" >&2; exit 1; }
done
if [ -n "$PORT" ]; then
  [[ "$DIRECT" =~ ^([0-9]{1,3}(\.[0-9]{1,3}){3}|\[[0-9A-Fa-f:]+\]):[0-9]+$ ]] \
    || { echo "Invalid listen address: $DIRECT" >&2; exit 1; }
fi
CONFIG_TMP="$(mktemp "$ACTIVE_DIR/.frontend.conf.XXXXXX")"
trap 'rm -f "$CONFIG_TMP"' EXIT
printf 'upstream frontend_active { server 127.0.0.1:%s; }\n' "$UPSTREAM" > "$CONFIG_TMP"
if [ -n "$PORT" ]; then
  # Limits are the frontend's own (it still enforces them: 2g bodies and 15 min
  # for git and forge, an hour for the office editor), so none are added here.
  cat >> "$CONFIG_TMP" <<CONF
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
fi
chmod 644 "$CONFIG_TMP"
# Rename, never rewrite in place: nginx re-reads the file on reload and a
# half-written one would take the whole server config down with it.
mv -f "$CONFIG_TMP" "$ACTIVE_DIR/frontend.conf"
