# Local → device convergence: dev topics onto the co-located device path

Status: investigation + staged plan, 2026-08-13. Direction is settled by #218
("local and device are one path; long term, everything runs the device path")
and refined by #282 (supply form × visibility are the real axes; "过渡的不是容
器，是『本地』那一半"). The local container path is the transitional form —
#316's deploy-interruption class (23 container rebuilds, 4 killed turns in one
day) is specific to it. Written against main @ `da393acc`, with PR #318
(device supply/visibility, #282 决定 2) in flight — see §5. This document
changes no behavior.

## 1. Where things stand

Dev (`cheese-dev-env1-app`, 192.168.16.5) runs every live topic on the local
container path: `topic.compute_profile` is materialized to the local provider's
name on first turn (`backend/app/domain/agent/chat.py:1840-1845`) and frozen
once the topic has run (`backend/app/api/routes/topics.py:494`, `:505-526`).

The device path already exists end-to-end and is proven against remote
MicroCloud machines: enrollment (`backend/app/api/routes/connector.py`),
transport (`backend/app/domain/agent/device_hub.py`), launcher
(`backend/app/domain/agent/device_launch.py`), provider
(`backend/app/domain/agent/device_provider.py`), and the `/llm` model route
(`backend/app/api/routes/llm_proxy.py`). `DeviceProvider` joins the compute
pool unconditionally (`backend/app/domain/agent/compute.py:456-463`), and the
market lists it, gated on a project-scoped online device
(`backend/app/domain/agent/market.py:109-117`,
`backend/app/api/deps.py:35-41`).

The co-located form — the dev box itself enrolled as a self-hosted device —
is in the code, not just an idea: when `DEVICE_SHARED_WORKSPACE_HOST_ROOT` is
set and the device is not platform-provisioned, the screen's cwd is the
topic's **real** worktree (container path translated to the host root the
device sees), so edits flow through the normal checkpoint/accept path with no
clone/sync (`device_provider.py:234-254`, checkpoint at `:400-407`). Remote
devices instead clone over git smart-HTTP and push back on every Stop hook
(`device_launch.py:245-256`, `:272-297`).

Note: `docs/device-self-hosting.md` §3 claims `DEVICE_SHARED_WORKSPACE_HOST_ROOT`
"is not yet wired as a server-side setting" — stale; it is
(`backend/app/core/config.py:203`, consumed at `device_provider.py:219`,
`:242`). That doc needs a correction pass when this plan deploys.

## 2. The chain that already exists (how the dev box becomes a device)

1. **Enroll**: `curl <origin>/connector/install.sh | sh` on the dev box →
   `cheesehost auth login` → human approves at `/connect` → durable token →
   outbound `WS /connector/agent` marks it online in `device_hub`
   (`connector.py:88-160`, `docs/device-self-hosting.md` §1).
2. **Bind**: assign the device to the dogfood team (`assign_to_team` — every
   project of the team may run on it, `connector.py:410-431`).
3. **Select**: a topic picks compute `device` before its first turn
   (`topics.py:505-526`), or the project sticky / team default supplies it
   (`chat.py:1826-1832`).
4. **Pin**: the first turn pins the topic to the device, write-once; an
   offline pinned device queues, never drifts
   (`device_provider.py:52-88`).
5. **Screen**: the provider opens a screen over `link.Msg`; the launcher
   writes an isolated `$HOME/.cheese/home/{project}` config home, wires hooks
   (`cheese-hook` + spool + drainer, `device_launch.py:304-353`), fetches the
   `cheese` CLI from the backend (`device_launch.py:311-315`, served by
   `backend/app/api/routes/sandbox.py:44`), embeds the platform system prompt
   via quoted heredoc (#308, `device_launch.py:207-211`, `:270-271`), and
   hosts `claude` in a per-work-dir tmux session that survives link drops
   (`device_launch.py:366-382`).
6. **Perceive**: hooks POST to `{CONNECTOR_PUBLIC_BASE}/sandbox/hooks/{topic}`
   — the same endpoint, router, and translation the local path uses
   (`backend/app/domain/agent/hooks_substrate.py`, one substrate by design).
7. **Attribute**: the screen acts as the topic's own agent-user; model calls
   go through `/llm`, which swaps the scoped token for the project's virtual
   gateway key server-side (`device_provider.py:294-298`,
   `llm_proxy.py:125-151`).

## 3. Gap inventory

Ranked. "Local" below means the container path dev topics run today
(`LocalDockerProvider`, `compute.py:86`; quotas and mounts shown for the tmux
variant are the container recipe both share).

### 3.1 Blocking — must be resolved before device is the dev default

**G1. Isolation/visibility: a co-located screen sees the whole box.**
The launcher is a bare `bash -lc` on the host — no container, no namespace
(`device_launch.py:224-388`). A screen on the dev box can Grep every other
topic's worktree, the backend's `.env`, and the docker daemon. #282 §三 is
explicit that on a shared machine the default room must see only its own tree,
and that whole-machine visibility "不能是随手能点的默认项". The container path
gives per-topic file isolation plus resource quotas (`--memory 2g --cpus 2
--pids-limit 512 --ulimit core=0`, `tmux_provider.py:366-378`); the bare path
has none of these (#282 §七-3: one room filling the disk takes every room on
the box down — dev measured 92% full, `docs/infrastructure.md:141`). PR #318
adds the `visibility` column with values `host|isolated` but states
"`isolated` has no transport behind it yet" (its migration docstring). **The
missing transport is the actual convergence work**: a device screen launched
inside a per-topic container on the device — the existing container recipe
becomes the `isolated` implementation of the device path, exactly #282 §五-2's
reading (the container is "共用一台机器时的实现手段", what retires is the
separate "local" backend).

**G2. Model supply flips on migration.** Device turns are hard-wired to the
gateway: `_model_kwargs` returns route `"gateway"` for provider `device`
(`chat.py:1430-1436`), the screen env points at `{base}/llm`
(`device_provider.py:294-298`), and `/llm` forwards to
`settings.anthropic_base_url` (LiteLLM) with the project's virtual key
(`llm_proxy.py:125-151`). The subscription branch exists only for
`tmux-hooks` (`chat.py:1437-1451`); there is no subscription/ccproxy pool
behind `/llm`. So any topic whose turns today run on subscription or a native
Claude profile silently changes model when moved to device. #218's "put
subscription traffic behind a backend-governed endpoint" is therefore a
**prerequisite for parity**, not a parallel track — or the flip must be an
explicit accepted decision for the migrated set.

**Resolved direction (2026-08-13):** **all** device turns — co-located and
remote alike — go through the **subscription**, the same path the local tmux
container already uses. GLM/`/llm` is dropped for device entirely; there is no
split. The earlier worry that a remote machine "can't hold the subscription
credential" does not apply: by design the machine never holds a real
credential. `subscription_provider` ships a fake one and the real token is
injected by the **metering proxy** on the backend; the machine only carries a
per-session scoped cheese token (HMAC over {project, topic, exp}) that
authenticates "bill this project", and the proxy docstring
(`provider_env.py:71`) explicitly contemplates the proxy being **exposed on a
machine network** and authenticating remote callers by that scoped token. So
the credential stays on the backend for every case; `/llm`→LiteLLM was never
the "safe for remote" route, just a different model pool. Everyone gets the
same Claude a local turn gets.

*One implementation wrinkle to resolve in Phase 0 (applies to every device,
co-located and remote):* the container reaches the metering proxy via docker
`--add-host` redirect-by-name of `api.anthropic.com` (`provider_env.py:71` +
the `--add-host` args in `tmux_provider`). A device screen is a **bare
`bash -lc` tmux session**, not a container, so `--add-host` does not apply —
the name redirect must come from another hook (host `/etc/hosts` entry, or the
proxy growing a real reachable vhost), and for a remote device the proxy must
be reachable at a real address (the scoped-token auth the docstring already
describes is what makes that safe). `subscription_provider`'s env (fake
credential + CA trust + scoped session token, **no `ANTHROPIC_BASE_URL`**)
carries over unchanged; only the SNI-redirect transport differs. Small but
non-zero — exactly what the Phase-0 scratch run should exercise.

**G3. Turn ceiling and liveness.** Device turns run under a single flat 900 s
deadline — `idle_suspect_s == hard_ceiling_s` reduces the two-layer check to
the old static deadline, flagged as an open TODO in the constructor
(`device_provider.py:130-142`, `config.py:216`). The local tmux recipe gets
300 s idle-suspect + pane-dead probe + 3 h hard ceiling
(`config.py:120-124`, `tmux_provider.py:528-586`). A 15-minute cap kills
exactly the long turns dev dogfooding produces (#316 observed 15-minute
turns). Device needs an equivalent activity signal (`device_hub` per-screen
bytes / heartbeat recency, `device_hub.py:76-79`) and the longer ceiling.

**G4. No screen/session lifecycle.** `DeviceHub.close_screen` has zero
callers outside the hub (`device_hub.py:228`); topic accept/archive reaps
both container names (`workspace/service.py:2097-2105`) but never a device
screen, its device-side tmux session, or its `$HOME/.cheese` dirs. On a
co-located box this leaks a running `claude` per finished topic on the same
host the platform runs on. The pin is likewise never released on archive
(only `host_swap` releases pins, `backend/app/domain/agent/host_swap.py:130`).

### 3.2 Behavior differences vs local — close or consciously accept

| # | Area | Local (container) | Device (co-located) | Evidence |
|---|---|---|---|---|
| D1 | Worktree | real worktree bind-mounted | **same real worktree** via host-root translation — carries over by construction | `device_provider.py:242-249`; `workspace/service.py:1800` |
| D2 | Checkpoint / accept | `checkpoint_worktree` per turn | same call when co-located; decision cached in-memory per (project, topic), written at screen open only | `device_provider.py:151`, `:284-288`, `:400-407`; `compute.py:236-245` |
| D3 | Session continuity / clone | tmux session in container; `--resume` picks up cloned transcripts | fresh `claude` per screen; `resume_session_id` accepted and ignored — clone/fork does not resume | `tmux_provider.py:404-427` vs `device_provider.py:354-387` |
| D4 | Per-turn env (`CHEESE_TURN`, `CHEESE_MEMORY_SCOPE`, `CHEESE_OWNER`) | injected per container/turn | never passed — `build_screen_launch` has no such params, and screen env is fixed at creation anyway. Blocks written via `cheese` lose turn attribution; memory scoping is lost | `device_launch.py:391-449`; `device_provider.py:302-325`; contrast `compute.py:182-189` |
| D5 | App preview (运行环境预览) | `docker port` on the topic's container → reverse proxy | dead: no container to ask, launcher sets no `CHEESE_APP_PORT`/`CHEESE_APP_BASE` | `workspace/service.py:2058-2073`; `topics.py:996-1015`; `tmux_provider.py:651-657` |
| D6 | Terminal 现场 | hook-event feed in the topic drawer (both paths); raw ttyd mirror exists but is unwired | hook-event feed works identically; raw screen viewer exists but only on 我的设备, not reachable from the topic | `tmux_provider.py:169-177`, `:439-449`; `connector.py:257`; `frontend/src/views/MyDevicesView.vue:324` |
| D7 | `cheese await` logs | `CHEESE_AWAIT_LOGS` in the session mount, readable by backend for status | env unset → logs land device-side where `status_snapshot` can't read them | `tmux_provider.py:680`; `backend/sandbox/cheese:231`; `awaited_tasks.py:260` |
| D8 | Image attachments (图片输入) | SDK embeds base64 into the turn | hooks substrate drops the `images` param; agent must `Read` the uploaded file — works co-located (real worktree), same as tmux | `compute.py:298-300`; `hooks_substrate.py:402` (unused) |
| D9 | Per-project sandbox image (spec §9.1) | project picks e.g. `cheesex-dev` | meaningless bare on host; toolchain is whatever the box has. Becomes meaningful again with G1's containered `isolated` transport | `compute.py:150-155` |
| D10 | Activity status for `cheese status` | tmux tracker surfaces idle/suspect | none (`tmux_activity_status` returns None for device) | `compute.py:389-400`; `topics.py:288-290` |
| D11 | `cheese` CLI freshness | staged per-turn from the backend build into the session mount | fetched once at screen creation; a long-lived screen keeps a stale CLI across backend deploys (the exact staleness class the mount fixed) | `workspace/service.py:1810-1823`; `device_launch.py:311-315` |
| D12 | System prompt refresh | rewritten every turn; next fresh session picks it up | embedded at screen creation; a reused screen keeps its launch prompt (same fresh-session rule, but "fresh" is rarer since the screen is per-topic-work-dir) | `tmux_provider.py:735-739`; `device_provider.py:274-277` |
| D13 | Backend-restart resilience | containers/sessions found by deterministic name | hub state is in-memory; screen re-adoption after restart is an unwired skeleton — first turn after restart opens a new screen that re-attaches to the surviving tmux session (works, but viewers/attribution of the old screen are gone until then) | `device_hub.py:195-207`; `device_launch.py:376-382` |

### 3.3 Operational prerequisites (dev deployment)

- `CONNECTOR_PUBLIC_BASE` must map 1:1 onto the backend root as reachable
  *from the device* — the 2026-08-08 dev incident (hooks landing on the SPA,
  every event dropped silently) is documented in the setting's own comment
  (`config.py:187-196`).
- `DEVICE_SHARED_WORKSPACE_HOST_ROOT` = the host path of the backend's
  `workspace_root` mount. Getting the provisioned/self-hosted distinction
  wrong "fails silently: the launcher mkdir -p's whatever it is given"
  (`device_provider.py:209-232`) — #318 makes this read stored
  `device.supply` instead of a reverse lookup.
- A dedicated unix user on the dev box for `cheesehost` (the screen runs as
  that user; today it would also bound what a `host`-visibility screen can
  reach — it must not be the deploy user).
- Single co-located device means `host_swap` has nowhere to swap to — it
  already degrades to a plain message (`host_swap.py:113-128`); fine, but the
  quarantine cooldown (#186) can park the whole dev deployment. Worth a
  conscious knob before the flip.
- Device runtime deps on the box: bash, node, curl, tmux, `claude`
  (`docs/device-self-hosting.md` §2) — all present on dev.

## 4. Existing stock: migrate or turn over

The ~dozen live dev topics each hold: a worktree (host-side, shared), a
sandbox container, a session dir (`~/.claude` mount with transcripts), and
`topic.compute_profile` frozen to the local provider.

- **Worktree**: carries over by construction (D1). Nothing to copy.
- **Conversation/session**: does NOT carry — the device path starts a fresh
  interactive `claude` (D3). This is the same loss class `host_swap` already
  accepts and words plainly: "what is lost is the uncommitted tail... not the
  topic" (`host_swap.py:26-30`) — except here the whole conversational memory
  restarts, which for a long-lived dogfood topic is a real cost.
- **The picker is frozen** after the first turn (`topics.py:514-515`) — by
  design (affinity red line). There is deliberately no path that silently
  moves a running topic.

**Recommendation: natural turnover, with an explicit opt-in migration action
for the few long-lived topics.** Topics are born and archived weekly on dev;
flipping the *default* for new topics converges the fleet without touching
any frozen pin, and archive already reaps the old containers. For the handful
of long-lived topics, add a deliberate, room-visible "move this topic's
compute" action (mirroring `host_swap`'s explicit-release pattern:
release/rewrite `compute_profile` between turns, post a system message naming
what is lost). Bulk-rewriting `compute_profile` in the DB is exactly the
silent-move the affinity rules exist to prevent — do not do that.

## 5. Fit with PR #318 (in flight — do not touch its files)

#318 lands the #282 decision-2 vocabulary this plan builds on:

- `device.supply` (`cloud` | `self_hosted`), stored at enrollment, never
  inferred; `_is_co_located` switches from the `project_machines` reverse
  lookup to reading it. The dev box enrolls through the human connector flow →
  `self_hosted` → with the shared root set, co-location holds. Correct for us
  by default.
- `device.visibility` (`host` | `isolated`), column only — "so that transport
  is a new VALUE rather than a new column". G1's containered screen is that
  value's implementation. Phase 1 below is sequenced **after** #318 merges:
  it would edit `device_provider.py` and the device domain, which #318 is
  actively changing; and its repo-rule (`check-repo-rules.sh` rule 5) bans the
  machine-table imports any interim work might otherwise add.
- Its `delete_platform_provisioned` door raises on `self_hosted` — the
  guarantee that no future reclaim path can destroy the enrolled dev box.

## 6. Staged plan

**Phase 0 — enroll and smoke (deployment only, no code).**
Enroll the dev box as a self-hosted device under a dedicated user; bind to
the dogfood team; set `CONNECTOR_PUBLIC_BASE` + `DEVICE_SHARED_WORKSPACE_HOST_ROOT`;
run ONE scratch topic end-to-end on compute `device` (create → turn → edit →
checkpoint → accept card → archive). This validates §3.3 and D1/D2 with zero
impact on live topics — device stays opt-in per topic. Fix
`docs/device-self-hosting.md` §3 staleness alongside.

**Phase 1 — close the blocking gaps (after #318 merges).**
1. Per-turn env + attribution (D4): deliver `CHEESE_TURN`/`CHEESE_MEMORY_SCOPE`/
   `CHEESE_OWNER` per turn — screen env is creation-fixed, so this rides the
   existing per-turn cheeselet call or a `var.push`, not the env.
2. Lifecycle (G4): close screens + release resources on accept/archive (the
   reaper that today calls `stop_topic_container` grows a device arm), and a
   device-side dir cleanup.
3. Ceiling/liveness (G3): raise the device ceiling to tmux parity and feed the
   idle-suspect layer from hub screen bytes / heartbeat recency.
4. `isolated` visibility transport (G1): the launcher gains a containered
   variant on devices whose visibility says so — per-topic container, resource
   quotas, worktree bind-mount; the existing tmux image is the starting recipe.
   `host` visibility remains for the explicit "要这台机器本身" rooms (#282 §四).
5. Model parity (G2): point **every** device screen at the **subscription**
   the same way the tmux container does (`subscription_provider` env); drop
   `/llm` for device entirely. This is a **blocking parity gap, not a
   follow-up** — until it lands, a device turn silently runs GLM instead of the
   Claude a local turn gets. The one open piece is the SNI-redirect transport
   for a bare (non-docker) process, plus exposing the metering proxy at a
   reachable address for remote devices (safe under the scoped-token auth the
   proxy already describes); see G2 above.
6. Await logs (D7) and CLI refresh (D11) as small follow-ups.

Gate resolved by decision (2026-08-13): **all** device turns go through the
subscription (drop GLM); no split, no general model-supply unification needed
first — the subscription path already exists for the local container and is
reused verbatim. The Phase-0 scratch run must confirm the bare-process proxy
redirect before Phase 2.

**Phase 2 — new topics default to device on dev.**
Flip the dogfood team's default compute to `device`
(`teams.py:552-566` / project sticky). New topics start on the device path;
`local-docker` stays selectable and untouched. Watch one week of dogfooding:
deploy-interruption class (#316) should disappear for device topics — the
screen and its tmux session live outside the deploy's container churn.

**Phase 3 — stock turnover.**
Natural turnover for most (archive reaps the container path); the explicit
opt-in move action (§4) for long-lived topics, worded like `host_swap`'s
room-visible message.

**Phase 4 — retire the transitional form.**
When no dev topic runs `local-docker`: demote it from default
(`market.py:98-108`), then fold the container recipe into the device path's
`isolated` transport and delete the divergent local plumbing (#218's end
state; #282 §五-2's framing — the container survives as an isolation means,
"local" as a category goes).

## 7. Risks and rollback

- **Rollback is per-scope and cheap until Phase 4**: compute is chosen per
  topic; Phase 2 rolls back by flipping the team default back — new topics
  return to `local-docker` immediately. Topics already pinned to the device
  keep running there (pins never drift); if the device path itself is broken,
  their turns fail with the explicit offline/setup errors, and the explicit
  move action (Phase 3's tool) moves them back. Nothing in Phases 0-3 deletes
  local-path code.
- **Double execution risk during migration**: a topic moved from container to
  device must have its container reaped at move time — both stacks editing
  one worktree (container mount + co-located screen) is D1's virtue turned
  hazard. The move action must call `stop_topic_container` itself.
- **Shared-host blast radius**: until G1's quotas land, one runaway screen
  can fill the dev disk for the whole platform (#282 §七-3; the disk-pressure
  guard `deploy/cheesex-disk-pressure-guard.sh` watches the box but cannot
  contain a writer).
- **Hook path misconfiguration fails silently** (§3.3 first bullet): Phase 0's
  smoke topic is the guard; the spool drainer retries for 24 h
  (`device_launch.py:343-353`), so a misconfigured base shows up as spool
  buildup on the box — check it during smoke.
- **Backend restarts mid-turn**: local turns die with the process either way;
  device screens survive (D13) but the in-flight turn's hook queue is gone —
  the server-side spool + reconcile (`chat.py:1317-1398`) recovers events, as
  today.
- **Credential surface**: a `host`-visibility screen exposes whatever its unix
  user can read. Until G1, the enrolled user must be unprivileged and the
  backend's env/secrets unreadable to it. This is the same trust #282 §四
  attaches to the "要这台机器本身" tier — acceptable for dogfooding, not for
  the default room.

## 8. First step

Phase 0, and within it the smallest real move: enroll the dev box as a
device (dedicated user, correct `CONNECTOR_PUBLIC_BASE`,
`DEVICE_SHARED_WORKSPACE_HOST_ROOT`), and run one scratch topic on compute
`device` through create → turn → checkpoint → accept → archive. It is pure
deployment, touches no live topic, races nothing #318 is editing, and it
converts most of §3's paper claims (co-location path translation, hook
delivery, checkpoint, accept) into observed facts before any code is written.

## References

- #218 — unify model supply behind the backend; local converges on device.
- #282 — supply form × visibility; container is the shared-machine isolation
  means, not a separate form.
- #316 — deploys kill in-flight turns on the local container path.
- PR #318 — `device.supply` + `device.visibility` (#282 决定 2), in flight.
- #308 (merged) — system prompt delivery to the launched `claude` (heredoc
  path in `device_launch.py`).
- #186 — 换身体; `host_swap.py` is the explicit-move precedent Phase 3 reuses.
- `docs/device-self-hosting.md` — enrollment runbook (§3 staleness noted
  above).
- `docs/plans/2026-08-10-usage-unification-design.md` — the #218 metering
  side this plan's G2 gate depends on.
