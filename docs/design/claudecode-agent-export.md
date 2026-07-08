# Claude Code Agent Export / Copy / Fork

Status: design draft (research spike — **not yet implemented**). Companion to
[`architecture.md`](architecture.md). This doc defines what a *complete, resumable*
Claude Code conversation is on disk, and the data contract + API/CLI surface for
exporting, uploading, copying and importing that state so an agent's memory can be
duplicated onto another machine or forked in place.

Scope note: `cli/` is **frozen** (thin `cheese` substrate + `link.Msg` wire protocol,
`misc/web-claude/` is the reference contract). All real logic lands in the backend and a
server-delivered driver. This doc describes the feature accordingly; it does **not**
propose any `cli/` change.

---

## 1. What a Claude Code conversation actually is on disk

Investigated against the real `~/.claude` on a v2.1.20x install. A conversation ("session")
is keyed by a **`sessionId`** (UUID, e.g. `1364fee1-7a0e-473b-917f-5d190f24cf5c`) and a
**project slug** derived from the launch **cwd**. The state is spread across several files
under `~/.claude/`, plus one machine-global config file `~/.claude.json`.

### 1.1 Project slug ("the hashing" gotcha)

`~/.claude/projects/<slug>/` where `<slug>` is the **cwd with every `/` (and `.`) replaced by
`-`**. It is *not* a hash — it is a lossy, reversible-ish path mangling:

```
cwd  /home/nictheboy/repo/SageSeekerSociety
slug -home-nictheboy-repo-SageSeekerSociety
```

Consequences for portability:
- The transcript's home directory is baked into the slug. Moving to a machine with a
  different `$HOME` or repo path **changes the slug** → the import must **re-derive the slug
  from the destination cwd** and place files under the new slug.
- Multiple distinct cwds can collapse to the same slug (a `-` in a real dir name is
  ambiguous). Rare, but the remap must key off the *actual destination cwd*, never off
  string-editing the source slug.

### 1.2 The file manifest

Everything below lives under `~/.claude/` unless noted. "Req" = required for a faithful,
resumable copy; "Opt" = improves fidelity (rewind, history) but resume works without it.

| # | Path (per session unless noted) | Format | Req? | What it is / notes |
|---|---|---|---|---|
| 1 | `projects/<slug>/<sessionId>.jsonl` | JSONL, append-only | **Req** | **The transcript** — the whole conversation. First lines: `mode`, `permission-mode`, `file-history-snapshot`; then per-message records `{type:user\|assistant, message, uuid, parentUuid, sessionId, cwd, gitBranch, version, timestamp, ...}`. `--resume <sessionId>` replays this. **This one file is the core of a copy.** Can be large (55 MB here). |
| 2 | (same file) records with `isSidechain:true` | JSONL lines | **Req** | **Subagent / sidechain transcripts are inlined in the same `.jsonl`** — there is no separate per-subagent file. Copying #1 already carries them. |
| 3 | `projects/<slug>/<sessionId>/workflows/scripts/*.js` | JS | Opt | Generated background-task/workflow driver scripts spawned during the session. Needed only if those background workflows must resume. |
| 4 | `projects/<slug>/memory/` | dir (md/json) | Opt→Req if used | Session-scoped memory artifacts. Include if present. |
| 5 | `tasks/<sessionId>/*.json` (+ `.highwatermark`, `.lock`) | JSON per work item | **Req if TODOs matter** | The agent's task/TODO list (`{id, subject, description, activeForm, status, blocks, blockedBy}`). This is the modern location of "todos" in this version (no top-level `~/.claude/todos/`). Drop `.lock`; regenerate on import. |
| 6 | `shell-snapshots/snapshot-bash-<ts>-<rand>.sh` | shell script | Opt | Captured shell env (aliases/functions/options/PATH) the Bash tool sources. Referenced per-session but machine-specific; **prefer to regenerate on the target** rather than transplant. |
| 7 | `session-env/<sessionId>/` | dir (often empty) | Opt | Per-session env overlay. Copy if non-empty. |
| 8 | `file-history/<sessionId>/<hash>@vN` | file blobs | Opt | Checkpoint backups of files the agent edited, powering `/rewind`. Large; **not** needed to resume the chat, only to time-travel edits. Optional in the bundle (flag-gated). |
| 9 | `history.jsonl` (global) | JSONL | Opt | Global prompt history across *all* projects, tagged by `sessionId`+`project`. For a copy, **filter to this sessionId** and merge on import; never ship wholesale (leaks other projects). |
| 10 | `settings.json` (global) | JSON | Opt | `{model, effortLevel, theme, ...}` UI/model prefs. Portable; merge, don't overwrite. |
| 11 | `~/.claude.json` → `projects[<cwd>]` block | JSON subtree | **Req (subset)** | Per-project config: `allowedTools`, `mcpServers`, `enabledMcpjsonServers`, `disabledMcpjsonServers`, `hasTrustDialogAccepted`, `lastSessionId`. **Keyed by absolute cwd** → must be **re-keyed to the destination cwd** on import. Ship only this subtree, sanitized. |
| 12 | `~/.claude.json` top-level | JSON | **Exclude** | `machineID`, `userID`, `oauthAccount`, caches, `numStartups`, telemetry. Machine/account identity — **do not export**. |
| 13 | `.credentials.json` (`claudeAiOauth`) | JSON | **NEVER export** | OAuth tokens. Hard-excluded. Target machine authenticates as its own enrolled device/account. |
| 14 | `plugins/installed_plugins.json`, `plugins/known_marketplaces.json` | JSON | Opt (as refs) | Which plugins/marketplaces are installed and their **absolute** `installPath`/`projectPath`. Ship as *references* (marketplace + name + version) and **re-install on target**; never transplant the `cache/` tree or absolute paths. |
| 15 | project `.claude/` dir + `CLAUDE.md` | files in repo | — (travels with git) | Agents, skills, scripts, project settings. Part of the repository, arrives via the git checkout, **not** the bundle. Bundle may record the expected git commit for consistency. |
| 16 | `sessions/<pid>.json` | JSON | **Exclude** | Live process registry (`pid → sessionId, cwd, startedAt, bridgeSessionId`). Purely ephemeral runtime; regenerated on launch. |

**Minimum viable faithful copy** = #1 (transcript, which includes #2 sidechains) + the
sanitized/re-keyed project subtree #11 + TODOs #5. Everything else is fidelity or
explicitly excluded.

### 1.3 Resume linkage

- A session resumes with `claude --resume <sessionId>` (alias `-r`), which loads
  `projects/<slug>/<sessionId>.jsonl` for the **current cwd's slug**. So resume needs both:
  the transcript file *and* being launched from the matching cwd.
- `~/.claude.json.projects[<cwd>].lastSessionId` records the most recent session for a cwd
  (drives bare `--continue`).
- `history.jsonl` and each transcript line also carry `sessionId`, so a **fork must rewrite
  the sessionId** consistently (new UUID) or the fork and origin will collide/interleave.

---

## 2. How our connector launches Claude (the hook points)

- The agent runs as a **screen** on a connected device. `backend/app/agent/hub.py`
  `open_screen(...)` mints a per-screen token and sends a `session.create` message
  (`{t, sid, command, screen, cols, rows, source}`) carrying the cheeselet `source`;
  `misc/web-claude/server/app.py` is the reference for the same flow.
- The launched program is `SCREEN_PROGRAM` (default `claude`), run as
  `bash -lc 'exec <program>'` (login shell so PATH resolves). Today it launches **bare
  `claude`** — no `--resume`. That `command` argv is the **hook point**: to resume/import a
  session we launch `claude --resume <sessionId>` (from the right cwd) instead of `claude`.
- The device also exposes a generic `exec` RPC (`hub.exec` / web-claude `/exec`) that runs
  an argv on the device and returns stdout — already used to pre-trust the cwd. **This is
  the transport for pushing/pulling bundle files** onto/off a device without any `cli/`
  change (tar over exec, or a driver-mediated file channel).
- The cheeselet (`backend/app/agent/cheeselets/claude.js`) only *drives the terminal*
  (busy/idle, choices, `say`/`compact`); it has no session-file knowledge. Export/import is
  a **backend + device-exec** concern, not a cheeselet concern — though the cheeselet could
  gain an `exportReady`/`importDone` signal.
- `sessionId` today is Claude's own UUID (see `sessions/<pid>.json.sessionId`), distinct
  from the connector `sid` (`s<hex8>`) and the bridge id. Export must record Claude's
  `sessionId`, and the orchestrator must map `agent_user_id ↔ device ↔ sessionId`.

---

## 3. Portability gotchas (summary)

1. **Slug = mangled cwd**, not a hash → re-derive from destination cwd on import (§1.1).
2. **`~/.claude.json` project config is keyed by absolute cwd** → re-key on import (#11).
3. **Absolute paths inside plugin/history/file-history metadata** → ship as references or
   filter; re-install/rehome on target (#8, #9, #14).
4. **Credentials & machine identity must never leave the source** (#12, #13). The bundle is
   authenticated *by the transfer*, not by carrying tokens.
5. **cwd must exist and match** on the target (repo checked out at the same relative path,
   ideally same git commit) or `--resume` won't find the transcript / the agent's file
   references break.
6. **Shell snapshots are machine-specific** → regenerate rather than transplant (#6).
7. **sessionId rewrite on fork** to avoid two live sessions sharing one id (§4).
8. **Live-session consistency**: the `.jsonl` is appended while Claude runs. Export must
   snapshot a quiesced session (agent idle, or accept a last-N-lines truncation) to avoid a
   half-written trailing record.

---

## 4. Export / Import / Copy / Fork semantics

**Bundle** = a `.tar.zst` (or dir) with a top-level `manifest.json` and a sanitized subset
of §1.2. Proposed layout:

```
manifest.json                     # see §5.1
transcript.jsonl                  # #1 (includes #2 sidechains)
tasks/*.json                      # #5, .lock/.highwatermark stripped
project-config.json               # #11 subtree, cwd-neutral (cwd stored separately)
memory/…                          # #4 if present
workflows/scripts/*.js            # #3 if present
settings.subset.json              # #10 selected keys
history.filtered.jsonl            # #9 filtered to this sessionId (optional)
file-history/…                    # #8 only if --with-rewind
plugins.refs.json                 # #14 references (marketplace/name/version), no cache
```

Explicitly **absent**: `.credentials.json`, `~/.claude.json` top-level identity, `sessions/`,
absolute plugin caches.

**Operations** (all four are the same bundle with different placement rules):

- **Export** — read the source device's `~/.claude`, assemble + sanitize the bundle, upload
  to backend blob storage. Source `sessionId` and source `cwd` recorded in the manifest.
- **Import** — on a target device: pick destination `cwd`; **derive new slug**; write
  `transcript.jsonl` to `projects/<newSlug>/<sessionId>.jsonl`; re-key `project-config.json`
  under `~/.claude.json.projects[<destCwd>]`; place tasks/memory/etc.; then launch
  `claude --resume <sessionId>` from `destCwd`.
- **Copy (cross-machine transfer)** = Export on A → Import on B, **keeping the same
  `sessionId`** (the source is being *moved*; A's session is closed/retired to avoid two live
  copies of one id).
- **Fork (same-machine or cross-machine duplication)** = Import with a **freshly minted
  `sessionId`**: rewrite the `sessionId` field on every transcript/history line and rename
  the file, so origin and fork diverge independently. A **same-machine fork** additionally
  needs a distinct cwd *or* distinct sessionId (same slug + same id would clobber the
  origin); we always mint a new sessionId, so same-cwd forks are safe (two `.jsonl` files in
  one slug dir).

**sessionId rewrite** (fork): stream the JSONL, replace `"sessionId":"<old>"` on each record
(and in `history.filtered.jsonl`), regenerate the filename. `parentUuid`/`uuid` message ids
are per-message and can stay as-is (they're not the session key); only `sessionId` is the
fork axis.

---

## 5. Proposed API + CLI surface

`cli/` is frozen, so there is no new `cheese` subcommand implemented in Go. The work is:
(a) backend REST endpoints, (b) device-side file movement via the existing **exec RPC**
driven by the orchestrator/driver, (c) optionally a driver (cheeselet) signal. A future
`cheese`-level UX, if ever wanted, would be a **server-delivered driver command**, not a new
binary.

### 5.1 `manifest.json`

```jsonc
{
  "schema": "cc-agent-export/v1",
  "createdAt": "2026-07-08T...Z",
  "source": {
    "sessionId": "1364fee1-...",
    "cwd": "/home/nictheboy/repo/SageSeekerSociety",
    "slug": "-home-nictheboy-repo-SageSeekerSociety",
    "claudeVersion": "2.1.202",
    "gitCommit": "<sha>",           // repo HEAD at export, for target consistency
    "agentUserId": 420
  },
  "files": [ { "role": "transcript", "path": "transcript.jsonl", "sha256": "...", "bytes": 55230363 }, ... ],
  "includes": { "fileHistory": false, "history": true, "plugins": "refs" },
  "excludes": ["credentials", "machineIdentity", "sessions", "pluginCache"]
}
```

### 5.2 Backend endpoints (sketch; standard `{code,message,data}` envelope, actor authorized)

```
POST   /agents/{userId}/session/export        -> starts export on the bound device
                                                  body: {includeFileHistory?, includeHistory?}
                                                  data: {bundleId, status}
GET    /agents/session/exports/{bundleId}      -> {status, manifest, downloadUrl?}
POST   /agents/session/import                  -> body: {bundleId, targetAgentUserId|deviceId,
                                                         cwd, mode: "copy"|"fork"}
                                                  data: {newSessionId, status}
POST   /agents/{userId}/session/fork           -> convenience: export+import fork on same/other
                                                  device; data: {newSessionId}
GET    /agents/session/imports/{jobId}         -> progress
```

- Only the agent's **owner** (or a permitted operator) may export/import — authorized against
  real permissions at the trust boundary, per the "session token necessary, not sufficient"
  rule. Bundles are **owner-private** blobs; downloadUrl is short-lived and scoped.
- **Copy** retires the source session (closes the screen, marks it moved) once the target
  reports `session.ready` on the resumed transcript.

### 5.3 Device-side mechanism (no `cli/` change)

1. Orchestrator, via `hub.exec`, runs a tar/read on the source device scoped to the exact
   file list (never a blanket `~/.claude` tar — that would sweep credentials). Streams bytes
   back to the backend, which sanitizes + stores the bundle.
2. Import reverses it: `exec` writes files into the target `~/.claude` at the remapped slug,
   patches `~/.claude.json` (re-key project subtree), then `open_screen` is issued with
   `command = ["bash","-lc","exec claude --resume <sessionId>"]` and the target `cwd`.
3. Sanitization (credential/identity exclusion, slug remap, sessionId rewrite) happens
   **server-side**, never on the device, so the exclusion policy is centrally enforced.

---

## 6. Open questions

1. **Quiescing**: force the agent idle before export, or support hot export with trailing
   partial-record repair? (The `.jsonl` is appended live.)
2. **file-history size**: default `--with-rewind` off. Do we ever need cross-machine rewind,
   given edited files also live in the git repo?
3. **MCP servers**: `project-config.json` may reference MCP servers with local commands/paths
   that don't exist on the target. Validate + warn, or attempt install?
4. **Plugins**: re-install from marketplace on import — what if the marketplace/version is
   unavailable? Degrade gracefully vs fail the import.
5. **Same-machine fork of a *live* session**: is a running origin safe to copy from, or must
   we snapshot at a message boundary?
6. **Identity semantics**: after a cross-machine **copy**, is it the *same* agent user on a
   new device (re-bind device→existing agent_user_id) or a new binding? Fork almost certainly
   wants a **new** agent user; copy wants the **same** one moved. Needs the orchestrator's
   agent↔device binding model to weigh in.
7. **Bundle format/versioning**: `.tar.zst` + `schema` field; how do we migrate bundles across
   Claude Code transcript-format changes (the `version` field varies per line)?
8. **Cross-`$HOME` cwd**: if the target has no equivalent repo path, do we clone the repo at
   the recorded `gitCommit` first, or refuse? Resume hard-depends on cwd existing.
</content>
