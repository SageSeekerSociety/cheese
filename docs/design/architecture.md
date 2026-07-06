# 知是 2.0 (Cheese 2.0) — Agent Layer Architecture

This document is the self-contained reference for the agent layer built on top of
the existing Cheese backend. It defines the data model, the actor model, the
orchestration model, the connector plane, and the frontend integration. It is the
single source of truth; `CLAUDE.md` references it for the rules that must be
followed.

The product intent (from `知是2.0产品设计.md`): an AI teammate ("芝士") participates
in a project's whole lifecycle — chatting, maintaining documents, owning work items
— so that the process leaves a trace automatically and no one fills in forms. The
platform's job is to turn a single-user coding agent into a **multi-user, durable,
observable** teammate.

---

## 1. Principles

1. **万物皆块 (everything is a block).** A chat message and a document section are
   the same substrate: an append-only `block` with two independent trees —
   `reply_to_id` (conversation) and `struct_parent_id` (document) — plus typed
   references (`block_ref`). Time / conversation-tree / document-tree / references
   are four views over one pool of blocks.

2. **一个 agent 就是一个用户 (an agent is a user).** There is exactly one identity
   type: `user`. Authorship, work-item ownership and document editing everywhere are
   just a `user_id`. The **only** difference between a human and an agent is the
   login method: a human authenticates with password / passkey; an agent
   authenticates with a session token minted by the connector when its Claude Code
   is driven. Both resolve to an `actor_id` (a user id) at the trust boundary. A
   human and an agent are interchangeable in the domain; in the future a user could
   be converted between the two by changing only how it logs in.

3. **一套业务服务,三个前门 (one service set, three front doors).** Humans reach the
   services over REST; agents reach the *same* services over a tool/callback RPC;
   the orchestrator reaches them in-process. The actor is injected at the trust
   boundary and never read from a request body. There is no second copy of business
   logic for agents — an agent's tool is a schema-generated CLI veneer over the same
   service a human calls.

4. **Thin client.** `cheesed` and `cheese` (the client-machine binaries) carry
   **zero business logic**. Command emission, the triage lock, attention policy and
   prompts all live in the backend, so a business change never requires the client
   to be reinstalled.

5. **Append-only truth, editable state, human acceptance.** What happened is only
   appended, never rewritten (chat, work-item annotations). State documents are
   editable, and editing a document is how you give the AI an instruction. A result
   counts only after a human accepts it.

---

## 2. Data model (business domain — never distinguishes human vs agent)

All business tables are anchored to `project_id` (the aggregate root). The actor is
always a `user_id`.

### 2.1 Block substrate — `domain/block`
```
block = { id, project_id, thread_id?, kind, author_id (→user),
          reply_to_id?,      ← conversation tree (chat)
          struct_parent_id?, ← document tree (docs)
          content, created_at, edited_at? }
block_ref = { id, from_block_id, target_type (block|document|workitem),
              target_id }    ← a message can reference a work item / doc / block
```
Chat blocks are append-only. Document blocks are editable (their `content` /
`edited_at` change). Both live in one table; the distinction is `kind` + which tree
they hang on.

### 2.2 群聊 — `domain/thread`
```
thread          = { id, project_id, parent_thread_id?, kind (general|management),
                    title }
thread_membership = { id, thread_id, user_id, role,
                      attention_policy_override? }   ← nullable; see §4
```
A message is a `block` with `thread_id` set and (optionally) `reply_to_id`. A group
can hold both humans and agents as members; the substrate does not care which.

### 2.3 文档 — `domain/document`
A projection over the block `struct_parent` tree, editable by any actor (human or
agent). `get_tree` / `render` / `edit_node`. Depth-guarded.

### 2.4 事项 — `domain/workitem`
Named `workitem` to avoid colliding with the legacy `task` / `topics` domains. The
work-item tree (事项树) mirrors the product's topic tree.
```
workitem            = { id, project_id, parent_id?, title, description,
                        owner_id? (→user), status, locked, created_at }
workitem_annotation = { id, workitem_id, author_id (→user), content, created_at }
```
**Lock semantics (uniform for human and agent owners):** taking ownership
(`owner_id` set) **locks** the item — it cannot be preempted by another actor and its
core content becomes immutable. From then on the only mutation is appending an
**annotation**, and writing an annotation **notifies the owner**. This is the whole
concurrency model for work: ownership is an exclusive lock; progress is append-only.

### 2.5 Reused legacy — `domain/project`, `domain/user`
`project` is the aggregate root (with membership); `user` is the sole identity.
Neither is modified structurally by the agent layer.

---

## 3. Actor model

```
   human  ──REST (login token)───────┐
                                      ├─▶ inject actor_id (trust boundary)
   agent  ──callback / tool RPC───────┤        │
          (connector session token)   │        ▼
   orchestrator ──in-process──────────┘   the SAME domain services
```
A service method takes `actor_id: int`. It never branches on human-vs-agent except at
exactly three seams:
- **AI gate** — a project's `ai_mode` decides whether agents may act at all.
- **Notification delivery** — a human gets a normal notification; an agent gets the
  event delivered into its session/queue.
- **Adapter / connector layer** — how an agent is physically driven.

Everything else treats the actor uniformly.

The **agent's login** is `agent/session`: a session token minted by the connector,
bound to the agent's `user_id`, verified on every callback. Possessing a valid token
is *not* sufficient — the callback is still authorized against the actor's real
permissions.

---

## 4. Orchestration model (design; built in Phase D)

- **One agent = one Claude Code session.** Messages from *all* the groups the agent
  belongs to are multiplexed into this single session, each prefixed with a
  `[group][speaker]` header. The single session is the agent's serial point — one
  mind, no split.

- **Attention policy is a settable attribute.** Values: `ALL_MESSAGES`,
  `ALL_USER_MESSAGES`, `IDLE_WINDOW`, `MENTION_ONLY`. The default lives on the agent
  (its personality — an aggressive superior is `ALL_MESSAGES`, a passive subordinate
  is `MENTION_ONLY`); a `thread_membership.attention_policy_override` can change it
  for one group. Delivery uses `override ?? agent_default`.

- **Group chat behaves like a real human group chat.** Messages are sparse. Every
  agent eventually sees all content (gated only by *when* it wakes, per its policy);
  each ignores what is irrelevant to it. Agents do not double-grab work because (a)
  the prompt tells them to ignore what is not theirs, and (b) whoever takes a task
  says so in the group ("已加入我的事项…"), which everyone else sees.

- **Triage lock = a sequencing guarantee, not suppression.** When a message arrives,
  it is delivered first to the single highest-priority eligible agent (the superior,
  `ALL_MESSAGES`, wakes first); other agents' wake-ups are *deferred* until the owner
  calls `finish_triage`. After release the message is visible to everyone per their
  unchanged reading logic — but by then they also see the superior's organizing and
  its claim, so they correctly ignore it. If the owner does not finish triage within
  a few minutes, the orchestrator **reminds** the agent (it does not force-release).

- **Collaboration is prompt-level, not permission-level.** Every agent holds the
  whole project's permissions (power is conveyed by prompt, not authz). An agent may
  @ another agent. The prompt encodes the protocol: state the problem in a document →
  @ the superior to decide → the superior @s a subordinate to delegate. A subordinate
  waits for the superior on out-of-scope matters; @s the superior only if it forgot
  or erred; subordinates generally do not talk to each other unless the superior
  assigns it.

- **Three trees.** 事项树 (work items), 文档树 (documents), 管理树 (management). The
  management tree is realized by a **management group** whose superior listens to
  every message and whose subordinates listen passively. The superior is prompted to
  keep the three trees aligned.

---

## 5. Connector plane (design; built in Phase D, contract designed in A/B)

The mechanism that carries a real Claude Code on a client machine into the callback
path. Client binaries live in `connector/` (Go, separate from the backend).

- **cheesed** (daemon, client machine): runs Claude Code in a private-socket tmux
  (invisible to the user's `tmux ls`), relays the terminal via a mature web-terminal
  library (ttyd/webtty + xterm.js), and keeps one long-lived, heart-beating control
  channel to the backend (screen frames + control commands). Transparent read/write
  relay; **no business logic**, no "read-only" concept of its own.
- **cheese** (CLI, client machine): fetches a tool schema from the backend and exposes
  it as friendly commands (`cheese post-message --thread X --content Y`) with
  generated `--help`; sends each tool call as an RPC (the **callback**) directly to
  the backend with the session token in a header. The schema is derived from the
  backend's own OpenAPI, so the tool surface stays in sync with the REST API by
  construction.
- **backend `agent/tools` + `api/routes/connector`**: the tool registry (annotated
  Python functions), the invoker (actor gate + per-tool authorization + dispatch to
  the same services), the callback endpoint, and the tool-schema endpoint.
- **`agent/adapters/cheesed_codex`**: binds "Claude Code on a client" to the abstract
  Agent (an input-able thing that has tools; it does **not** return a main text
  stream — its ordinary output stays on the "现场" screen, and only tool calls come
  back).
- **`agent/binding`**: `agent_binding(user_id, adapter, machine,
  default_attention_policy, parent_user_id, status)` — orchestrator-owned execution
  binding, keyed by `user_id`. Not part of the business domain.

"逻辑只读": command emission is arbitrated by the orchestrator. A user watching the
现场 can scroll (real escape sequences) but cannot change the agent's state until it
explicitly takes over, which blocks orchestrator commands for the duration.

---

## 6. Frontend (integrated into the existing Vue app from the start)

Not a standalone page — new views inside `frontend/src`, revealed behind
`?experimental=true` via a "项目" rail entry beneath "元思".

Desktop is three zones plus an overlay:
- far-left rail (群聊 / 文档 / 事项 / 日历 / 成员 / 看板),
- group list + conversation (the *process*),
- a right **context pane** (文档 / 事项 / 预览 / Git) showing the business object you
  are currently looking at; **clicking a reference in a message navigates the context
  pane to that object** (对话是过程,点引用看状态). Maximizing it is the full-tree view.
- **现场** does not occupy a zone: a working agent's avatar shows a ring; clicking it
  opens a floating popup wired (later) to that agent's tmux via xterm.js/webtty,
  read-only until you flip a takeover switch.

Mobile collapses the side-by-side into a navigation stack: chat is primary; tapping a
reference pushes the object full-screen (with a back arrow); tapping a working avatar
pushes the 现场 full-screen; a bottom tab bar mirrors the rail.

---

## 7. Module layout (tech-doc four layers)

```
backend/app/
  domain/            业务层 — only user_id, never human-vs-agent
    block/ thread/ document/ workitem/    + reused project/ user/
  agent/             agent plane (not business)
    session/         session token = the agent's login
    tools/           tool registry + invoker + callback dispatch → same services
    binding.py       execution binding (adapter/machine/attention), keyed by user_id
    orchestration/   event loop / serial queue / triage lock / attention
    adapters/cheesed_codex/   bind client Claude Code as an Agent
  api/routes/
    threads.py documents.py workitems.py   human front door (REST)
    connector.py                            agent front door (tool schema + callback + control)
connector/           客户机二进制 (Go, zero business logic)
  cheesed/  cheese/
frontend/src/views/workspace/               integrated workspace UI
```

---

## 8. Delivery plan

**初稿 = A + B + C** (the three business modules + agent-as-user + integrated
frontend). Real Claude Code driving, attention/management runtime, 现场 xterm.js,
compute provisioning and the institutional layer are all **Phase D+**.

- **A — API contract + backend mock + integrated frontend + deploy + test users.**
  Define the real REST contract for threads/documents/workitems (backend returns
  mock data), define the agent front-door contract (tool schema + callback shape,
  also mock), integrate the frontend against real URLs, deploy the backend and seed
  test users (humans + reserved agent users). Also begin the `cheese` CLI, generating
  it from the backend's OpenAPI.
- **B — 群聊 + agent-as-user.** Real `block` + `thread` + threads REST; real
  `agent/session` (login) + callback dispatch, exercised by a test harness so a human
  (REST) and an agent (callback) hit the *same* thread service.
- **C — 事项 + 文档.** Real `workitem` (lock / freeze / annotate-and-notify) and
  `document` (editable struct tree) + `block_ref` and reference-jump in the UI.
- **D+ — connector transport + cheesed_codex adapter + orchestration runtime +
  attention/management + 现场 xterm.js + compute + institutional layer.** Real Claude
  Code is driven into the *same* callback path built in B.

---

## 9. Security invariants

- The actor is injected at the trust boundary; never trusted from a request body. A
  valid session token is necessary, not sufficient — callbacks are authorized against
  the actor's real permissions.
- Client binaries hold no secrets and no business logic.
- Cluster/compute credentials come from environment/secret config, never committed.
- Existing PVE templates are never modified; new machines use new templates.
