# Etrip Runtime Hardening Implementation Plan

> Execute against the isolated `fix/etrip-runtime-hardening` worktree, then apply the compatible subset directly to the Etrip host from a timestamped backup.

**Goal:** Remove the root-command path, stop continuous automatic agent patrols, make project navigation resilient to transient failures, and add local health-based recovery.

**Architecture:** Authorize quality-gate writes at the API boundary, execute checks in disposable constrained containers, separate container reaping from AI scheduling, cache/retry project reads in the browser, and add systemd health monitoring around the existing bare-metal Uvicorn service.

---

### Task 1: Lock quality-gate configuration to project human administrators

**Files:** `backend/app/api/routes/projects.py`, `backend/tests/integration/test_accept_gate.py`, `backend/tests/integration/test_accept_approvals.py`

1. Add a dependency that resolves only verified human session handles.
2. Allow project owners and `lead` members; conceal all other projects with 404.
3. Reject oversized or NUL-containing commands.
4. Add owner/lead/member/anonymous/agent authorization tests.

### Task 2: Move gate commands off the host

**Files:** `backend/app/core/config.py`, `backend/app/domain/workspace/service.py`, `backend/tests/unit/test_gate_command.py`, `backend/tests/integration/test_accept_gate.py`

1. Add explicit gate image and resource-limit settings.
2. Replace host `sh -lc` with constrained `docker run` argv and streamed logging.
3. Force-remove the exact container on timeout; never fall back to host execution.
4. Mock the process boundary in unit tests and use a deterministic test runner in integration tests.

### Task 3: Separate maintenance from AI patrols

**Files:** `backend/app/core/config.py`, `backend/app/domain/scheduler/service.py`, `backend/app/main.py`, `backend/tests/unit/test_idle_reap.py`

1. Remove reaping from the heartbeat tick.
2. Add a dedicated idle-sandbox runner with its own interval and idle threshold.
3. Start and stop it independently in application lifespan.
4. Verify it runs with heartbeat scheduling disabled.

### Task 4: Make project navigation stale-while-revalidate

**Files:** `frontend/src/api.ts`, `frontend/src/App.vue`, `frontend/src/lib/projectCache.ts`, frontend unit tests

1. Retry idempotent GETs on network failures and 502/503/504.
2. Cache the last successful project list in user-scoped session storage.
3. Initialize from cache and never clear a valid list on refresh failure.
4. Test cache isolation and retry decisions.

### Task 5: Apply, recover, and verify Etrip

**Files:** compatible files under `/opt/cheesex`, `/etc/systemd/system/cheesex-healthcheck.*`, `/usr/local/sbin/cheesex-healthcheck`

1. Check for active turns and record the current service state.
2. Back up the exact live files and frontend dist.
3. disable automatic heartbeat scheduling and install compatible source changes.
4. Build the frontend, restart once, and install the three-strike health timer.
5. Verify local and public routes, authorization, scheduler/timer state, repeated-request reliability, logs, and rollback artifacts.

### Task 6: Full repository verification and durable handoff

1. Run focused backend/frontend tests.
2. Run repository quality checks.
3. Review the diff for secrets and unrelated changes.
4. Commit the isolated branch and publish it through a pull request without using a runner for deployment.
