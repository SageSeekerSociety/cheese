# Development fixture failures

These are excerpts and original session-output references, not complete stdout copies or fixed-source negative controls. Both ran with an uncommitted new fixture on base `4d994bc9`. The tool-output files are on the central session filesystem; the work-machine shell cannot read them. Original raw output remains in the session transcript.

## Actor mismatch

Output task `bl60dzdb8`, 2026-10-01 02:13 UTC:

```text
message = {'type': 'websocket.close', 'code': 1008, 'reason': ''}
E           starlette.websockets.WebSocketDisconnect
2026-10-01 02:13:00 topic_access_denied handle=alice
1 failed, 3 warnings in 2.97s
```

The fixture created the project under default owner but opened chat as alice. It now explicitly creates the project with `owner_handle=alice`; no authorization check changed.

## Incorrect listening-stop fixture call

Output task `bcqqnbwd5`, 2026-10-01 02:14 UTC:

```text
E           TypeError: ChatService.stop_listening() missing 1 required positional argument: 'timeout_s'
```

The fixture now releases only its isolated runtime readers with `before.runtime.stop_listening`, following the existing recovery fixtures. It does not stop the retained scripted runner. No shared session/service is stopped.

Raw files are under the central session task-output directory named by these IDs. Neither failure is evidence of a product defect, completion fault, native binary behavior or protocol upgrade.
