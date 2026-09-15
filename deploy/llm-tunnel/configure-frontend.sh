#!/usr/bin/env bash
# The existing optional sites include can also hold the application front door.
# Prepare only; validate and reload nginx before changing tunnel ingress.
set -euo pipefail
ACTIVE_DIR="${1:?usage: configure-frontend.sh ACTIVE_DIRECTORY UPSTREAM_PORT [LISTEN_PORT]}"
UPSTREAM_PORT="${2:?upstream port required}"
LISTEN_PORT="${3:-18080}"
for port in "$UPSTREAM_PORT" "$LISTEN_PORT"; do
  [[ "$port" =~ ^[0-9]+$ ]] && ((port > 0 && port < 65536)) || { echo "Invalid port: $port" >&2; exit 1; }
done
CONFIG_TMP="$(mktemp "$ACTIVE_DIR/.frontend.XXXXXX")"
trap 'rm -f "$CONFIG_TMP"' EXIT
cat > "$CONFIG_TMP" <<EOF
upstream frontend_active { server 127.0.0.1:$UPSTREAM_PORT; }
server {
  listen 127.0.0.1:$LISTEN_PORT;

  # Device and execution traffic enters through this stable front door with
  # the public /api prefix. Keep it off the frontend containers this proxy
  # replaces during an ordinary application rollout.
  location = /api/connector/agent {
    proxy_pass http://127.0.0.1:18083/connector/agent;
    proxy_http_version 1.1;
    proxy_set_header Host \$http_host;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_read_timeout 24h;
    proxy_send_timeout 24h;
    proxy_buffering off;
  }

  location ~ ^/api/connector/session/[^/]+/screen\$ {
    rewrite ^/api/(.*)\$ /\$1 break;
    proxy_pass http://127.0.0.1:18083;
    proxy_http_version 1.1;
    proxy_set_header Host \$http_host;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_read_timeout 24h;
    proxy_send_timeout 24h;
    proxy_buffering off;
  }

  location ~ ^/api/topics/[^/]+/execution/[^/]+\$ {
    rewrite ^/api/(.*)\$ /\$1 break;
    proxy_pass http://127.0.0.1:18083;
    proxy_http_version 1.1;
    proxy_set_header Host \$http_host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    client_max_body_size 100m;
    proxy_read_timeout 15m;
    proxy_send_timeout 15m;
    proxy_buffering off;
  }

  location / {
    proxy_pass http://frontend_active;
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
mv -f "$CONFIG_TMP" "$ACTIVE_DIR/sites-frontend.conf"
