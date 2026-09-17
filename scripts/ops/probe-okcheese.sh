#!/usr/bin/env bash
# What a visitor to okcheese.com gets, checked from outside every network this
# project owns. Run it anywhere with curl and openssl; the workflow that calls it
# on a schedule is .github/workflows/okcheese-uptime.yml.
#
# Each check exists because that failure has happened here or is one nothing else
# would catch:
#
#   reachable      the site is served through a reverse SSH tunnel from the dev
#                  box to a Hong Kong box. Either end going away takes the domain
#                  with it and nothing else reports that.
#   redirect       a browser sends you to http first. When Caddy did not own :80
#                  that landed on a different, months-old deployment whose login
#                  page had no providers, and it read as a broken site.
#   providers      the tunnel can be up while what is behind it is broken. An
#                  empty provider list means nobody can log in, which a 200 on
#                  the front page does not reveal.
#   certificate    these names are issued over TLS-ALPN only, so a renewal that
#                  starts failing is silent until the certificate expires.
#
# Exit 0 = all good. Exit 1 = a check failed, with the reason on stdout.
set -uo pipefail

HOST=${OKCHEESE_HOST:-okcheese.com}
CERT_MIN_DAYS=${CERT_MIN_DAYS:-14}
fails=()

say() { printf '%s\n' "$*"; }

# --- reachable over https -----------------------------------------------------
code=$(curl -sS -o /dev/null -m 25 -w '%{http_code}' "https://$HOST/" 2>/dev/null)
if [ "$code" = "200" ]; then
  say "OK   https://$HOST/ -> 200"
else
  fails+=("https://$HOST/ answered '${code:-no response}', wanted 200")
fi

# --- plain http redirects to https -------------------------------------------
read -r rcode rurl < <(curl -sS -o /dev/null -m 25 -w '%{http_code} %{redirect_url}' "http://$HOST/" 2>/dev/null)
case "$rcode:$rurl" in
  30[128]:https://*) say "OK   http://$HOST/ -> $rcode $rurl" ;;
  *) fails+=("http://$HOST/ answered '${rcode:-no response}' to '${rurl:-no location}', wanted a 301 to https") ;;
esac

# --- someone can actually log in ---------------------------------------------
body=$(curl -sS -m 25 "https://$HOST/api/users/auth/oauth/providers" 2>/dev/null)
count=$(printf '%s' "$body" | grep -o '"id"' | wc -l | tr -d ' ')
if [ "${count:-0}" -ge 1 ]; then
  say "OK   login providers: $count"
else
  fails+=("the login page has no providers (answered: ${body:-no response}) — the tunnel can be up while what is behind it is not")
fi

# --- certificate has time left ------------------------------------------------
not_after=$(echo | openssl s_client -connect "$HOST:443" -servername "$HOST" 2>/dev/null \
  | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
if [ -n "$not_after" ]; then
  if exp=$(date -d "$not_after" +%s 2>/dev/null); then :
  else exp=$(date -j -f "%b %d %T %Y %Z" "$not_after" +%s 2>/dev/null); fi
  if [ -n "${exp:-}" ]; then
    days=$(( (exp - $(date +%s)) / 86400 ))
    if [ "$days" -ge "$CERT_MIN_DAYS" ]; then
      say "OK   certificate expires in $days days ($not_after)"
    else
      fails+=("the certificate expires in $days days ($not_after) — renewal here has only the TLS-ALPN path, so check it now")
    fi
  else
    fails+=("could not parse the certificate expiry '$not_after'")
  fi
else
  fails+=("could not read a certificate from $HOST:443")
fi

if [ ${#fails[@]} -eq 0 ]; then
  say "all checks passed for $HOST"
  exit 0
fi
say ""
say "FAILED for $HOST:"
for f in "${fails[@]}"; do say "  - $f"; done
exit 1
