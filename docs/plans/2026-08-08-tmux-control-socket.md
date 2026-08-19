# Verifiable Prompt Delivery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A prompt sent to 芝士 either arrives and is confirmed, or fails loudly within seconds — never the current third outcome of silent non-delivery discovered at the 900-second ceiling.

**Architecture:** One long-lived `tmux -C` control client per topic container replaces per-keystroke `docker exec` spawns, giving structured command receipts. Liveness is checked before each send because tmux reports success into a dead pane. Consumption is confirmed by Claude Code's `UserPromptSubmit` hook, which fires for pasted input and carries a unique `prompt_id`. Failure escalates through a fixed ladder and ends in a visible platform event.

**Tech Stack:** Python/FastAPI, asyncio subprocess pipes, tmux control mode, Claude Code hooks, pytest.

---

### Task 1: Specify the control client against a real tmux

**Files:**
- Create: `backend/tests/unit/test_tmux_control.py`

**Steps:**

1. Write cases that start a real tmux server on a scratch socket: a command that succeeds returns ok, a command naming a missing pane returns the tmux reason, and a send into a pane whose process was killed still returns ok (the property that forces the liveness check).
2. Add a case asserting `%output` from the pane reaches a subscriber.
3. Skip the module when `tmux` is absent so CI without tmux stays green.
4. Run the focused tests and confirm they fail before implementation.

### Task 2: Implement the control client

**Files:**
- Create: `backend/app/domain/agent/tmux_control.py`

**Steps:**

1. Implement an async client that owns one `tmux -C attach` process, parses `%begin`/`%end`/`%error`/`%output`, and correlates replies by tmux's command id.
2. Expose `send(*args) -> CommandResult(ok, lines, error)` and an `%output` subscription hook.
3. Reconnect on a dropped connection; raise a typed error when reconnection fails so the caller can report a screen failure.
4. Run Task 1's tests to green.

### Task 3: Specify delivery at the provider boundary

**Files:**
- Modify: `backend/tests/unit/test_tmux_provider.py`

**Steps:**

1. With a fake control client, assert a `%error` on paste aborts the send instead of proceeding to Enter.
2. Assert a dead pane (`pane_dead=1`) recreates the session rather than sending into it.
3. Assert the escalation order on a missing receipt: extra Enter, then session recreate + resend, then a platform event.
4. Confirm the new cases fail before implementation.

### Task 4: Wire the receipt

**Files:**
- Modify: `backend/app/domain/agent/harness/claude_code/hooks_substrate.py`
- Modify: `backend/app/domain/agent/harness/claude_code/hook_events.py`

**Steps:**

1. Register `UserPromptSubmit` in `hooks_settings` so both backends emit it.
2. Translate the event into an internal delivery receipt keyed by `prompt_id` and prompt text; it is a receipt, not a 现场 event, so it must not render as a tool row.
3. Add unit coverage for the translation.

### Task 5: Replace the send path

**Files:**
- Modify: `backend/app/domain/agent/tmux_provider.py`

**Steps:**

1. Replace `_send_prompt`'s three discarded-return-code execs with control-client calls that check each result.
2. Add the `#{pane_dead}` precheck ahead of the paste.
3. Await the receipt with a bounded window; implement the escalation ladder.
4. Post the platform event when the ladder is exhausted, reusing the existing platform-failure event shape.
5. Run Task 3's tests to green.

### Task 6: Verify on dev

**Files:**
- None (deployment verification)

**Steps:**

1. Deploy to the dev box and run a turn on a local sandbox topic and on the MicroCloud machine topic.
2. Kill the pane mid-turn and confirm the failure surfaces in seconds as an event, not as a 900-second timeout.
3. Record both outcomes in the PR description.
