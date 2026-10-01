# Preview transport checkpoint

This branch is separate from frontend PR #2303. It starts at frontend 4bde9419; frontend follow-up 007c66aa is not copied here. No merge or deployed-image verification is claimed.

## Implemented boundaries

- Hub returns an owned response at RESP, with incremental body iteration. The buffered probe wrapper remains bounded separately.
- Relay owns the handle until the response object is fully constructed. Disconnect cleanup protects bounded cancellation writes from Starlette's canceled AnyIO body scope.
- Helper registers stream ownership before connecting. CLOSE shuts down the socket, interrupting connect/header/body reads; worker capacity is released by worker finalization, not dispatch-time deletion.
- HTTP `read1` forwards available chunks rather than waiting for 64KiB or EOF. Truncated declared lengths produce ERR, not successful END. Content-Length and gzip representation bytes are retained; Transfer-Encoding framing is removed by http.client.
- HTTP/WS admission and WS outboxes are bounded. Tunnel writes, heartbeat/pong writes and app WS writes have deadlines. A helper with unfinished workers does not redial.
- Upgrade headers negotiate supported capabilities. WS close code/reason is interpreted as RFC metadata only on negotiated streams; legacy text/local terminal notices are not decoded as status bytes. Relay finalization does not overwrite a received close with normal 1000.

## Owning verification

Four focused suites passed 37 tests: test_preview_tunnel.py, test_preview_hub.py, test_app_preview_http.py, test_app_preview_ws.py. Ruff checks passed. Logs are outside the product tree.

The two d89 cancellation regressions failed before the fix: actual Starlette StreamingResponse with ASGI2.3 disconnect emitted no CLOSE at a tunnel send checkpoint; cancellation during watcher cleanup left one unowned hub stream. Both now pass. They use real hub/adapter/Starlette classes with controlled ASGI boundaries, not a real network or authentication proof.

Actual independent loopback TCP peers verify headers-stage and body-stage cancellation by observed EOF/reset, closed owned socket, joined worker, done signal and returned capacity. Gated chunked SSE emits its first DATA before the app releases END. A declared-length truncation produces IncompleteRead/ERR. Actual WebSocket peers verify 1013 and UTF-8 reason in both directions; browser-origin close also joins reader/writer threads. Relay tests cover negotiated 1013, invalid 1006 normalization and legacy ASCII close without a spurious normal close.

Independent review archive preview-network-d89-independent-audit.zip was downloaded normally and verified at 479715 bytes, SHA256 ee3dee4be91b82a271121b131226f87ffd52c829197d20c8e73d68a5fbc53064. Reports, owner reproductions and dependency source provenance were read; old O research was not rerun.

## Still required

This checkpoint is not the complete transport acceptance. Actual frozen old/new helper/backend four-combination verification, gzip/HEAD/Range/repeated-query end-to-end cases, content-host authorization integration, quota/slow-writer sibling controls, early-connect/request-write cancellation races and shared tunnel stall teardown remain to verify. Cancellation-task draining, metadata/frame limits and post-cancel DATA races need further hardening. No fixed-instance routing, immutable resource snapshot or controlled runtime-ready protocol is introduced.
