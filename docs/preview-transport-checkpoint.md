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

The original four focused suites passed 37 tests: test_preview_tunnel.py, test_preview_hub.py, test_app_preview_http.py, test_app_preview_ws.py. Four additional test_preview_transport_live.py cases passed over actual Uvicorn, viewer TCP, tunnel WS and independent app TCP sockets. Ruff checks passed. Logs are outside the product tree.

The live suite verifies cancellation before headers, after headers and after the first SSE chunk; each independent app peer observes EOF/reset and the helper socket closes, worker joins and capacity returns. Headers and the first chunk arrive while the app still withholds END. A process-only negative control replaces read1 with buffered read: the first-chunk case fails at browser recv timeout (1 failed, exit 1), rather than during setup. The other live case checks gzip representation bytes/encoding, HEAD length 12345, 206/416 Content-Range and repeated escaped query parameters. These transport cases invoke relay after an explicit local viewer authorization boundary; they do not prove production grant/cookie authorization.

The two d89 cancellation regressions failed before the fix: actual Starlette StreamingResponse with ASGI2.3 disconnect emitted no CLOSE at a tunnel send checkpoint; cancellation during watcher cleanup left one unowned hub stream. Both now pass. They use real hub/adapter/Starlette classes with controlled ASGI boundaries, not a real network or authentication proof.

Actual independent loopback TCP peers verify headers-stage and body-stage cancellation by observed EOF/reset, closed owned socket, joined worker, done signal and returned capacity. Gated chunked SSE emits its first DATA before the app releases END. A declared-length truncation produces IncompleteRead/ERR. Actual WebSocket peers verify 1013 and UTF-8 reason in both directions; browser-origin close also joins reader/writer threads. Relay tests cover negotiated 1013, invalid 1006 normalization and legacy ASCII close without a spurious normal close.

Independent review archive preview-network-d89-independent-audit.zip was downloaded normally and verified at 479715 bytes, SHA256 ee3dee4be91b82a271121b131226f87ffd52c829197d20c8e73d68a5fbc53064. Reports, owner reproductions and dependency source provenance were read; old O research was not rerun.

## Frozen four-combination evidence

Complete helper, hub and route modules were exported from old 4bde9419291525000873c26ccdd8513c850fd683 and new 2091b01d8e45c8ac09e04b5f2a2b691d6a718ae7, loaded without AST extraction and exercised through actual loopback sockets. The external harness and original logs/results are retained outside this tree. Other authentication dependencies are current installed modules, not two complete historical deployments.

All four HTTP pairs preserve the tested gzip body bytes, 206/416 Content-Range and repeated escaped query. Only new/new restores both gzip Content-Encoding and HEAD representation length. Old helper drops Content-Length; old relay drops Content-Encoding and reports HEAD length zero. Mixed versions cannot repair the other endpoint's loss.

Eight WS direction cases completed with exit 0, recording legacy degradation rather than asserting it fixed. New/new preserves 1013 with Chinese reason in both directions. New helper workers/writers and sockets exit in both backend combinations. Old helper browser-origin close leaves the app awaiting input until the peer's five-second timeout; old backend reports upstream closure as normal 1000. New backend with old helper reports unknown upstream closure as 1011. All tested WS pairs preserve the repeated escaped query and text echo. Production content-host authorization is bypassed at the local viewer boundary.

The repository live suite is under tests/unit and receives its pure layer marker from tests/conftest.py because its fixture closure has no DB fixtures. The existing test workflow runs tests/ with the selected TEST_LAYER, including pure; no workflow execution is claimed for this unpublished follow-up.

## Still required

This checkpoint is not complete transport acceptance. Content-host authorization integration, quota/slow-writer sibling controls, early-connect/request-write cancellation races and shared tunnel stall teardown remain to verify. Cancellation-task draining, metadata/frame limits and post-cancel DATA races need further hardening. No fixed-instance routing, immutable resource snapshot or controlled runtime-ready protocol is introduced.
