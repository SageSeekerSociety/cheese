#!/bin/bash
# 起浏览器、跑一轮测量、关掉，都在一次调用里完成（后台进程不跨命令存活）。
set -u
CHROME=${CHROME:?set CHROME to the browser binary}
PORT=${PORT:-9333}
PROFILE=${PROFILE:-/var/tmp/chromeprof}
rm -rf "$PROFILE"
"$CHROME" --no-sandbox --headless=new --disable-gpu --disable-dev-shm-usage \
  --disable-features=Translate,OptimizationHints,MediaRouter,BackgroundNetworking \
  --disable-background-networking --no-first-run --no-default-browser-check \
  --remote-debugging-port="$PORT" --remote-allow-origins='*' \
  --user-data-dir="$PROFILE" about:blank > /var/tmp/chrome.log 2>&1 &
CPID=$!
for _ in $(seq 1 30); do
  curl -s -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 && break
  sleep 1
done
if ! curl -s -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "browser did not come up"; tail -5 /var/tmp/chrome.log; exit 1
fi
CDP_PORT="$PORT" uv run --with websocket-client python measure.py "$@"
RC=$?
kill $CPID 2>/dev/null
exit $RC
