#!/usr/bin/env bash
# Issue or renew dev-gateway.okcheese.com and install it on the dev intranet
# gateway's APISIX (192.168.16.11), whose hand-pasted cert for the ghg.org.cn
# name expired 2026-10-05 and cannot be renewed from here (no DNS API there).
#
#   gateway-tls-renew.sh
#
# Runs daily from cheese-gateway-tls-renew.timer on the dev box. certbot only
# renews within 30 days of expiry; the APISIX object is replaced only when the
# served certificate differs from the issued one. Fails (non-zero) when the
# certificate the gateway serves is within ALERT_DAYS of expiry, so the unit
# shows failed in `systemctl --failed` long before seats lose their tunnel.
set -euo pipefail
NAME="${NAME:-dev-gateway.okcheese.com}"
GATEWAY="${GATEWAY:-nictheboy@192.168.16.11}"
SSL_ID="${SSL_ID:-dev-gateway-okcheese}"
ALERT_DAYS="${ALERT_DAYS:-14}"
TLS_STATE_DIR="${TLS_STATE_DIR:-$HOME/ops/tls}"
CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/dns-cloudflare:v5.8.0}"

docker run --rm --user "$(id -u):$(id -g)" \
  -v "$TLS_STATE_DIR/letsencrypt:/etc/letsencrypt" \
  -v "$TLS_STATE_DIR/cloudflare.ini:/cloudflare.ini:ro" \
  "$CERTBOT_IMAGE" certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --config-dir /etc/letsencrypt --work-dir /etc/letsencrypt/work --logs-dir /etc/letsencrypt/logs \
  --dns-cloudflare --dns-cloudflare-credentials /cloudflare.ini \
  --dns-cloudflare-propagation-seconds 30 \
  --cert-name "$NAME" --keep-until-expiring -d "$NAME"

live="$TLS_STATE_DIR/letsencrypt/live/$NAME"
issued=$(openssl x509 -in "$live/fullchain.pem" -noout -serial | cut -d= -f2)
served() {
  echo | timeout 15 openssl s_client -connect "${GATEWAY#*@}:443" -servername "$NAME" 2>/dev/null \
    | openssl x509 -noout -serial 2>/dev/null | cut -d= -f2
}
if [[ "$(served)" != "$issued" ]]; then
  # The admin API listens on the gateway's loopback only; the key stays there.
  python3 - "$live/fullchain.pem" "$live/privkey.pem" "$NAME" <<'PY' | ssh -o BatchMode=yes "$GATEWAY" "
    set -e
    cd ~/apisix-stack
    key=\$(grep -A4 'admin_key:' apisix_conf/config.yaml | grep -m1 -E '^ *key:' | awk '{print \$2}' | tr -d '\"')
    mkdir -p backups
    curl -sf -H \"X-API-KEY: \$key\" http://127.0.0.1:9180/apisix/admin/ssls/$SSL_ID \
      > backups/ssl-$SSL_ID-\$(date -u +%Y%m%dT%H%M%SZ).json || true
    curl -sf -X PUT -H \"X-API-KEY: \$key\" -H 'Content-Type: application/json' \
      --data-binary @- http://127.0.0.1:9180/apisix/admin/ssls/$SSL_ID >/dev/null"
import json, sys
cert, key, name = sys.argv[1:4]
print(json.dumps({"cert": open(cert).read(), "key": open(key).read(), "snis": [name]}))
PY
  sleep 3
  [[ "$(served)" == "$issued" ]] || { echo "gateway still serves another certificate for $NAME" >&2; exit 1; }
  echo "installed $NAME serial $issued on the gateway"
fi

end=$(echo | timeout 15 openssl s_client -connect "${GATEWAY#*@}:443" -servername "$NAME" 2>/dev/null \
  | openssl x509 -noout -enddate | cut -d= -f2)
left=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 ))
echo "$NAME served until $end ($left days)"
(( left > ALERT_DAYS )) || { echo "$NAME expires in $left days" >&2; exit 1; }
