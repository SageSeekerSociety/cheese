# 知是 2.0 (Cheese 2.0) — Agent Layer Architecture

This document is the self-contained reference for the agent layer built on top of the
existing Cheese backend. It defines the data model, the actor model, the orchestration
model, the connector plane (grounded in the **frozen `cli/` substrate**) and the
frontend integration. It is the single source of truth; `CLAUDE.md` references it for
the rules that must be followed.

The product intent (from `知是2.0产品设计.md`): an AI teammate ("芝士") participates in
a project's whole lifecycle — chatting, maintaining documents, owning work items — so
the process leaves a trace automatically and no one fills in forms. The platform's job
is to turn a single-user coding agent into a **multi-user, durable, observable**
teammate.

---

## 1. Principles

1. **万物皆块 (everything is a block).** A chat message and a document section are the
   same substrate: an append-only `block` with two independent trees — `reply_to_id`
   (conversation) and `struct_parent_id` (document) — plus typed references
   (`block_ref`). Time / conversation-tree / document-tree / references are four views
   over one pool of blocks. AI memory is *not* a fifth store: it is a rebuildable
   projection of the block tree, loaded on demand via OpenViking (`viking://` file
   paradigm, L0/L1/L2 layering) and re-distilled at the end of each session.

2. **一个 agent 就是一个用户 (an agent is a user).** There is exactly one identity type:
   `user`. Authorship, work-item ownership and document editing everywhere are just a
   `user_id`. The **only** difference between a human and an agent is the login method:
   a human authenticates with password / passkey; an agent authenticates with a session
   token minted by the connector when its Claude Code is driven. Both resolve to an
   `actor_id` (a user id) at the trust boundary. Domain code never branches on
   human-vs-agent; converting a user between the two would change only how it logs in.

3. **一套业务服务,三个前门 (one service set, three front doors).** Humans reach the
   services over REST; agents reach the *same* services over a tool/callback RPC; the
   orchestrator reaches them in-process. The actor is injected at the trust boundary and
   never read from a request body. There is no second copy of business logic for agents
   — an agent's tool is a schema-generated veneer over the same service a human calls. A
   valid session token is necessary, not sufficient — every call is authorized against
   the actor's real permissions.

4. **瘦客户机是宪法 (the thin client is the constitution).** The `cli/` substrate and its
   wire protocol are **frozen** (§5). Command emission, the triage lock, attention
   policy and prompts all live in the backend, so a business change never requires the
   client to be reinstalled. The server-delivered cheeselet is the AI CLI's **driver** —
   the thin read-screen / press-key / report-status layer that preserves flexibility;
   business decisions do not go into the cheeselet unless doing so makes things markedly
   simpler.

5. **Append-only truth, editable state, human acceptance.** What happened is only
   appended, never rewritten (chat, work-item annotations). State documents are editable,
   and editing a document is how you give the AI an instruction. A result counts only
   after a human accepts it.

6. **权限属于项目 (permissions belong to the project).** A permission holder shares a
   permission *to a project*; every human, agent and automation in that project then has
   it, and the share can be revoked. An agent's "power" is not an authz difference
   (initially everyone is fully privileged) but a role difference conveyed by prompt.

---

## 2. Data model (business domain — never distinguishes human vs agent)

Business tables carry an **optional** `project_id`. Most work objects (documents, work
items) anchor to a project as their aggregate root, but **chat is project-independent**:
a `thread` is a WeChat/Feishu-style group whose members are unrelated to any project (a
two-person thread is a direct message). `project_id` is therefore nullable throughout —
a thread (and its blocks) *may* be attached to a project, but a standalone chat has
`project_id = NULL`. The actor is always a `user_id`.

### 2.1 Block substrate — `domain/block`
```
block = { id, project_id?, thread_id?, kind, author_id (→user),
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
thread            = { id, project_id?, kind (general|direct|management), title?,
                      created_by }
thread_membership = { id, thread_id, user_id, role (member|admin|owner),
                      attention_policy_override? }   ← nullable; see §4
thread_membership_application = { id, thread_id, user_id, initiator_id, approver_id?,
                      type (request|invitation), status, role, ... }
```
Chat is a **global, project-independent** feature: a thread is a group that can hold any
users, humans and agents alike (`kind=direct` when it is a two-person private chat). The
substrate does not care which. `project_id` is nullable (a thread is normally standalone).
A message is a `block` with `thread_id` set and (optionally) `reply_to_id`.

**Membership consent (`thread_membership_application`).** An admin/owner adds humans
directly, but **adding an agent requires the agent's owner to consent** — an agent invite
mints an `INVITATION` whose `approver_id` is the agent's owner (the human who enrolled its
device), delivered through the notification system; the agent joins only on approval (a
`MEMBER`/`ADMIN`/`OWNER` role is carried on the application). A human asking to join mints
a `REQUEST` a thread admin answers. This reuses the `domain/team` approval pattern. **The
notification deliberately reuses `NotificationType.TEAM_INVITATION`** with a payload
`kind="thread_invite"` (plus `application_id` / `thread_id` / `agent_user_id`) rather than
a new type — `NotificationType` is a DB CHECK-constrained column, so a thread-invite type
would require a migration; the payload discriminator lets the frontend tell the two apart.
An admin/owner inviting an agent it *itself* owns skips consent and adds it directly.

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
**Lock semantics (uniform for human and agent owners):** taking ownership (`owner_id`
set) **locks** the item — it cannot be preempted by another actor and its core content
becomes immutable. From then on the only mutation is appending an **annotation**, and
writing an annotation **notifies the owner**. This is the whole concurrency model for
work: ownership is an exclusive lock; progress is append-only.

### 2.5 Reused legacy — `domain/project`, `domain/user`
`project` is the aggregate root (with membership); `user` is the sole identity. Neither
is modified structurally by the agent layer.

### 2.6 Three trees
事项树 (work items), 文档树 (documents), 管理树 (management) are three projections of one
project. Alignment is **not** an FK constraint but a soft constraint: the most powerful
agent is prompted to keep them aligned (§4).

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

Everything else treats the actor uniformly. There is **no `is_agent` column**: whether a
user is an agent is *derived*, not stored — a user is an agent iff it has a live execution
binding (a connector-driven screen / `agent_screen` row). The frontend uses this derived
flag purely for presentation (an agent badge on the avatar); business logic never branches
on it.

The **agent's login is not a per-agent JWT.** An agent's `cheese api` (the tool door,
§5.4) authenticates with a *pair*: the device's **durable device token** as
`Authorization: Bearer` (the client's config fallback) **plus** the screen's **screen
token** as `X-Cheese-Screen` (a 128-bit `uuid4` secret injected as `CHEESE_SCREEN` when
the screen opens). The server's `resolve_actor` (`app/agent/attribution.py`) resolves
that pair to the screen's `agent_user_id`: a call *from inside a screen* acts as that
agent; a bare device call (no valid screen token, or one naming another device's screen)
acts as the device's human owner — and the tool door rejects those. **No `CHEESE_TOKEN`
is injected.** This deliberately replaced an earlier expiring per-agent JWT: an agent
runs for days, so a token with an expiry would silently die mid-session; the
device+screen token pair never expires and is re-resolved per call. Possessing the pair
is still *not* sufficient — every call is authorized against the actor's real
permissions.

---

## 4. Orchestration model (design; built in Phase D)

- **One agent = one Claude Code session.** Messages from *all* the groups the agent
  belongs to are multiplexed into this single session, each prefixed with a
  `[group][speaker]` header. The single session is the agent's serial point — one mind,
  no split.

- **Attention policy is a settable attribute.** A member's per-thread override lives on
  `thread_membership.attention_policy_override` — a `String(32)` encoding one of three
  modes: **`ALL`** (wake on every message), **`INTERVAL:<n>`** (wake at most once per
  `n` minutes — `n` is clamped to ≥ 1; a malformed interval falls back to 1), or
  **`MENTION`** (wake only when @-mentioned). A **null** override decodes to `MENTION` —
  the safe agent default (only wake it when someone @'s it). Encoding/decoding live in
  `parse_attention_policy` / `format_attention_policy`
  (`app/domain/thread/services.py`); delivery is `override ?? MENTION-default`.

  **Delivery gating** (per agent member, evaluated in `forward_message_to_thread_agents`):

  | condition | delivered? |
  |---|---|
  | @-mentioned | always (ignores policy) |
  | mode `ALL` | always |
  | mode `INTERVAL:<n>` | iff ≥ `n` min since this thread last delivered to it |
  | mode `MENTION`, not mentioned | never |

  An @-mention is either an explicit `mention_user_ids` from the client or a fallback
  text scan for `@<nickname>` against member nicknames.

- **Group chat behaves like a real human group chat.** Messages are sparse. Every agent
  eventually sees all content (gated only by *when* it wakes, per its policy); each
  ignores what is irrelevant to it. Agents do not double-grab work because (a) the
  prompt tells them to ignore what is not theirs, and (b) whoever takes a task says so in
  the group ("已加入我的事项…"), which everyone else sees.

- **Triage lock = a sequencing guarantee, not suppression.** Among the *non-mentioned*
  eligible agents (per the gating table above), only the **highest thread-role** one is
  woken now (the superior wakes first); the rest are **deferred**. @-mentioned agents are
  always woken immediately and never deferred. The deferral releases (補投 — the deferred
  agents are then typed the message) the moment the responsible agent calls
  `finish_triage` (the tool door, §5.4), or after a fallback timer of ~30s
  (`_triage_defer_seconds`) if none does; a new message on the thread also flushes any
  still-pending deferral. After release the message is visible to everyone per their
  unchanged reading logic — but by then they also see the superior's organizing and its
  claim, so they correctly ignore it. **Implementation note (Act 4):** this is the
  *basic* form — a plain timeout-flush, **no reminder / no timeout-escalation** yet (the
  design's "remind, don't force-release" is not built).

- **Collaboration is prompt-level, not permission-level.** Every agent holds the whole
  project's permissions. An agent may @ another agent. The prompt encodes the protocol:
  state the problem in a document → @ the superior to decide → the superior @s a
  subordinate to delegate. Upper vs lower agents differ only in prompt, not in authz.

- **Three trees.** The management tree is realized by a **management group** whose
  superior listens to every message and whose subordinates listen passively. The superior
  is prompted to keep the 事项/文档/管理 trees aligned.

- **Memory (time-boxed).** On session restore the project's live documents *are* the
  first version of memory; the OpenViking integration is an interface stub first (the
  block-tree→memory projection can be filled in later).

---

## 5. Connector plane — the frozen `cli/` substrate

The mechanism that carries a real Claude Code on a client machine into the callback
path. The client is a single business-free Go binary in `cli/` (`cheese`); its reference
consumer and living protocol contract is `misc/web-claude/`. **Both are frozen: neither
`cli/` nor its wire protocol changes as the agent layer grows.** The agent layer's job is
to re-implement the *server* side of this exact protocol inside the backend.

### 5.1 What the substrate is
`cheese` is a generic terminal-hosting host with zero business trace: it authenticates
via a device flow, opens one long-lived control channel to the server, and runs
server-delivered JS **cheeselets** that drive a real CLI (Claude Code) over a small,
fixed bus. It never encodes what the app is for.

### 5.2 Wire protocol (`link.Msg` flat union, `link.Version = 1`)
A single JSON message shape carries every interaction, in both directions:
- **`hello{v}` ↑ / `welcome{v}` ↓** — handshake with protocol version. A mismatch warns
  but never hard-fails; this is the evolution hook.
- **`session.create` / `close` / `ready` / `error`** — a session is one hosted CLI (one
  screen + one cheeselet).
- **`script.load`** — server ships the cheeselet JS for a session.
- **variable bus** — `var.set` ↓ (server-owned, B-class) / `var.push` ↑ (JS-owned,
  A-class): the cheeselet reports status (busy/idle, parsed choices) as variables.
- **function bus** — `rpc.call` / `rpc.result` in **both** directions: cheeselet↔server
  calls.
- **screen channel** — `screen.subscribe` / `unsubscribe` / `input` / `resize` /
  `data`: raw terminal bytes for the 现场 viewer, with pty size sync.
- **`exec` / `exec.cancel` / `exec.result`** — device-level command execution with a
  caller-supplied `timeout` (device-side kill), cancel-by-id, `stdin`, and a per-stream
  1 MiB output cap (`truncated` flag). Rarely used; kept deliberately small.

### 5.3 Liveness
Half-open connections are detected with a ws ping every 15s (`pingPeriod`) and a 45s
read deadline (`pongWait`) reset on any pong or message; failure triggers the existing
infinite high-frequency reconnect (backoff cap 5s). The client never gives up.

### 5.4 Backend responsibilities (Act 2)
- **`domain/device`** — the device flow (`start` / `approve` / `poll`), durable device
  token, device→project binding, and a `/connect?code=…` approval API (real auth: only a
  logged-in user may approve).
- **`/api/connector/agent` ws** — the server end of the `link.Msg` protocol (welcome
  version negotiation, session lifecycle, var/rpc buses, screen relay, exec), wired to
  the real DB. This ports the proven `misc/web-claude/server` implementation.
- **Screen = command + cheeselet.** When the server opens a screen it mints a screen
  token (a 128-bit `uuid4` secret) injected as `CHEESE_SCREEN`; `cheese api` calls carry
  it as `X-Cheese-Screen` alongside the device's durable token as `Authorization: Bearer`.
  The server's `resolve_actor` maps that pair (screen → agent → user), attributing each
  tool call. **No per-agent JWT / `CHEESE_TOKEN` is injected** — see §3 for why (a JWT
  would expire mid-session; the device+screen pair does not).
- **Viewer authorization** — only project members may watch a project device's 现场.

### 5.5 便捷安装 (convenient install — serving side implemented; CI publishing pending)
`misc/web-claude/install.sh` is the cornerstone template: the *local/repo* installer that
finds or builds a binary next to the checkout and ships a **private tmux** so cheese never
fights the machine's own tmux. Its network sibling is a **server-hosted one-liner** so a
fresh machine needs nothing pre-installed:

```
curl -fsSL <origin>/connector/install.sh | sh
```

The backend serves, from the same origin as everything else
(`backend/app/api/routes/installer.py`):
- `GET /connector/install.sh` — a POSIX `sh` script (`backend/app/agent/install/install.sh`)
  that detects `uname -s`/`-m` → `<os>-<arch>`, downloads the matching `cheese` binary to
  `~/.local/bin/cheese`, provisions a private tmux at `~/.config/cheese/bin/tmux`, bakes in
  the origin as the default `base`, and prints the next step (`cheese link auto-connect`,
  which runs the device-flow login and installs the boot service). The connector base URL
  is baked in from `CONNECTOR_ORIGIN` or derived from the request, so binaries resolve behind
  any edge prefix. **tmux resolution order:** (1) copy the machine's own `tmux` if present —
  the surest OS match, and how macOS gets one; (2) else download the published static build;
  (3) else warn the user to install tmux themselves after setup (screens won't start until
  then). It carries no secrets and no business logic.
- `GET /connector/latest/<os>-<arch>/{cheese|tmux}` — the prebuilt artifacts for each
  supported target, served from `CONNECTOR_DIST_DIR`. These are produced by
  **`pnpm build`** in the frontend (`frontend/scripts/build-connector.mjs`): `cheese` is
  cross-compiled from the frozen `cli/` with Go (linux/darwin × amd64/arm64); a fully static
  `tmux` is built with Docker + musl (`frontend/scripts/tmux/`, cached in `.connector-dist`).
  Static tmux is **Linux-only**, so only `linux-*` targets publish one — macOS clients use
  their own tmux (resolution step 1 above). The binaries land in `frontend/public/connector/`
  (git-ignored build artifacts) and vite copies them into `dist/`, served from the same
  origin. Both steps are best-effort: a missing Go/Docker toolchain skips that artifact and
  still builds the web app, so the endpoint may 404 until a full build runs on a toolchain box.

The script carries **no secrets and no business logic** (it only fetches a binary and sets
a URL); enrollment still goes through the device flow (§5.4), so a piped installer never
grants access by itself. This closes the loop: `curl … | sh` → `cheese link connect` →
approve at `/connect` → the agent is live.

The production cheeselet is `misc/web-claude/cheeselet/claude.js` promoted to
`backend/app/agent/cheeselets/claude.js` (busy/idle state machine, choice parsing,
auto-mode, compact, command buffering) — a driver, not a decision-maker.

"逻辑只读": command emission is arbitrated by the orchestrator. A user watching the 现场
can scroll (real escape sequences) but cannot change the agent's state until it
explicitly takes over, which blocks orchestrator commands for the duration.

**Boot/ops.** The whole stack can run as boot-persistent systemd services — unit files
live in `misc/deploy/systemd/` (`cheese-infra` = Postgres/Valkey/ES via docker compose,
`cheese-backend` = uvicorn, `cheese-frontend` = the vite dev server that proxies `/api`
and `/connector`). Per-machine agent hosts instead run the connector as a service that
`cheese link auto-connect` installs (systemd/launchd, `User=<invoking user>`).

---

## 6. Frontend (integrated into the existing Vue app from the start)

Not a standalone page — new views inside `frontend/src`, revealed behind the
experimental flag (`?exp=true` in the URL, read by `composables/useExperimental.ts`;
sticky across in-app navigation via a router guard in `router/index.ts`) via a "聊天"
rail entry beneath "元思". The gate hides every new element unless `?exp=true` is
present — the `/chat` and `/my-agents` routes (`router/workspace.ts`, `router/agents.ts`
`beforeEnter: expOnly`), the 聊天 rail entry (`App.vue`), and the "我的 Agent" user-menu
item (`LeftAppRail.vue` / `MobileAppBar.vue`, `v-if="experimental"`). **`/connect` is the
one exception — it is NOT gated** (`router/connect.ts`), because the CLI's device-approve
link (`<origin>/connect?code=…`) carries no `exp` flag. Chat is the rail entry; agent
and device management is **not** on the rail (rail space is scarce) — it lives behind a
"我的 Agent" item in the user menu (the menu with 个人中心 / 退出登录), opening an
owner-centric page with two blocks: **Agent 管理** (create / delete / rename / set avatar,
laid out to admit more per-agent actions later) and **设备管理** (rename / delete; a device
is enrolled via `install.sh` + the device flow, so its "添加设备" button opens an install
tutorial, not a form).

Desktop is three zones plus an overlay:
- far-left rail (群聊 / 文档 / 事项 / 日历 / 成员 / 看板),
- group list + conversation (the *process*),
- a right **context pane** (文档 / 事项 / 预览 / Git) showing the business object you are
  currently looking at; **clicking a reference in a message navigates the context pane to
  that object** (对话是过程,点引用看状态). Maximizing it is the full-tree view.
- **现场** does not occupy a zone: a working agent's avatar shows a ring (busy/idle from a
  cheeselet variable); clicking it opens a floating popup wired to that agent's screen via
  xterm.js over the `screen.*` channel — the three-state (readonly / scroll / full)
  viewer with A−/A+ font control and `viewerLevel` reporting, **ported verbatim from
  `misc/web-claude/web`** (protocol fields unchanged).

Mobile collapses the side-by-side into a navigation stack: chat is primary; tapping a
reference pushes the object full-screen; tapping a working avatar pushes the 现场
full-screen; a bottom tab bar mirrors the rail.

**Browser `/connector/...` transport.** The chat/presence/我的 Agent views call the backend
`/connector/...` endpoints directly (raw JSON, no `{code,message,data}` envelope), so they
use `fetch` — not the axios REST instance — and therefore **bypass the axios refresh-token
interceptor**. To keep a long-lived chat/workspace tab from freezing when its ~15-min
access token expires, these calls go through a shared helper (`network/api/connectorFetch.ts`)
that, on a 401, refreshes the access token **once** (a single in-flight refresh shared by
concurrent polls) and retries.

---

## 7. Module layout (tech-doc four layers)

```
cli/                         frozen substrate — one business-free `cheese` Go binary
misc/web-claude/             frozen reference: living protocol contract + regression bed
backend/app/
  domain/            业务层 — only user_id, never human-vs-agent
    block/ thread/ document/ workitem/    + reused project/ user/
    device/          device flow, durable device token, device→project binding
  agent/             agent plane (not business)
    attribution.py   resolve_actor: device token + screen token → actor (no per-agent JWT, §3)
    tools/ registry  tool registry + invoker + callback dispatch → same services
    cheeselets/claude.js   production driver (promoted from web-claude)
    orchestration/   event loop / serial queue / triage lock / attention (Phase D)
    binding.py       execution binding (adapter/machine/attention), keyed by user_id
  api/routes/
    threads.py documents.py workitems.py   human front door (REST)
    connector.py                            agent front door: link.Msg ws + screen relay
frontend/src/views/workspace/               integrated workspace UI
  components/connector/                      现场 viewer (ported from web-claude/web)
```

---

## 8. Delivery plan

The build proceeded in six acts, mapped to this doc:

- **Act 1 — blueprint.** This document (grounded in the frozen `cli/` protocol).
- **Act 2 — connector plane.** `domain/device` + `/api/connector/agent` porting the
  `link.Msg` protocol from web-claude, on the real DB; screen token attribution; viewer
  authz (§5.4).
- **Act 3 — business base.** Real `block` / `thread` / `document` / `workitem` on
  PostgreSQL + alembic (retiring the mock store), anchored to `project_id`; human REST +
  orchestrator front doors (§2).
- **Act 4 — orchestrator + agent identity.** Session-token minting, one-session-per-agent
  multiplexing, triage lock + reminder, attention policy, upper/lower prompts, the agent
  tool door, time-boxed memory (§3, §4).
- **Act 5 — frontend + 现场.** `/connect` approval page and the ported three-state 现场
  floating window; clickable references (§6).
- **Act 6 — real end-to-end + Proxmox self-bootstrap + green.** A real task run on
  `cheese-dev-env2-client`, a time-boxed Proxmox provisioning experiment (new templates
  only), `task check` green, clean commits.

**Discipline.** `cli/` and its protocol change by not one character; every act ends
end-to-end demonstrable (cut scope, not verification); `misc/web-claude` is read-only —
reference and regression bed, no further development.

---

## 9. Security invariants

- The actor is injected at the trust boundary; never trusted from a request body. A valid
  session token is necessary, not sufficient — callbacks are authorized against the
  actor's real permissions.
- Client binaries hold no secrets and no business logic.
- Cluster/compute credentials come from environment/secret config, never committed.
- Existing PVE templates are never modified; new machines use new templates.
