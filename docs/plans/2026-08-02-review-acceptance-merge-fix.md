# Review Acceptance Merge Safety Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent 采纳 from accepting and archiving a topic when its workspace merge did not complete.

**Architecture:** Make the workspace result distinguish explicit no-op outcomes from merge failures. Keep the existing conflict state for failures with actual conflicted paths; raise a client-visible validation error for exceptions, empty-conflict failures, or malformed false results so FastAPI's session dependency rolls back the vote and leaves the card pending/topic active.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async sessions, pytest.

---

### Task 1: Prove the acceptance state machine lies

**Files:**

- Create: `backend/tests/unit/test_review_acceptance_merge_failure.py`
- Modify: `backend/tests/integration/test_accept.py`

**Step 1: Add focused service regressions**

Inject `merge_topic()` raising `RuntimeError` and returning
`{"merged": false, "conflicts": []}`. In both cases require `ValidationError`, a
pending card, and an active topic.

**Step 2: Run the tests before implementation**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/test_review_acceptance_merge_failure.py -q
```

Expected pre-fix result: both failure cases fail because `accept()` returns an
accepted card and archives the topic.

### Task 2: Introduce an explicit merge-result contract

**Files:**

- Modify: `backend/app/domain/workspace/service.py`
- Modify: `backend/app/domain/review/services.py`

**Step 1: Label intentional no-op results**

Return `noop: true` only for `no topic branch` and `topic is the base branch`.
Do not infer no-op status from a reason string.

**Step 2: Fail all unacknowledged non-merges**

Log unexpected merge exceptions/results with project/topic identity and raise a
stable English `ValidationError` telling the caller the card remains pending and
can be retried. Treat presence of `conflicts` as failure even when the list is
empty; retain the existing `conflict` card flow only when paths are present.

### Task 3: Prove rollback and compatibility

**Files:**

- Modify: `backend/tests/unit/test_review_acceptance_merge_failure.py`
- Modify: `backend/tests/integration/test_accept.py`

**Step 1: Cover legitimate callers**

Require each explicit no-op outcome to accept/archive normally. The existing
happy path exercises the real no-topic-branch case.

**Step 2: Cover request transaction rollback**

At the HTTP boundary, inject a merge exception and require 422. Fetch the card
and topic afterward and require pending/active plus no persisted approval.

**Step 3: Verify and checkpoint**

Run the focused unit/integration tests, then
`bash .claude/scripts/check.sh`. Record exact output in `tmp/audit/FIXES.md` and
commit only this finding on `fix/audit-review-adoption`; if `.git` is still
read-only, record that no branch/commit was created.
