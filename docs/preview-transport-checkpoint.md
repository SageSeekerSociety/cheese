# Preview transport checkpoint

Transport PR #2314 merged as 1eb1b355 after exact-head and queue RequiredCI success. The deployment owner verified the normal deploy-dev log for that SHA. The frozen experiments below describe their original checkpoints, not the entire production deployment. The subsequent resource/runtime work is documented in preview-runtime-identity.md.

## Implemented boundaries

- Hub returns an owned response at RESP, with incremental body iteration. The buffered probe wrapper remains bounded separately.
- Relay owns the handle until the response object is fully constructed. Disconnect cleanup owns one deadline-bounded close task through Starlette's canceled AnyIO body scope and direct/repeated asyncio task cancellation, reaps it, then propagates direct cancellation.
- Helper registers stream ownership before connecting. CLOSE shuts down the socket, interrupting connect/header/body reads; worker capacity is released by worker finalization, not dispatch-time deletion.
- HTTP `read1` forwards available chunks rather than waiting for 64KiB or EOF. Truncated declared lengths produce ERR, not successful END. Content-Length and gzip representation bytes are retained; Transfer-Encoding framing is removed by http.client.
- HTTP/WS admission and WS outboxes are bounded. Tunnel writes, heartbeat/pong writes and app WS writes have deadlines. A helper with unfinished workers does not redial.
- Upgrade headers negotiate supported capabilities. WS close code/reason is interpreted as RFC metadata only on negotiated streams; legacy text/local terminal notices are not decoded as status bytes. Relay finalization does not overwrite a received close with normal 1000.

## Owning verification

The original four focused suites passed 37 tests: test_preview_tunnel.py, test_preview_hub.py, test_app_preview_http.py, test_app_preview_ws.py. Four additional test_preview_transport_live.py cases passed over actual Uvicorn, viewer TCP, tunnel WS and independent app TCP sockets. Ruff checks passed. Logs are outside the product tree.

The live suite verifies cancellation before headers, after headers and after the first SSE chunk; each independent app peer observes EOF/reset and the helper socket closes, worker joins and capacity returns. Headers and the first chunk arrive while the app still withholds END. A process-only negative control replaces read1 with buffered read: the first-chunk case fails at browser recv timeout (1 failed, exit 1), rather than during setup. The other live case checks gzip representation bytes/encoding, HEAD length 12345, 206/416 Content-Range and repeated escaped query parameters. The initial 9c68 case only forced 206/416 responses; the follow-up sends actual Range bytes=2-4 and bytes=99-100 requests and requires the independent app to observe each header before answering. These transport cases invoke relay after an explicit local viewer authorization boundary; they do not prove production grant/cookie authorization.

The two d89 cancellation regressions failed before the fix: actual Starlette StreamingResponse with ASGI2.3 disconnect emitted no CLOSE at a tunnel send checkpoint; cancellation during watcher cleanup left one unowned hub stream. Both now pass. They use real hub/adapter/Starlette classes with controlled ASGI boundaries, not a real network or authentication proof. A later direct task.cancel during the close write also reproduced a missing CLOSE before the owned-close-task fix (1 failed, exit 1). Its regression requires accepted CLOSE before propagating cancellation; a second regression applies repeated cancellation and a stalled write, requiring one bounded close task to finish with no duplicate write.

Actual independent loopback TCP peers verify headers-stage and body-stage cancellation by observed EOF/reset, closed owned socket, joined worker, done signal and returned capacity. Gated chunked SSE emits its first DATA before the app releases END. A declared-length truncation produces IncompleteRead/ERR. Actual WebSocket peers verify 1013 and UTF-8 reason in both directions; browser-origin close also joins reader/writer threads. Relay tests cover negotiated 1013, invalid 1006 normalization and legacy ASCII close without a spurious normal close.

Independent review archive preview-network-d89-independent-audit.zip was downloaded normally and verified at 479715 bytes, SHA256 ee3dee4be91b82a271121b131226f87ffd52c829197d20c8e73d68a5fbc53064. Reports, owner reproductions and dependency source provenance were read; old O research was not rerun.

## Frozen four-combination evidence

Complete helper, hub and route modules were exported from old 4bde9419291525000873c26ccdd8513c850fd683 and new 2091b01d8e45c8ac09e04b5f2a2b691d6a718ae7, loaded without AST extraction and exercised through actual loopback sockets. The external harness and original logs/results are retained outside this tree. Other authentication dependencies are current installed modules, not two complete historical deployments.

All four HTTP pairs preserve the tested gzip body bytes, 206/416 Content-Range and repeated escaped query. Only new/new restores both gzip Content-Encoding and HEAD representation length. Old helper drops Content-Length; old relay drops Content-Encoding and reports HEAD length zero. Mixed versions cannot repair the other endpoint's loss.

Eight WS direction cases completed with exit 0, recording legacy degradation rather than asserting it fixed. New/new preserves 1013 with Chinese reason in both directions. New helper workers/writers and sockets exit in both backend combinations. Old helper browser-origin close leaves the app awaiting input until the peer's five-second timeout; old backend reports upstream closure as normal 1000. New backend with old helper reports unknown upstream closure as 1011. All tested WS pairs preserve the repeated escaped query and text echo. Production content-host authorization is bypassed at the local viewer boundary.

The repository live suite is under tests/unit and receives its pure layer marker from tests/conftest.py because its fixture closure has no DB fixtures. The existing test workflow runs tests/ with the selected TEST_LAYER, including pure. At f44b02c3, RequiredCI 36810266243's pure job passed, but backend lint failed on two helper typing errors; that run is not CI success for the PR.

## Resource-bound verification

Four test_preview_transport_bounds.py cases passed against independent raw TCP app peers. Sixteen pending HTTP streams reject the seventeenth and admit a replacement after cancel; sixteen WS streams separately reject the seventeenth while an HTTP sibling succeeds, then admit a WS replacement after cancel. Canceled app peers observe EOF and owned workers/writers join. A non-reading app WS peer receives a bounded outbox flood while an HTTP sibling still completes; after release the app observes EOF and reader/writer exit. A non-reading shared tunnel with a reduced socket send buffer and a shortened deadline verifies timer teardown: the actual tunnel and app sockets close, and the helper session/worker join. The author logs do not observe native sendall entry or establish kernel write blocking. An independent Windows run entered native sendall with fd=-1 and duration zero because the timer had already closed the socket during Python masking; this does not establish the same timing in the author's Linux run. The test does not prove kernel write blocking, real WAN timing or all admission races.

## Final transport boundaries

The final 501ad3d9 checkpoint adds production content-host grant/cookie/database integration, gated native connect/request-body cancellation, metadata/request/WS bounds, canceled queued DATA suppression and shared-send failure stopping admission and closing the idle tunnel. The final targeted set passed 63 cases; the production authorization set passed 67 cases at b8d459e3. These observations do not establish kernel-blocked writes or real WAN timing. Already-started frames remain in flight. Fixed resource routing and runtime readiness belong to the subsequent checkpoint; immutable resource closure remains unimplemented.
