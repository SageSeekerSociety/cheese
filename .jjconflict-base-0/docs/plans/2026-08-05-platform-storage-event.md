# Platform Storage Event Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn filesystem exhaustion into a structured, persistent platform incident with a clear frontend recovery card.

**Architecture:** A shared backend classifier maps ENOSPC exceptions or provider text to a stable platform-event payload. Existing event blocks carry that metadata over persistence and WebSocket replay; a small frontend presenter selects a dedicated ChatPanel card while preserving the plain event fallback.

**Tech Stack:** Python/FastAPI/SQLAlchemy, pytest, Vue 3/TypeScript, Vitest, scoped CSS.

---

### Task 1: Specify backend classification

**Files:**
- Create: `backend/tests/unit/test_platform_failures.py`
- Modify: `backend/tests/integration/test_chat_realtime.py`

**Steps:**

1. Write unit cases for ENOSPC exception chains/text and a negative control.
2. Write an integration case whose provider returns the exact tmux ENOSPC shape.
3. Assert one provider call, no raw path in user copy, structured event metadata, and a coded terminal error frame.
4. Run the focused tests and confirm they fail before implementation.

### Task 2: Implement the backend event

**Files:**
- Create: `backend/app/domain/agent/platform_failures.py`
- Modify: `backend/app/domain/agent/chat.py`
- Modify: `backend/app/domain/agent/runtime.py`

**Steps:**

1. Add a conservative ENOSPC classifier and stable storage-event payload.
2. Exclude storage exhaustion from transient provider retries.
3. Persist metadata and emit the stable code for provider failures.
4. Extend `post_system_event` with optional metadata and classify runtime exceptions.
5. Do not schedule automatic continuation for storage exhaustion.
6. Run focused pytest, Ruff, and Pyright.

### Task 3: Specify and implement frontend presentation

**Files:**
- Create: `frontend/src/lib/platformEvents.ts`
- Create: `frontend/src/lib/platformEvents.spec.ts`
- Modify: `frontend/src/cx_types.ts`
- Modify: `frontend/src/components/ChatPanel.vue`

**Steps:**

1. Write presentation-helper tests for structured, unknown, and ordinary events.
2. Add typed event metadata and a fail-safe presentation helper.
3. Render the platform incident card before normal event rows.
4. Add accessible industrial-warning styling using existing design tokens.
5. Run Vitest, targeted ESLint, type checking as far as the repository baseline permits, and a production build.

### Task 4: Direct dev deployment and regression

**Files:**
- Deploy the changed backend/frontend files directly to `cheese-dev-env1-app`.

**Steps:**

1. Back up the exact live files and frontend dist.
2. Patch/build without GitHub Runner and restart only after active turns reach zero.
3. Create a controlled structured storage event in a test topic or verify the classifier/payload inside the live backend.
4. Verify backend health, frontend asset content, event persistence shape, and no new ENOSPC logs.
5. Commit with `[skip ci]`, push the existing branch, and confirm no workflow starts.
