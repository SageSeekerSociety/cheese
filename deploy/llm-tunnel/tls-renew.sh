#!/usr/bin/env bash
# Issue or renew the certificate for the public names and hand it to api-front.
#
#   tls-renew.sh ACTIVE_DIRECTORY FRONTEND_PROXY_PORT DOMAIN [DOMAIN...]
#
# The certificate lives on this box because TLS for the public names ends
# here, not at the Hong Kong relay (see configure-frontend.sh). Hong Kong only
# sees the encrypted stream, so the ACME challenge cannot be answered over
# HTTP there either; it is answered through Cloudflare DNS instead.
#
# Box-local inputs, never committed:
#   $TLS_STATE_DIR/cloudflare.ini   dns_cloudflare_api_token = <token scoped to
#                                   Zone.DNS:Edit on the domain's zone>
#   $TLS_STATE_DIR/letsencrypt/     certbot's account and certificate state
#
# Run it daily (a systemd timer); certbot only renews within 30 days of expiry,
# and nginx is reloaded only when the files it serves actually changed.
set -euo pipefail
ACTIVE_DIR="${1:?usage: tls-renew.sh ACTIVE_DIRECTORY FRONTEND_PROXY_PORT DOMAIN [DOMAIN...]}"
FRONTEND_PROXY_PORT="${2:?frontend proxy port required}"
shift 2
(($# > 0)) || { echo "at least one domain is required" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
TLS_STATE_DIR="${TLS_STATE_DIR:-$HOME/ops/tls}"
API_FRONT_CONTAINER="${API_FRONT_CONTAINER:-cheese-api-front}"
CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/dns-cloudflare:v5.8.0}"
[[ -f "$TLS_STATE_DIR/cloudflare.ini" ]] || { echo "missing $TLS_STATE_DIR/cloudflare.ini" >&2; exit 1; }

domains=()
for domain in "$@"; do domains+=(-d "$domain"); done
docker run --rm \
  -v "$TLS_STATE_DIR/letsencrypt:/etc/letsencrypt" \
  -v "$TLS_STATE_DIR/cloudflare.ini:/cloudflare.ini:ro" \
  "$CERTBOT_IMAGE" certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --dns-cloudflare --dns-cloudflare-credentials /cloudflare.ini \
  --dns-cloudflare-propagation-seconds 30 \
  --cert-name "$1" --keep-until-expiring "${domains[@]}"

live="$TLS_STATE_DIR/letsencrypt/live/$1"
target="$ACTIVE_DIR/tls"
mkdir -p "$target"
if cmp -s "$live/fullchain.pem" "$target/fullchain.pem" && cmp -s "$live/privkey.pem" "$target/privkey.pem"; then
  echo "certificate unchanged; nginx left alone"
  exit 0
fi
backup="$(mktemp -d "$TLS_STATE_DIR/.previous.XXXXXX")"
cp -a "$target/." "$backup/" 2>/dev/null || true
cp -L "$live/fullchain.pem" "$target/fullchain.pem.new"
cp -L "$live/privkey.pem" "$target/privkey.pem.new"
chmod 600 "$target/privkey.pem.new"
mv -f "$target/fullchain.pem.new" "$target/fullchain.pem"
mv -f "$target/privkey.pem.new" "$target/privkey.pem"
bash "$HERE/configure-frontend.sh" "$ACTIVE_DIR" 18086 "$FRONTEND_PROXY_PORT"
if ! docker exec "$API_FRONT_CONTAINER" nginx -t; then
  if [[ -f "$backup/fullchain.pem" ]]; then
    cp -a "$backup/." "$target/"
  else
    rm -f "$target/fullchain.pem" "$target/privkey.pem"
  fi
  bash "$HERE/configure-frontend.sh" "$ACTIVE_DIR" 18086 "$FRONTEND_PROXY_PORT"
  rm -rf "$backup"
  echo "api-front rejected the new certificate; restored the previous files" >&2
  exit 1
fi
docker exec "$API_FRONT_CONTAINER" nginx -s reload
rm -rf "$backup"
echo "installed certificate for $* and reloaded $API_FRONT_CONTAINER"
