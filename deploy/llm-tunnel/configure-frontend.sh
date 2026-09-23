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

  location ~ ^/(api/)?llm/tunnel\$ {
    rewrite ^/api/(.*)\$ /\$1 break;
    proxy_pass http://127.0.0.1:8091;
    proxy_http_version 1.1;
    proxy_set_header Host \$http_host;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_read_timeout 4h;
    proxy_send_timeout 4h;
    proxy_buffering off;
  }

  location ~ ^/(api/)?forge/events/ {
    rewrite ^/api/(.*)\$ /\$1 break;
    proxy_pass http://127.0.0.1:8093;
    proxy_http_version 1.1;
    proxy_set_header Host \$http_host;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_read_timeout 24h;
    proxy_buffering off;
    client_max_body_size 5m;
  }

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

# TLS for the public names, terminated here in the mainland rather than at the
# Hong Kong relay: Hong Kong forwards the encrypted stream by SNI and prepends
# a PROXY protocol header, so it never holds the plaintext and the client's
# address still arrives. Emitted only once a certificate is in place (see
# tls-renew.sh), so a box without one keeps serving the plain listener alone.
#
# "listen ... http2" rather than "http2 on;": the latter is unknown before
# nginx 1.25.1, and CI's distro nginx is older than the box's image.
TLS_DIR="$ACTIVE_DIR/tls"
if [[ -f "$TLS_DIR/fullchain.pem" && -f "$TLS_DIR/privkey.pem" ]]; then
  cat >> "$CONFIG_TMP" <<EOF

server {
  listen 127.0.0.1:18443 ssl http2 proxy_protocol;
  ssl_certificate /etc/nginx/active/tls/fullchain.pem;
  ssl_certificate_key /etc/nginx/active/tls/privkey.pem;
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_session_cache shared:front_tls:10m;
  add_header Strict-Transport-Security "max-age=31536000" always;

  location / {
    proxy_pass http://127.0.0.1:$LISTEN_PORT;
    proxy_http_version 1.1;
    proxy_set_header Host \$http_host;
    proxy_set_header X-Forwarded-For \$proxy_protocol_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_read_timeout 24h;
    proxy_send_timeout 24h;
    proxy_buffering off;
    proxy_request_buffering off;
    client_max_body_size 100m;
  }
}
EOF
fi
mv -f "$CONFIG_TMP" "$ACTIVE_DIR/sites-frontend.conf"
