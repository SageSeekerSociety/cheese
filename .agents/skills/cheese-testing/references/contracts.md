# Cheese contract map

Paths are repository-relative. These are starting points to inspect and extend,
not claims that every scenario in a row already has a passing test. Recheck the
assertions and workflow selection when using an entry.

## Boundaries and scenarios

| Changed boundary | Cheese-specific properties and failure cases | Existing entry points |
| --- | --- | --- |
| Backend / connection owner / connector | Keep offline, silent peer, absent socket, rejected credentials, and executor failure distinguishable across serialization. Preserve the device header and machine's error; retire completed calls from the release-drain count. | `backend/tests/contract/test_peer_states.py`; `backend/tests/support/wire.py` |
| Python / Go execution frames | Both real encoders/parsers agree on call, data, and result frames, including binary payloads and absent error fields. | `backend/tests/fixtures/wire/`; `backend/tests/contract/test_wire_frames.py`; `cli/internal/link/frames_test.go` |
| Harness / platform | Shared launch/event vocabulary; hook identity, ordering and pairing; real interactive tool execution. A request-shape test does not establish a complete interactive turn. | `backend/tests/fixtures/harness-contract/`; `backend/tests/contract/test_harness_contract.py`; `backend/tests/extension/harness-contract.test.ts`; `backend/tests/support/contract_harness.py`; `backend/scripts/test_harness_contracts.py`; `cli/e2e/` |
| Native child / parent | Pinned Claude background children receive targeted SendMessage instructions; TaskStop ends only the selected child while its sibling and parent execute real tools. The provider responses are scripted. | `backend/tests/unit/test_claude_child_control.py` |
| Session / work lease | Chat starts without renting hands; the first execution tool acquires its own session lease. Check direct machine selection, authenticated session ownership, project resource authorization, stale generations, background work and unfinished native children, completion of in-flight calls on retained leases, retained unknown outcomes without replay, offline replacement, and retained-resource cleanup. | `backend/tests/integration/test_turn_without_files_rents_no_hands.py`; `backend/tests/integration/test_session_work_leases.py`; `backend/tests/contract/test_dispatch_record_contract.py`; `backend/tests/integration/test_archive_retires_storage.py` |
| Session tools / executor process | Deferred tools read project instructions before mutation. Project MCP calls cross the native transport, owner HTTP boundary, executor socket and project server process; Bash search runs on the work machine. Unsupported native Glob/Grep never run locally. | `backend/tests/unit/test_execution_transport.py`; `backend/tests/unit/test_remote_execution.py`; `scripts/remote_execution/mcp_contract.py` |
| Claude Code headless protocol / driver | Stdin controls, moving a running tool to the background, mid-turn messages, AskUserQuestion reaching the driver while other tools run unattended, subagent transcript layout, `result`/`is_error` turn semantics and the process lifetime, on the pinned and the newest published build. | `scripts/remote_execution/headless_contract.py`; `.github/workflows/mcp-contract.yml` |
| Dispatch / device answer | Distinguish never sent, answered, and outcome unknown. A lost answer is not permission to resend a side effect; a late answer must not overwrite an already settled result. | `backend/tests/contract/test_dispatch_record_contract.py` |
| Event / delivery record | Recipient identity and visibility; replay after interruption; duplicate processing; receipt and completion ordering. Generate additional sequences against the stated delivery guarantee, not an assumed exactly-once guarantee. | `backend/tests/unit/test_delivery_ledger.py`; `backend/tests/integration/test_delivery_trace.py`; `backend/tests/integration/test_topic_event_subscription.py` |
| Transaction / external I/O | While storage or device I/O is blocked, another connection can access the relevant row and another caller can make progress. Exercise business calls with a bounded real pool when testing saturation. | `backend/tests/integration/test_project_environment.py`; `backend/tests/integration/test_central_room_sessions.py`; `backend/tests/integration/test_db_pool_gauge.py`; `backend/tests/unit/test_db_pool_fits_the_server.py` |
| HTTP response / database commit | A device code returned by `/connector/auth/device/start` is visible to a separate request when the client receives the response. Gate the real request session's commit and observe a loopback HTTP response: awaiting an entire `TestClient` or ASGI-transport call can miss this boundary. | `backend/tests/integration/test_connector_device_flow.py::test_device_code_is_committed_before_start_http_response` |
| Reconnect / shared resources | Concurrent recovery stays bounded; failure releases capacity; all eligible devices are accounted for. Include completion identities/counts when claiming full-fleet recovery, not just the maximum concurrency. | `backend/tests/unit/test_recovery_burst_is_bounded.py`; `backend/tests/isolation.py` |
| Project export / offline files | Human membership and private-room scope; offline Git clone, original file names and checksums; missing artifacts and interrupted download cleanup. | `backend/tests/integration/test_project_export.py` |
| Forge / acceptance | Read the forge's result; pending/unknown is not merged. Cover rejection, queue removal, and actual merge completion through the owning acceptance path. | `backend/tests/support/ledger_forge.py`; `backend/tests/contract/test_forge_contract.py`; search acceptance callers when changing queue semantics |
| Release / running execution | Supported backend/owner/connector versions interoperate; schema changes preserve the supported rollout sequence; an in-flight call and established connection survive the business-backend release. | `backend/tests/integration/test_owner_reads_survive_a_column_drop.py`; `backend/tests/unit/test_owner_recreate_connect_retry.py`; `backend/scripts/device_connection_lifecycle_acceptance.py` (`image_owner`, `image_acceptance`); `scripts/remote_execution/owner_fixture.py` (`WireOwner`); `.github/workflows/remote-execution.yml`; `.github/workflows/release-device-connection.yml` |
| Frontend / API / rendered page | Identity-scoped data, authenticated resources, event propagation, visible completion, scrollability and layout across relevant viewports. Use browser geometry for layout promises. | `frontend/src/` adjacent `*.spec.ts`; `e2e/tests/layout-invariants.spec.ts`; `e2e/tests/`; `.claude/rules/frontend.md`; `.claude/rules/e2e.md` |
| CI selection / suite result | Every selected required job actually runs; missing reports, a collapsed selection, and unavailable required dependencies do not count as tested behavior. | `.github/scripts/test_required_ci.py`; `backend/scripts/assert_suite_ran.py`; `backend/tests/unit/test_assert_suite_ran.py`; `.github/workflows/required-ci.yml` |

## Fixtures to reuse

- Backend authentication: `.claude/rules/backend-tests.md` names the route-specific
  helpers. `backend/tests/contract/conftest.py` provides `authed_client` without
  driving the Redis-backed login route; integration fixtures exercise real login
  where required. Pick the helper for the route's identity contract.
- Harness doubles: inspect `backend/tests/support/contract_harness.py`,
  `stand_in_harness.py`, and `fake_pi.py` for the behavior being tested. They have
  different scopes; one does not substitute for all harness tests.
- Browser data and isolation: use the seed/setup in `.github/workflows/e2e.yml`
  and the helpers in `e2e/tests/helpers.ts`. Persistent Redis outlives database
  recreation; negative-login test identities must be unique across runs.

## Limits to check before claiming coverage

- In terminal acceptance, inspect `WireOwner` in
  `scripts/remote_execution/owner_fixture.py` and its shared `image_owner`
  lifecycle in `backend/scripts/device_connection_lifecycle_acceptance.py`.
  The image case upgrades an isolated database before the released owner serves
  current-backend calls. Use the connector packaged with that image: the owner
  automatically replaces a connector whose binary digest differs. The
  source-only smoke substitutes device authentication and uses a Python
  connector harness; it does not verify the released-image boundary.
- Application requests and direct business calls use production-sized
  `QueuePool` engines that are disposed on their owning loop. With the sync
  `client` fixture, run direct business coroutines through `client.portal.call`
  with `client.test_request_factory`. `client.test_factory` is the separate
  `NullPool` handle for setup and inspection that intentionally run on
  short-lived `asyncio.run` loops; do not pass it into application services.

  `backend/tests/integration/test_remote_read_connections.py` already exercises
  forge and machine reads with a one-connection pool, checking another session
  can query during the substituted remote call. Extend that pattern for the
  changed path; it does not establish saturation coverage for every business call.
- Provider request fixtures and fixed model responses establish protocol
  behavior. Real-model output quality belongs in periodic evaluation. Inspect
  the workflow before claiming that such evaluation is scheduled.
- The historical report and issues #1279/#1300 describe both decisions and
  unfinished work. Inspect code and executed checks for implementation status;
  update this map when an entry moves or a limitation is removed.
