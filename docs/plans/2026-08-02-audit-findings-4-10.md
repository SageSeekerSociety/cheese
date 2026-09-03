# Audit Findings 4–10 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Ship each remaining effect-verification audit finding as one tested local commit on its own branch from `origin/main`.

**Architecture:** Keep durable state unchanged whenever an external effect is unknown or failed, and make the failure visible to the caller or deployment check. Each finding is isolated on a fresh branch; its regression test is first run against the vulnerable code, then against the fix, followed by the repository check script.

**Tech Stack:** Python 3.13, FastAPI, httpx, Redis, Docker Compose, Bash, pytest, uv.

---

### Task 1: Preserve gateway usage checkpoints on read failure

**Files:**
- Modify: `backend/app/domain/agent/gateway.py`
- Modify: `backend/app/domain/agent/chat.py`
- Test: `backend/tests/unit/test_gateway.py`

1. Add a MockTransport regression that starts from a non-zero checkpoint, returns HTTP 500, then recovers at a higher cumulative total. Keep the caller's old checkpoint when the failed drain has no authoritative result and assert the recovered delta is only 50 prompt, 10 completion, and $0.005.
2. Run `cd backend && uv run pytest tests/unit/test_gateway.py::test_failed_read_cannot_reset_checkpoint_and_rebill_prior_spend -q`; expect the vulnerable code to report 150 prompt and 30 completion.
3. Make `daily_spend()` return `None` on failed/invalid reads, propagate that unknown result through `drain_new_usage()`, and have `_drain_gateway_usage()` retry without committing before returning no usage.
4. Run the focused test and all gateway tests; expect PASS.
5. Run `bash .claude/scripts/check.sh`, append its literal result to `tmp/audit/FIXES.md`, then commit on `fix/audit-usage-checkpoint`.

### Task 2: Fail closed when a project virtual key cannot be provisioned

**Files:**
- Modify: `backend/app/domain/agent/chat.py`
- Test: `backend/tests/unit/test_gateway_fail_closed.py`

1. Add a model-call boundary test whose pool profile resolves normally but `_gateway_project_env()` returns `None`; assert `_model_kwargs()` raises a visible service-unavailable error rather than returning shared credentials with `routed=True`. Add compatibility cases for gateway-disabled, non-pool profiles, and an existing virtual key.
2. Run the mint-failure case against `origin/main`; expect `DID NOT RAISE` and shared `ANTHROPIC_AUTH_TOKEN` in the returned env.
3. Require a non-empty project-key override before returning gateway-routed kwargs. Raise `SystemBusyError` when the configured pool gateway cannot establish project scope.
4. Run the focused file and `bash .claude/scripts/check.sh`, record output, and commit on `fix/audit-virtual-key-fail-closed`.

### Task 3: Acknowledge notification email only after SMTP success

**Files:**
- Modify: `backend/app/domain/notification/maintenance.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/sample.env`
- Test: `backend/tests/unit/test_email_queue_task.py`

1. Add a one-item Redis/session/sender regression in which `send()` returns `False`; assert `processed == 0` and the item remains retryable. Add success and retry-limit/dead-letter cases.
2. Run the SMTP-failure case against `origin/main`; expect `processed == 1` and an empty source queue.
3. Claim each item into a processing list, remove it only after successful delivery, and move failed items back with a bounded retry counter or to a dead-letter list after the configured maximum. Count only successful sends as processed.
4. Run the focused tests and `bash .claude/scripts/check.sh`, record output, and commit on `fix/audit-email-retry`.

### Task 4: Require both backend and frontend before deploy/drift success

**Files:**
- Modify: `deploy/deploy-docker.sh`
- Modify: `.github/workflows/deploy-drift.yml`
- Modify: `scripts/whats-live.sh`
- Create: `deploy/check-app-tier.sh`
- Create: `deploy/tests/test-app-tier-health.sh`

1. Add fake-Docker/SSH fixtures for healthy-current backend with absent frontend and current backend with stale frontend. Invoke the real deploy and operator paths; expect the vulnerable scripts to print success.
2. Centralize validation of exactly one backend and frontend container, exact image tag, and healthy status. Make deploy probe both service endpoints before success and make both drift callers use the same predicate.
3. Add healthy/current compatibility fixtures, run all shell cases and `bash .claude/scripts/check.sh`, record output, and commit on `fix/audit-app-tier-health`.

### Task 5: Retain attachment metadata when object deletion fails

**Files:**
- Modify: `backend/app/core/storage.py`
- Modify: `backend/app/domain/attachment/services.py`
- Test: `backend/tests/unit/test_attachment_service.py`

1. Add a service regression where storage returns `False`; assert `SystemBusyError` and no repository delete. Run it on `origin/main`; expect no exception and `repo.delete(1)`.
2. Treat a false storage effect as retryable service unavailability and retain the row. Make local deletion idempotently return success when the file is already absent, while an S3 exception remains failure.
3. Run failure, success, no-storage-key, and absent-local-object tests plus `bash .claude/scripts/check.sh`; record output and commit on `fix/audit-attachment-delete`.

### Task 6: Complete the machine LLM proxy boundary actually used by a turn

**Files:**
- Modify: `backend/app/domain/agent/device_provider.py`
- Create: `backend/app/api/routes/llm_proxy.py`
- Test: `backend/tests/unit/test_device_provider.py`
- Test: `backend/tests/integration/test_llm_proxy.py`

1. Pass a realistic profile env containing an upstream URL/key into `_ensure_screen()` and assert the captured machine env still contains only `{public_base}/api/llm` and the scoped token. Assert the route authenticates the bearer/x-api-key token by its signed project claim and never exposes admin paths.
2. Run against `origin/main`; expect the profile env to overwrite the proxy URL/key and route requests to return 404.
3. Reverse env precedence so backend proxy identity wins. Add the allowlisted Anthropic proxy route, derive project identity from `scoped_token_claims`, mint/use only that project's virtual gateway key, preserve streaming response semantics, and fail closed when project-key acquisition fails.
4. Run route/device compatibility tests and `bash .claude/scripts/check.sh`; record output and commit on `fix/audit-machine-llm-proxy`.

### Task 7: Final audit

1. Confirm every branch tip is based directly on `origin/main`, contains exactly one new commit, and has no unrelated tracked changes.
2. Confirm every entry in `tmp/audit/FIXES.md` contains branch, commit, pre-fix failure output, post-fix output, full check output, and legitimate-caller checks.
3. Do not push or open pull requests.
