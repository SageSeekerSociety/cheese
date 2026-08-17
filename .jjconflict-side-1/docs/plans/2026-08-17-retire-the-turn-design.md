# Retire the turn

Status: design. Supersedes `2026-08-16-topic-event-subscription-design.md`, which proposed
demoting the turn to a marker. Demoting is a half-measure: it leaves an object with a
lifetime that the platform must keep defending. This retires it.

## The disease

Underneath a topic is ONE long-lived interactive Claude Code session. It has no turns. The
platform invents turn boundaries and then has to defend them, and every defence has failed
in a different way:

- The hook queue was registered for a turn's duration (`HookRouter.register` inside
  `run_turn`), so a topic could only run one turn, so a lock had to serialize turns, so a
  message posted during a long turn waited it out. Fixed at the symptom level in #469.
- Between turns nothing is subscribed, so hooks fall to the disk spool. That window is not
  an edge case: the session starts its own turns (a backgrounded command finishes and wakes
  it), the platform gives up on a turn while the container is merely slow to boot, late
  hooks arrive after `Stop`, and a backend restart drops the in-memory registry entirely.
- Consumption was stamped when a turn *succeeded*, but whether Claude Code accepted a
  message is independent of whether the turn succeeded — a timed-out turn replayed a message
  the session had already answered. Fixed in #495 by moving consumption onto a per-message
  `UserPromptSubmit` receipt.

Each fix moved one job off the turn. This finishes the job.

## What "turn" actually carries

One word, four unrelated jobs. Unbundling them is the whole task.

| # | Job | Today | Where it belongs |
|---|---|---|---|
| ① | **Attribution** — which blocks belong together | `blocks.turn_id`, threaded into REST handlers via the `X-Cheese-Turn` header and `core/turn_context.py` | The human message that caused the work |
| ② | **Consumption** — which human messages 芝士 has been handed | per-block, on a `UserPromptSubmit` receipt | **Done — #495.** Do not disturb it. |
| ③ | **Usage** — token spend accounting | `ResourceUsage.turn_id`; `usage/repositories.py` reports `count(distinct(turn_id))` | The human message that caused the spend |
| ④ | **Lifecycle** — in-flight state, timeouts, retries, 正在思考 | `TurnRunner` (79 refs in `runtime.py`), `run_hooks_turn`'s two-layer timeout | The session / screen |

## The decision that makes this shippable

**Keep the `turn_id` column and the `X-Cheese-Turn` header. Change what the id IS.**

It stops being a freshly minted id for an invented interval and becomes **the id of the
human block that caused the work**. Consequences, all good:

- no migration — the column keeps its name, type and index
- no protocol break — the `cheese` CLI keeps sending `X-Cheese-Turn`; it now carries a
  message id, and the CLI neither knows nor cares
- `count(distinct(turn_id))` keeps working and starts meaning something honest: how many
  human messages caused work, instead of how many intervals the platform invented
- the frontend keeps grouping by the same field

A platform-initiated run (kickoff, resume, an unsolicited turn the session started itself)
has no originating human message. Those mint an id as before — but it is a **work id**, not
a turn: nothing opens or closes it, nothing times it out, nothing waits for it.

## Non-goals

Do not change: the `X-Cheese-Turn` header name or the `turn_id` column name; #495's receipt
consumption; the tmux→inbox-socket injection question; `--channels`; the `❯` readiness gate.

## Phases — one commit each, suite green at every one

**P0 — characterization.** Pin today's observable behaviour on current `main` before
touching anything: blocks of one exchange share an id; `X-Cheese-Turn` from the CLI lands on
CLI-written blocks; usage rows attribute to the same id; a mid-turn message is consumed on
its receipt and NOT on turn success (#495). These must pass before and after.

**P1 — subscription outlives turns.** `HookRouter` holds a per-topic sink created with the
topic's first live screen and dropped with that screen; `subscribe` is idempotent (the old
`register` replaced the queue, which is what starved the first turn); `drain` goes away. One
long-lived consumer task per topic translates hooks → events → blocks continuously, not
stopping at `Stop`. Hooks arriving with no platform work open are **not** spooled: they open
their own work id and become visible, which is how a session-initiated run stops vanishing.

**P2 — lifecycle off the turn.** Timeouts, idle-suspect and liveness become properties of
the session, not of an interval. `TurnRunner`'s in-flight state and the 正在思考 indicator
derive from session activity. The per-topic lock survives only where it must: around prompt
construction, because `_pending_human_blocks` reads unstamped blocks and two concurrent
builds otherwise answer the same message twice (measured — `test_turn_message_window`).

**P3 — attribution derives from the message.** Where a turn id is minted for a human summon,
use that human block's id instead. Platform-initiated work keeps minting, as a work id.

**P4 — usage attributes to the message.** `ResourceUsage.turn_id` carries the same derived
id. Keep the reporting query working; say in the commit body what its number now means.

**P5 — delete the abstraction.** Rename what is left so the code stops describing a thing
that no longer exists: `core/turn_context.py`, `TurnRunner`, the frontend's
`ChatPanel.turnLifecycle.spec.ts`. No behaviour change in this phase — if one appears, it
belongs in an earlier phase.

## Acceptance

- Nothing in the backend mints an id to represent "an interval the platform opened and will
  close". Every id is either a human message's or a work id for platform-initiated work.
- `test_turn_message_window`, `test_topic_event_subscription` and #495's receipt tests pass.
- A session-initiated run (background command finishes, session wakes itself) shows up in
  the room attributed to its own work id.
- A backend restart reattaches to a live screen instead of losing its stream.
- `task check` green. Do not run the full suite locally beyond the files you touch — push and
  read CI. On this host `test_check_script_strict.py` (5), `test_workspace_git_timeout.py` (2)
  and `test_workspace.py`'s docker case fail on a pristine `main` too.
