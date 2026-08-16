# Topic event subscription — retire the per-turn hook queue

Status: design, not yet implemented. Base branch: `fix/turn-lock-merge-into-running` (PR #469).

## The defect this fixes

The platform subscribes to a topic's Claude Code event stream **for the duration of one
turn**. `HooksTurnProvider.run_turn` registers a queue with `HookRouter` when it starts and
unregisters it when it sees the `Stop` hook. Everything wrong below follows from that one
choice:

1. One queue per topic id means one turn per topic. `HookRouter.register` says so:
   "Turns are serialized per topic, so one queue per topic is sufficient." A second
   `register` on a live topic silently replaces the first turn's queue — that turn then
   starves to its idle timeout while the second turn ends early on the first one's `Stop`.
2. So `ChatService` holds a per-topic `asyncio.Lock` across the whole turn to keep that from
   happening — which is why a message posted during a long turn used to wait the turn out.
   PR #469 papered over the symptom by injecting into the running turn; the cause is here.
3. Between turns nothing is subscribed. Every hook that arrives in that window misses the
   queue and falls to the disk spool, to be reconciled later, out of order, attributed to no
   turn.

Window (3) is not an edge case. Measured on Claude Code 2.1.233, these all land in it:

- **The session starts its own turn.** A backgrounded command finishes, Claude Code wakes on
  the completion notification and runs a turn the platform never asked for. Observed
  directly: platform prompt → session backgrounds `sleep 45` → answers → `Stop` (platform
  unregisters) → 45s later the session runs a second turn on its own and emits a second
  `Stop`. The whole second turn is unsubscribed.
- **The platform gave up but the session did not.** `DELIVERY_TIMEOUT_S` (25s) or the
  idle-suspect path ends the platform's turn while the container's `claude` is merely slow
  to boot. It then works normally, unheard.
- **Late hooks.** `run_turn` returns on `Stop` and unregisters immediately; a `MessageDisplay`
  delayed on the network arrives after.
- **Backend restart.** `HookRouter._queues` is process memory. The screens outlive it.

The spool + `settle_spool` reconcile machinery exists **only** to paper over window (3). It
is a backstop for a hole this design creates.

## Target model

**A topic's subscription lives as long as the topic's screen, not as long as a turn.**

- `HookRouter` holds a per-topic sink that is created when the topic first gets a live screen
  and torn down when that screen goes away.
- One long-lived consumer task per topic drains that sink continuously: hook → `AgentEvent` →
  persisted block → broker frame. It does not stop at `Stop`.
- A **turn** stops being a subscription lifetime and becomes an *interval marker* on that
  stream: it opens when the platform injects a prompt, closes on the matching `Stop`, and
  exists only to attribute blocks and to bound retries/timeouts.
- The per-topic turn lock is deleted. Nothing needs serializing once two turns cannot fight
  over one queue.

What this buys, concretely: all four bullets above stop being holes. The session's own turns
get shown. A slow-booting container is not declared dead. Late hooks land in order. A restart
reattaches instead of losing the stream.

## Already true — do not rebuild these

Check each before writing code; the refactor is smaller than it looks because of them.

- **Frames already reach clients without a request.** `TurnRunner.submit` runs `converse` as a
  background task and publishes frames to `InProcessBroker`; WebSocket connections just
  subscribe (`runtime.py`). So a consumer that publishes to the broker needs no route change.
- **Hooks already carry an event id** (`_eid`, set by `cheese-hook`) and are already spooled
  to disk in the container *before* the HTTP POST. Durability exists; ordering and
  attribution are what is missing.
- **`deliver` already exists** (PR #469, `HooksTurnProvider.deliver`) as the primitive that
  injects text into a live screen. Keep it. What goes away is `converse`'s
  `if lock.locked()` branch — in the target model every message injects, unconditionally.

## The six decisions

A subagent will get these wrong by default. They are decided; do not re-litigate them.

### D1 — Who owns the consumer's lifetime

The **provider** does, keyed by topic id, next to the `_live` screen map PR #469 added.
`HooksTurnProvider` gains `ensure_subscription(project_id, topic_id)` / `drop_subscription(topic_id)`:

- `_ensure_ready` calls `ensure_subscription` right after the screen is confirmed.
- `drop_subscription` is called where the screen is known dead — `TmuxHooksProvider.drop_control`,
  device disconnect, container removal.
- The consumer task must NOT be tied to `run_turn`'s `finally`. That is the bug.

### D2 — Turn attribution

The consumer holds a nullable `current_turn: TurnMark | None` per topic.

- The platform opening a turn (injecting a prompt) sets it.
- `Stop` closes it and clears the pointer.
- **Hooks arriving with no open turn are not dropped and not spooled.** They open an
  *unattributed* turn with a freshly generated `turn_id`, marked in block meta as
  platform-unsolicited. This is how a session's own turn becomes visible instead of vanishing
  — the single biggest user-facing win, and the reason for the whole refactor.

### D3 — What `converse` becomes

`converse` keeps posting the human block and acking the summon. Then it injects and returns.
It no longer owns the agent stream — the consumer does. Concretely:

- delete the `_topic_locks` / `_lock_for` machinery and every `async with self._lock_for(...)`
- `_converse_impl`'s prompt assembly (history, pending blocks, memories, system prompt) stays,
  but it feeds the injection rather than a `run_turn` call
- the `consumed` stamp moves to turn close, driven by the consumer

Keep `_pending_human_blocks`'s existing semantics exactly: stamp on turn *completion*, never
on prompt build, so a turn that dies leaves messages pending. That rule is load-bearing and
was itself a bug fix.

### D4 — Timeouts

`run_hooks_turn`'s two-layer policy (`idle_suspect_s`, `hard_ceiling_s`) and its 25s delivery
check move onto the turn marker, not the subscription. The subscription has no timeout — it
ends when the screen ends. `ActivityTracker` and `_confirm_alive` keep working; they are
already per-screen, not per-turn.

Note the delivery check changes meaning: today "no hook in 25s" means the prompt never
arrived. With a long-lived subscription, `SessionStart`-class traffic can already have been
seen. Bound it on "no hook attributable to THIS turn", not "no hook at all".

### D5 — Idempotency

The consumer must be safe to run twice over the same `_eid` (restart replay, double POST).
Block persistence keys on `_eid`; a second sighting is a no-op, not a duplicate block. The
existing backfill path already dedupes by eid — reuse that, do not invent a second scheme.

### D6 — Restart recovery

On startup, for each topic whose screen is still live (container running / device online),
`ensure_subscription` and replay the topic's spool from the last persisted `_eid` forward.
The spool stops being the primary path for window (3) and becomes what it should have been:
a crash-recovery log.

## Non-goals

Do not touch, this round: the tmux→inbox-socket injection change (separate, PR #469 discussion);
`--channels`; the `❯` readiness gate; anything in `frontend/` beyond what a changed frame
shape forces; `RemoteCheesedProvider`'s node protocol.

## Phasing — one commit per phase, tests in the same commit

Each phase must leave the suite green and the product working. Do not batch.

- **P0 — characterization.** Tests that pin today's observable behaviour: a turn's blocks get
  its `turn_id`; a hook with no live turn reaches the room via the spool; two messages during
  one turn end up answered. These must pass before and after; they are the safety net.
- **P1 — subscription split.** `HookRouter` sink per topic + consumer task + `ensure_subscription`
  / `drop_subscription` (D1). `run_turn` stops registering/unregistering and instead opens and
  closes a turn marker on the existing subscription. No behaviour change intended.
- **P2 — unattributed turns (D2).** Hooks with no open turn become their own visible turn.
- **P3 — delete the lock (D3).** Including PR #469's `lock.locked()` branch; injection becomes
  the only path.
- **P4 — timeouts onto the marker (D4).**
- **P5 — restart recovery (D6),** and demote the spool reconcile to backstop.

## Acceptance

- The Q1 scenarios each have a test: session-initiated turn, slow-boot container, late hook,
  backend restart. Each must show up in the room attributed correctly.
- `_topic_locks` no longer exists.
- A message posted during a long turn is answered without waiting for it (PR #469's tests
  still pass, via the new path).
- `task check` green. Do not run the suite locally beyond the files you touch — push and read
  CI (the host lacks docker; `test_workspace.py`, `test_workspace_git_timeout.py` and
  `test_check_script_strict.py` fail here for environment reasons on a pristine `main` too).
