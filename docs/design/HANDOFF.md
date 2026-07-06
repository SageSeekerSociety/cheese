# 知是 2.0 Agent Layer — Build Handoff

Branch `design/cheese-agent-layer` (rebuilt clean from `main`; the earlier
debugged connector slice is preserved on `archive/connector-debug-slice`).
Whole backend green: **ruff + pyright zero, 2836 tests passing**. Every migration
round-trips; the security-critical path was independently adversarially reviewed
(no authorization bypass).

Read [`architecture.md`](./architecture.md) for the full design. This file is
the "what's ready, what I need from you, how to continue" summary.

## What's built and usable now

**The AI → platform path is reachable end-to-end.** An agent (via the cheese
CLI, or any client) calls:

- `GET /agent/tools/schema` and `POST /agent/tools/call` with an
  `X-Agent-Session` token (mint via `app.agent.authorization.token.mint_agent_session`).
- Tools: `post_message`, `write_document`, `request_human_decision`. Each derives
  `project_id` from the injected actor (containment), passes the agent gate
  (`project.ai_mode`), permission checks, then writes real blocks / notifications.

**The human ↔ agent conversation is complete on both sides.** Humans use
`/threads` (create thread, list, post message, read messages — gated on project
membership); agents write to the same block/thread substrate via tools.

**Permissions are first-class** (`agent/authorization`): a capability holder
shares a capability *with a project* (`domain/grant`, revocable); the
`ProjectAuthorizer` allows an action only if some human identity that counts
really holds it — the actor's own, the human an agent acts for, or a granter
whose share is **re-checked live** — by reusing the existing `permission_checker`.

**Domains** (all `project_id`-anchored): `block` (append-only dual tree + refs),
`thread` (群聊 + membership + attention schema), `document` (activefile
projection), `review` (验收), `milestone` (deadlines), `agent` (registry),
`project` (extended aggregate root).

**Agent engine seam**: abstract `Agent` contract + `RecordingAgent` +
`naive_api` machine-less agent loop (tool-calling, step-capped, mockable model).

**Compute**: `compute/pool/PveClient` — async Proxmox client (inventory / clone /
task-poll), unit-tested and **verified read-only against the live cluster**.
Config via `PVE_*` env (no secret committed).

## Decisions I need from you (blocked without these)

1. **Orchestration serial unit** — per-agent (single mind, serial) or
   per-(agent, group)? Drives the `agent/orchestration` queue.
2. **Agent-to-agent @** — may an agent @-summon another agent? (Leaning yes.)
3. **Triage / 对话锁 / preemption** — you were shaping: dispatcher role vs.
   per-message-range organize-lock; preempt-current vs. reprioritize-and-queue.
   The orchestration runtime waits on this.

## Deliberately NOT built (needs you / heavier / risky solo)

- **Orchestration runtime** (platform → AI delivery) — blocked on the above.
- **Connector transport / `cheesed_codex`** — interactive Claude Code; the
  debugged Go client is on `archive/connector-debug-slice`.
- **Live provisioning** (clone → install cheesed → register) — creates real VMs;
  templates 119 (LXC) / 124 (VM) ready, IPs to be allotted high-down in
  `192.168.16.0/20`. Best run with you watching to avoid mess.
- Real `ChatModel` OpenAI binding (needs live creds), `agent/{memory,skills,roles}`.

## How to verify / run

```bash
cd backend
uv run alembic upgrade head          # apply migrations
uv run ruff check app tests          # lint
uv run pyright app                    # types
uv run pytest tests/unit tests/integration -q   # 2836 pass
```
