#!/usr/bin/env bash
# Tell IndexNow engines (Bing, Yandex, Naver, Seznam, Yep) that a host's public
# pages changed, so they recrawl them without waiting for the sitemap.
# Usage: scripts/ops/indexnow.sh [host]   (default okcheese.com)
# The key is the file frontend/public/<key>.txt, served at the host's root, which
# is how the engines check the request comes from the site's owner.
# Run by hand after a change to the public pages; not on a timer, so no retry.
set -euo pipefail

host=${1:-okcheese.com}
root=$(cd "$(dirname "$0")/../.." && pwd)
keyfile=$(ls "$root"/frontend/public/*.txt | while read -r f; do
  [[ $(basename "$f" .txt) =~ ^[0-9a-f]{32}$ ]] && echo "$f"; done | head -1)
key=$(basename "$keyfile" .txt)

urls=$(curl -fsS "https://$host/sitemap.xml" | grep -o '<loc>[^<]*' | sed 's/<loc>//')
[ -n "$urls" ] || { echo "no URLs in https://$host/sitemap.xml" >&2; exit 1; }

body=$(python3 -c 'import json,sys; h,k=sys.argv[1:3]; print(json.dumps({"host":h,"key":k,"keyLocation":f"https://{h}/{k}.txt","urlList":sys.stdin.read().split()}))' "$host" "$key" <<<"$urls")
code=$(curl -sS -o /dev/stderr -w '%{http_code}' -X POST https://api.indexnow.org/indexnow \
  -H 'Content-Type: application/json; charset=utf-8' -d "$body")
echo "IndexNow $host: HTTP $code ($(wc -l <<<"$urls" | tr -d ' ') URLs)"
# 200 and 202 are accepted; 403 means the key file is not served at the host yet.
[[ $code == 200 || $code == 202 ]]
