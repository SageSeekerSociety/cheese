# Infrastructure — deploys, backups, access

The one place that describes where this app runs, how it ships, and how its data
is protected. If you're touching deploy or ops, read this first. Backup/DR
specifics live in [`deploy/README-backup.md`](../deploy/README-backup.md); this
doc is the map around it.

## Environments

There are **three** independent deployments. They are separate machines with
separate data — don't conflate them.

| Env | Public | App host | DB | Stack | Deploys via |
|---|---|---|---|---|---|
| **dev / test** | xiaoyuer's test domain | `cheese-dev-env1-app` (192.168.16.5, ghg private net) | `cheese-dev-env1-postgresql` (192.168.16.7) | Docker Compose (`deploy/deploy-docker.sh`) | **auto on merge to `main`** |
| **prod (RUC)** | `cheese.ruc.edu.cn` | `cheese-prod-app` (192.168.16.8, ghg private net) | `cheese-prod-postgresql` (192.168.16.10) | Docker Compose (`deploy/deploy-docker.sh`) | **published GitHub Release → approval** |
| **etrip** | `etrip.cn` | `etrip` (8.217.1.152, Aliyun HK) | in-container `cheese_prod_postgres` (paradedb) + `cheesex-pg` | Docker Compose (`/opt/cheese-deploy`) | **published GitHub Release → approval** |

Notes:
- dev and prod are on ghg's private `192.168.16.0/24` (no public inbound). etrip
  is a public Aliyun box.
- prod's uploaded files (PDF 赛题) live on the app box's local disk
  (`STORAGE_TYPE=local`, `backend/uploads/`), **not** object storage.

## Shipping

### The image model (why we deploy by commit SHA)

`build.yml` builds one **backend**, **frontend**, and **sandbox** image **per
commit** on every `main` push, tagged with the 7-char commit sha
(`ghcr.io/sageseekersociety/cheese/<image>:<sha>`). This is a monorepo, so one
sha pins backend+frontend together. Deploys reference that **sha**, never a
floating tag like `:main` (which let backend and frontend drift to different
commits) and never a version tag (frontend has no semver tags). Images are
private on GHCR; boxes pull with their existing GHCR auth.

### The runtime: Docker (deploy by sha)

dev and prod (RUC) run the app as **Docker containers** (`deploy/deploy-docker.sh`
+ `deploy/compose/docker-compose.base.yml`): pull the per-commit `backend` +
`frontend` images, migrate, bring the app tier up, health-check, auto-rollback.
The frontend image bundles nginx (SPA + `/api` reverse-proxy). DB/Redis are
**external** ghg hosts (the app only holds `DATABASE_URL`/`REDIS_URL`); uploads
bind-mount a host dir outside the containers (`/home/nictheboy/shared/uploads`
on prod — the 赛题 PDFs). Each box was cut over from bare-metal once
(`deploy/{dev,prod}-docker-cutover.sh`); the old systemd service is kept
**installed-but-disabled** as an instant rollback.

Device control connections have a separate release boundary. The
`device-connection` service owns `/connector/agent`, live terminal WebSockets,
and the in-memory `DeviceHub`. It does not run chat recovery, cleanup, or machine
wake-up work; the current business backend performs those jobs after reading the
owner's connection snapshot. A normal app release starts it if it is absent,
then leaves its running container and image unchanged while backend and frontend
are replaced. The business backend calls the owner over an authenticated
compose-network endpoint and restores its online-device and screen view from the
owner at startup. Executor calls remain in the owner under their existing trace
ID, so replacing a backend waiter neither cancels the device call nor prevents a
replacement backend from collecting its result.

On boxes with `cheese-api-front`, the deploy copies the versioned nginx config,
checks it, and gracefully reloads nginx before replacing the backend. The exact
`/connector/agent` and live-terminal paths go to the owner's loopback port;
other API paths continue through the active backend switch. Updating the owner
itself is a separate release operation because it closes the connections it
owns. Dispatch **Release device connection owner** with a tested ref and target;
that workflow runs `deploy/release-device-connection.sh`, which pulls and
force-recreates only `device-connection`, then waits for its health check. It is
manual-only, refuses to replace an owner with an active executor call, and is
never called by the normal app deployment workflows. It reads the same box-local
deploy environment and compose overlays as the app deployment.

**On dev the backend rolls out without downtime.** The box's **:8081** is
`cheese-api-front`, a host-network nginx from `deploy/llm-tunnel/` whose
backend upstream comes from an include file (`~/ops/llm-tunnel/active/
backend.conf`). With `ACTIVE_BACKEND_DIR` set in `~/ops/deploy.env`, the deploy
script starts the new image as `cheese-backend-next` on **:18082**, waits for
its `/healthz`, points api-front at it and reloads, recreates the compose
`backend` (on **:18081**) behind it, points api-front back, and removes the
temporary container. The frontend container reaches the backend through that
same host port (`API_UPSTREAM=host.docker.internal:8081`), so its `/api` never
sees the swap either; the ghg edge (APISIX) proxies to **:8080** (frontend) and
**:8081** (api-front). What remains is about one second on **:8080** when the
frontend container itself is recreated. Measured on the first rollout
(2026-09-04): 0 failed requests on :8081 across the swap, 1 second of refused
connections on :8080. Before it, every deploy cut the backend for the ~13 s a
container takes to boot. A box without `ACTIVE_BACKEND_DIR` — prod (RUC),
etrip — still recreates in place, gap included; the first deploy after
enabling it on a box pays the old gap once, because the frontend that is still
running resolves `backend` by compose name.

### dev — continuous deploy

Merge to `main` → `deploy-dev.yml` runs on the **self-hosted runner on the dev
box** (label `cheese-dev`), gated on `build.yml`'s images, then
`deploy/deploy-docker.sh`. No human step.

### prod (RUC) — release-gated

Publishing a GitHub Release (or a manual `workflow_dispatch`) → `deploy-prod.yml`:
gate on the release commit's CI, then **a human approval** (auto-opened issue,
same mechanism as etrip), then the **self-hosted runner on the prod box** (label
`cheese-prod`) runs `deploy/deploy-docker.sh`. prod never auto-deploys, and it
deploys **releases, not main commits** — its current release is **v0.16.4** (whose
images are under the pre-rename `cheese-backend-py/*` path, set via the compose's
`BACKEND_IMAGE`/`FRONTEND_IMAGE` overrides; future releases build under `cheese/*`
and need no override). main is fusion — a different line — so main commits
correctly do not reach prod. `workflow_dispatch` has a `dry_run` input.

### etrip — release-gated

`deploy.yml` fires on a **published GitHub Release** (or manual
`workflow_dispatch`), then:
1. waits for the release commit's `build-backend` + `test` checks to be green,
2. **requires a human to approve** — it opens an issue and blocks until an
   approver (currently `andylizf`) comments `approve` (see
   [`trstringer/manual-approval`](https://github.com/trstringer/manual-approval)),
3. connects over Tailscale and SSHes to the box to run `deploy.sh <sha>`.

`workflow_dispatch` supports a `dry_run` input that connects and prints status
without deploying — use it to validate connectivity safely.

### Merge policy

The repo is **squash-only** (merge commits and rebase are disabled; branches
auto-delete on merge). Every PR lands as one squashed commit.

## CI runner pool (cheese-ci)

Heavy CI (`test.yml`'s migration-heads/test, `e2e.yml`'s e2e) runs on the
**cheese-ci** label — a pool of MicroCloud VMs (prod tenant, customer
`cheese-ci`, offering 103 standard-vm, 8c/8G/40G, one runner slot per machine:
`cheese-ci-runner-{1..3}` at `192.168.30.{3..5}`), NOT on the dev box. The box
keeps `cheese-dev` exclusively for what genuinely needs it (deploy, drift,
heartbeat, backup checks) — its single slot used to serialize every heavy job
(measured: 61% of CI time was queueing).

- Provisioning is scripted: `deploy/ci-runner/deps.sh` (build-essential +
  rustup — `uv sync` compiles the local srp_rs crate; weekly docker prune —
  nothing else reclaims layers here) then `deploy/ci-runner/provision.sh
  <name> <registration-token>` (runner + systemd service with Restart=always +
  OOMPolicy=continue — the dev-box runner once died silently for 25h after an
  OOM kill). Registration tokens: `gh api -X POST
  repos/SageSeekerSociety/cheese/actions/runners/registration-token`.
- Machines are created via the MicroCloud prod API
  (`http://microcloud-prod.119net.ghg.org.cn/microcloud`, Bearer = tenant
  secret, held by Lg / in the team chat — never committed). Reach the machines
  from the dev box (`ssh ci@192.168.30.x`, dev box's `~/.ssh/id_ed25519`).
  MicroCloud does not support resizing yet — pick sizes at creation; more
  machines = ask Lg for capacity.
- The pool shares one Proxmox disk with every other guest on pve119 (a single
  1.7 TB SAS logical volume, thin pool `local-lvm`, no NVMe on the box), so a
  service container's disk IO competes with MicroCloud provisioning, the
  observability stack and everything else there. The `test` job's integration
  two-thirds used to be bound by that disk's sync-write latency (2026-09-02: a
  4 KB `oflag=dsync` write took 3.5 ms on runner-3, IO stall 23% of the time,
  #668 needed five attempts to finish inside the 20-minute timeout while #667
  had taken 7 minutes on a quiet host). Since #670 the Postgres data directory
  of the `test` and `e2e` service containers is a 3 GB tmpfs: no disk in the
  path, and pytest went from 7m18s (#667, quiet host) to 4m58s (#670, busy
  host). A full run writes about 1 GB including WAL, measured locally; if the
  suite ever outgrows the tmpfs, Postgres fails with ENOSPC and the size in the
  workflow is the knob. The unit-test third never touched the disk and runs at
  the same pace either way.
- One runner slot per machine is deliberate: the workflows bind host ports
  5432/6379 for service containers, so two heavy jobs on one machine would
  collide (`port is already allocated`). Lifting this (常驻 PG/Valkey + drop the
  host port bindings) would double the pool to 6 slots on the same three
  machines — the open follow-up from the CI plan's P2.
- Liveness (alerting): `box-heartbeat.yml`'s `ci-pool` job proves **at least
  one** of the three is alive; `box-uptime.yml` alerts when it stays queued. It
  cannot see a partial outage, because `provision.sh` gives every machine the
  same single `cheese-ci` label. **Open ops step**: re-register each runner with
  `--labels cheese-ci,<name>` (`config.sh --replace`), then fan the heartbeat
  out to a matrix over the per-machine labels. Until that lands, a single dead
  pool machine shows up only as slower CI.
- Liveness (on demand): `box-diag.yml`'s `ci-pool` job **does** cover all three
  today — three concurrent jobs on the one shared label cannot land on the same
  machine, since each VM has a single slot. It prints hostname, disk, and
  dangling-volume count per machine; a job left **Queued** means the pool is
  short a machine. This trick is fine for a manual probe (it saturates the pool
  for ~20s) but not for the hourly heartbeat, which would then false-alarm
  whenever a merge burst holds the slots — hence the ops step above.

## Disk — what actually fills a box, and what may be deleted

Two mechanisms, deliberately different in kind:

- **`deploy/cheesex-disk-pressure-guard.sh`** (systemd timer, app box) is an
  *emergency brake*: at 85% it removes sandbox containers, and only after
  confirming no turn is active. It never touches build caches, and it is **not
  installed on the dev/agent boxes** — so on those boxes nothing was watching
  the things that actually fill them.
- **`deploy/dev-box-disk-cleanup.sh`** is the *routine* reclaim for any box.
  Reports by default; `--apply` deletes; `--self-test` checks its own arithmetic.

What filled `cheese-dev-env6-app` (measured 2026-08-11 at **92%**, 2.6G free):
docker build cache 3.0G (71 entries, none in use) · apt archives 1.7G · Go build
cache 1.3G · superseded vscode-server builds + VSIX cache 2.0G · rust toolchain
downloads 665M. Reclaiming exactly those took it to **65%** (~8.5G back).

The rule the script encodes: **only delete what a command can rebuild.** A
slower next build is an acceptable price; someone else's data is not. Dev boxes
are shared — env6 also hosts unrelated projects' containers — so the script
never touches images or volumes a running container uses, never touches
`~/.cache/ms-playwright` (e2e browser binaries, not refetched on demand), and
never touches anything under a project directory. `docker system prune -a` is
the wrong tool here for exactly that reason: it would delete a co-tenant's
stopped work.

To see disk across the CI pool without ssh, run `box-diag.yml`'s `ci-pool` job —
it prints hostname and disk per machine.

## Logs — reading a container that no longer exists

The dev/prod containers log to **journald**, not to a json-file. The difference
only matters after a deploy, and then it matters completely: a json-file lives
in the container's own directory, so `docker compose down` deletes it. Turns die
*during* deploys, so the failures most worth reading were the ones whose
evidence the deploy had already removed — that is how 257 turn failures on
2026-08-18 ended up permanently unclassifiable (#574).

`docker logs` works exactly as before for a *live* container. For one that is
gone:

```bash
sudo journalctl -t cheese-backend-1 --since "2 hours ago"   # by container name
sudo journalctl -t cheese-llm-tunnel -t cheese-api-front -f # the data plane
sudo journalctl -t cheese-backend-1 --since "09:00" --until "09:30"
```

`sudo` (or membership of `systemd-journal`) is required — an ordinary user sees
only their own messages, and the command returns empty rather than refusing,
which reads exactly like "there are no logs".

Retention is journald's default, `SystemMaxUse` = min(10% of the filesystem,
4 GB). Measured on dev, the backend writes ~61 MB/day, so 4 GB is on the order
of two months; the journal also gives back space automatically when the disk
runs low (`SystemKeepFree`), so it cannot be the thing that fills a box.

The standing data-plane pair (`cheese-llm-tunnel`, `cheese-api-front`) is
covered too. It is deployed by `deploy/llm-tunnel/up.sh` rather than
`deploy-docker.sh`, so its logs used to vanish whenever an operator re-ran that
script — including across the 「container up, pipe dead」 incident (#579), whose
first-hand account was exactly what nobody could read afterwards.

## Box ops runbook — changing backend env on a box

The one rule: **containers are only ever (re)created by `deploy/deploy-docker.sh`.**
A hand-run `docker compose up` looks equivalent but is not — the script exports
`IMAGE_TAG` / `SANDBOX_IMAGE` / `TMUX_SANDBOX_IMAGE` / `QUALITY_GATE_IMAGE`
pinned to the deploy SHA and sources `~/ops/deploy.env` (`COMPOSE_OVERLAYS`
etc.). Recreating without those pins silently flips the sandbox images to
nonexistent `:main` tags; on 2026-08-10 that broke every @芝士 turn on dev
("Agent 运行组件暂时缺失") until a proper redeploy.

Changing backend env (e.g. enabling an OAuth provider):

1. Edit the env file — dev: `/home/nictheboy/cheese-backend-py/backend/.env`
   (the compose `env_file` default; `BACKEND_ENV_FILE` overrides). **Back it up
   first** (`cp .env .env.bak-$(date +%Y%m%d-%H%M%S)`).
2. env_file is read at container **create** time — `docker restart` does NOT
   pick up changes. Recreate via the deploy script, re-deploying the sha that
   is already running:

   ```bash
   cd ~/actions-runner/_work/cheese/cheese
   SHA=$(docker inspect cheese-backend-1 --format '{{.Config.Image}}' | sed 's/.*://')
   bash deploy/deploy-docker.sh "$SHA"
   ```

3. **The pull will be denied** — the box holds no ghcr login outside workflow
   runs (`deploy-dev.yml` logs in per-run and logs out after). The cheapest fix
   is not to run the script by hand at all: **dispatch `Deploy (dev/test box)`
   manually** (Actions → that workflow → Run workflow → `main`). It logs into
   ghcr, runs this same script on the self-hosted runner that lives ON the box,
   and reads the very `.env` you just edited. Check first that `main`'s HEAD is
   the sha you want redeployed, since a dispatch deploys the ref's HEAD rather
   than what is currently running, and that HEAD is not a docs-only commit (the
   `Skip docs-only commits` step would no-op the deploy).

   To stay on the command line, log in and re-run step 2 unchanged:

   ```bash
   docker login ghcr.io -u <github user>   # password = PAT with read:packages
   ```

   `DEPLOY_APP_IMAGE_SOURCE=local` does **not** substitute for that login on a
   box that runs agents. It covers the two app images only; the agent runtime
   images are launched through docker.sock, so compose cannot hold them and the
   script pulls `SANDBOX_IMAGE` unconditionally whenever
   `AGENT_RUNTIME_IMAGES_REQUIRED` is true — which the subscription overlay
   makes it. Local mode gets you past `pull backend frontend` and straight into
   the identical denial one step later. Do not reach for
   `AGENT_RUNTIME_IMAGES_REQUIRED=false` to skip it either: that same block
   creates the image-retainer containers that keep the next `docker image prune
   -a` from reclaiming the sandbox image out from under every turn.

4. Verify: container env via `docker inspect` (parse the JSON — don't split on
   commas, values like `OAUTH_ENABLED_PROVIDERS=ruc,github_app` get chopped),
   then `curl -sf localhost:8081/healthz`.

Related: never hand-install files INTO a running container (they evaporate on
the next recreate); the gateway's own env keys follow the same
recreate-not-restart rule (`deploy/gateway/README.md`).

### Turning on the openviking memory backend (#187)

Everything except the key is already in place: `deploy-docker.sh` creates
`VIKING_HOST_PATH` (default `/home/nictheboy/cheese-viking`), hands it to uid
1000 with the other mounts, and compose bind-mounts it at `/data/viking` with
`OPENVIKING_DATA_DIR` pointed there. On the default `MEMORY_BACKEND=db` the
directory simply stays empty.

To switch a box over, add to its `backend/.env` and redeploy the running sha
(step 2 above — `docker restart` will not do):

```
MEMORY_BACKEND=openviking
OPENVIKING_LLM_API_KEY=<zhipu key>
OPENVIKING_EMBEDDING_API_KEY=<zhipu key>
```

Then import the facts the db backend already holds. The rows are kept as the
audit trail, so this is additive; imported ids are checkpointed on the volume,
so a re-run resumes instead of duplicating:

```bash
docker exec -w /app cheese-backend-1 \
  python scripts/migrate_memory_to_openviking.py --dry-run   # then without it
```

Two things to know before flipping it:

- **That directory IS the database.** Not Postgres, not the image. The PG backup
  job does not cover it; it has a backup line of its own
  (`cheese-viking-backup.timer`, every 6h, off-site to R2 — see
  `deploy/README-backup.md`). Installing that timer is part of the same manual
  runbook as the DB backup, so confirm it is actually running on this box before
  you flip the switch, not after.
- **The key buys extraction, not just vectors.** Every remembered fact costs a
  chat call (OpenViking's extractor) plus embedding calls. A key that only
  works on the embedding endpoint gets you a backend that stores nothing.

#### Checking that it actually came up

A wrong key does not raise anything. Extraction runs in a background task
inside OpenViking and the read path returns empty on error, so a rejected key
looks *exactly* like the db backend: no memories, no complaint. So the backend
calls both endpoints itself at boot and reports what happened. Two places to
look, in this order:

1. **The container log, right after the redeploy.** On success:

   ```
   memory: openviking model endpoints answered — embedding at …, chat at …
   ```

   On failure it is an `ERROR` line naming the endpoint, the HTTP status, the
   vendor's own message, and — the part that usually is the answer — *which
   setting the key came from*. `key from anthropic_auth_token` means the
   openviking keys were never set and it fell back to the agent gateway's
   token, which these endpoints will always reject.

2. **`/health/detailed`, any time after.** `checks.memory` carries the same
   verdict, per endpoint, with a `checked_at`; it is re-probed in the
   background every 5 minutes, so a key that expires later shows up here too.

   ```bash
   docker exec cheese-backend-1 curl -s localhost:8081/health/detailed \
     | jq .checks.memory
   ```

A failing memory check makes `/health/detailed` report `degraded`, and that is
all it does: it does **not** 503 `/readyz` and does **not** touch `/healthz`,
which is the container health check and therefore the deploy's rollback gate.
Turning "the model vendor is having a bad afternoon" into a rolled-back release
would cost more than the silence this check exists to break.

One more thing the probe catches that a key test would not: it compares the
width of the vector it gets back against `OPENVIKING_EMBEDDING_DIMENSION`.
OpenViking does not ask the endpoint for a specific width, so a model whose
native width differs from the configured one gives you a working key and a
broken index.

`backend/tests/integration/test_openviking_fake_endpoint.py` exercises this
whole path against a local stand-in endpoint, so the wiring is verifiable
without a key — but it says nothing about extraction quality, which is exactly
what the real key is for. The self-check has its own key-less coverage in
`backend/tests/integration/test_memory_endpoint_probe.py`.

### Turning on 记忆整理 / dreaming (#187)

Independent of the openviking switch above, and much cheaper to try: dreaming
reads the **db** backend's existing rows (`memory_entries`, `memory_dreams`), so
it needs no vendor key and does not care what `MEMORY_BACKEND` is set to. One
line, then the same redeploy as any other env change:

```
DREAM_ENABLED=true
```

Memory consolidation runs independently of resource cleanup:

- `SANDBOX_REAP_INTERVAL_SECONDS` remains the compatibility name for its interval
  (one hour by default); it no longer releases idle rooms.
- `SANDBOX_IDLE_HOURS` sets the required inactivity (eight hours by default).
  Existing idle time counts immediately; enabling dreams does not start a new wait.
- Each pass consolidates memory for at most `DREAM_MAX_PER_SWEEP` rooms (one by
  default). `DREAM_MIN_BLOCKS` defaults to twenty. Rooms keep their sessions.

To inspect the running job:

```bash
docker logs cheese-backend-1 --since 1h 2>&1 | grep 'shipped to device'
```

It **spends model budget** on a background trigger — about one agent turn per
organized topic. That is the whole reason it is off by default.

## Backups

Every box runs the same scripts (only the R2 prefix and host differ); details and
restore/DR runbook in [`deploy/README-backup.md`](../deploy/README-backup.md).

- **DB**: hourly `pg_dump -Fc` → verify → off-site to Cloudflare R2 (bucket
  `cheese-db-backups`). Prefixes: `db/` (dev), `prod-db/` (prod), `etrip/`.
- **Uploads** (prod, local disk): hourly additive mirror to R2 `prod-uploads/`.
- **Memory** (`VIKING_HOST_PATH`, the openviking tree): 6-hourly full tar →
  verify → off-site to R2 `viking/` / `prod-viking/`. Taken live, so a snapshot
  the backend wrote through is kept but named `-hot`. On `MEMORY_BACKEND=db` the
  tree is empty and the run is skipped, not failed.
- **Transcripts**: live collection writes immutable original byte ranges and source
  identity records directly to the private `TRANSCRIPT_S3_BUCKET`, alongside a
  PostgreSQL index. Existing tar archives remain in `TRANSCRIPTS_HOST_PATH`
  (`/home/nictheboy/cheese-transcripts`, mounted at `/data/transcripts`) and keep
  their hourly additive R2 mirror through `cheese-transcripts-mirror.timer`.
  Neither archived-room cleanup nor this mirror deletes retained transcript objects.
  See [archived-room cleanup deployment](../deploy/README-room-cleanup.md).
- **Monitoring** (code-enforced tripwires): `backup-freshness.yml` (daily, fails
  if last backup > 26h), `box-uptime.yml` (twice hourly at :25/:50, fails when
  the last **two** heartbeats both failed to complete — dev box, prod box, or
  the cheese-ci pool; one queued heartbeat is ordinary contention, not an
  outage, so it does not alert),
  `backup-restore-test.yml` (weekly, restores the newest dump into a throwaway
  postgres and fails if it doesn't come back).

The backup scripts are version-controlled, but **installing them on a box**
(copying to `~/ops/`, systemd timers, the R2 credential in `~/ops/r2.env`) is a
manual runbook, not automated provisioning — see `deploy/README-backup.md`.

## Database encoding — always create with an explicit `ENCODING 'UTF8'`

**Never let `initdb`/`CREATE DATABASE` pick the encoding from the ambient
locale.** A box with no `LANG` set gets `SQL_ASCII`, and a `SQL_ASCII` server
**rejects non-ASCII `\uXXXX` escapes inside `jsonb`** — which is how the first
GitHub profile with a Chinese display name 500'd the OAuth callback (#222 →
#233). Spell it out every time:

```sql
CREATE DATABASE <name> OWNER cheese ENCODING 'UTF8' TEMPLATE template0
  LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C.UTF-8';   -- PG 17
```

```bash
initdb -D "$PGDATA" --encoding=UTF8 --locale=C          # cluster level
```

Status: **dev was rebuilt as UTF8 on 2026-08-16; production
(`192.168.16.10`) is still `SQL_ASCII`** and is scheduled for the same rebuild.
The procedure, its failure modes, and what must not be edited in the script:
[`deploy/README-utf8-cutover.md`](../deploy/README-utf8-cutover.md). Application
code carries a stopgap for the meantime — `_json_dumps_utf8` in
`backend/app/core/db.py` sends JSON binds as raw UTF-8 rather than `\uXXXX`, and
raw bytes are accepted under either server encoding.

**No amount of testing catches this class of bug**, and that is worth knowing
before someone proposes "add a test so it can't happen again": every test
environment is already UTF8 — `.claude/scripts/dev-db.sh` runs
`initdb --encoding=UTF8 --locale=C`, and CI's postgres service container
(`paradedb`, a postgres-image derivative) inherits that image's UTF-8 locale
default. There *is* already a regression test for the Chinese-`jsonb` path
(`backend/tests/integration/test_github_account_link.py`) — but it only ever
runs against a UTF8 server, so it confirms the stopgap works and still tells you
nothing about the encoding of the box you deploy to. Server encoding is a
property of the box, not of the
code, so it can only be caught by asserting on the real box — or by never
creating a database without naming the encoding, which is the rule above.

## Access

- **ghg private net (dev/prod boxes)**: reachable via the OpenVPN split-tunnel
  into ghg. Credentials are not in this repo.
- **etrip**: SSH target (`ssh etrip`); GitHub Actions reaches it over Tailscale.
- Self-hosted runners pull outbound, so no public inbound is needed on the boxes.

## The backend runs as uid 1000 — and must keep doing so

The backend process and the agent inside a sandbox container share one git
store: `ws.sandbox_vcs_mounts` bind-mounts a project's main-repo `.git` into
every sandbox container, read-write — and **both sides commit into it**, since
the agent's own commit in its worktree is how a topic branch moves. git creates
object directories 0755 and loose objects 0444, owned by whoever wrote them, so
if the two sides run as different uids the second one can read every object and
add none: its commit fails on a directory it does not own, and the backend's
reads fail on a store it cannot enter (which git reports as `not a git
repository`, not as a permission error). Both directions have hit production —
the file panel 422ing for every topic in a project, and an agent whose work
could not leave the container.

`core.sharedRepository` is git's supported way to widen those modes, so this
constraint is negotiable — but nothing negotiates it today, so the fix is that
both sides ARE the same uid:

- sandbox: `node:22` + `USER node` = **1000**, started with `--user node`;
  `backend/sandbox/Dockerfile` asserts the uid at build time.
- backend: `backend/Dockerfile` creates its user with uid/gid **1000** to match.
- single source of truth: `app.domain.workspace.service.AGENT_UID`, pinned
  against both Dockerfiles by `tests/unit/test_workspace_uid_alignment.py`.

**Ops consequence.** The host bind mounts (`WORKSPACES_HOST_PATH`,
`UPLOADS_HOST_PATH`, `APPHOME_HOST_PATH` — the last one is the backend's `HOME`,
where git reads its global config from — `VIKING_HOST_PATH`, the openviking
memory tree, and `TRANSCRIPTS_HOST_PATH`, the transcript archives) hold files
written by the pre-2026-08 backend as uid 1001.
`deploy/deploy-docker.sh` hands them over once via
`deploy/fix-workspace-ownership.sh` before the swap —
idempotent, marker-guarded, and it runs the chown in a throwaway root container
(no sudo on the box). If a backend ever boots onto an unmigrated path it logs
`workspace_ownership` at ERROR naming the offending file; the fix is to run that
script and restart.

**The handover is the deploy's point of no return, so it runs last.** Every other
fallible step — image pulls, the runtime-image smoke test, `alembic upgrade head`
— aborts leaving the box exactly as it was; this one does not. It sits
immediately before `dc up` with nothing between them that can fail. It was third
of five until 2026-08-11, when the step after it aborted the deploy and left dev
holding a 1001 backend on a 1000 tree: `git` refused the workspaces as
`dubious ownership` and every project 422'd until the next deploy (run
31466502982). For the same reason a health-check rollback hands the mounts
*back* to `PREVIOUS_AGENT_UID` (1001) before starting the old image — but only
when that run actually moved them, which the script reports to the caller.
Rolling images back without rolling ownership back is not a rollback.

These scripts are exercised by `deploy/tests/` against a fake docker, gated in CI
by `.github/workflows/deploy-scripts-test.yml` (hosted, ~1m — it must not queue
behind the box's single runner). Before 2026-08-11 that harness existed but no
workflow ran it, which is how an untested ordering change reached the box with
six green checks.

## Gotchas — things that look renameable but are NOT

The GitHub repo was renamed `cheese-backend-py` → `cheese`. Several identifiers
keep the old name **on purpose** because changing them breaks or loses data:

- On-box paths `/home/nictheboy/cheese-backend-py`, systemd unit
  `cheese-backend-py.service` — filesystem/service identity, untouched by a repo
  rename.
- Docker **volume** names `cheese-backend-py_postgres_data` / `_uploads` on etrip
  — renaming them would point containers at empty volumes (data loss).
- Old GHCR packages `cheese-backend-py/*` still exist and hold pre-rename images
  (rollback to old shas still works).

## Known gaps / follow-ups

- **The dev box has one runner slot serving 11 workflows.** Since the heavy jobs
  moved to the cheese-ci pool this is the binding constraint on CI: measured over
  2026-08-10..11, `test.yml` finishes in 4.5m median / 5.1m p75 and `e2e.yml` in
  3.4m / 4.4m, while `build.yml` — whose three build jobs are still on
  `cheese-dev` — takes 9.9m median / 17.2m p75 / 20.7m p90, with `build-backend`
  queueing 14.1m at p75 and `build-frontend` 10.3m at the median. The box is not
  busy (15% utilisation over 19h, and 0 overlapping jobs, confirming the single
  slot) — it is serialised. It is also what every box-monitoring false alarm has
  been about, and a wedged job here held the slot for 8h on 2026-08-07. Adding a
  second labelled slot on the box is the cheapest fix; see
  `docs/topics/CI提速B-plan-job-挪-hosted.md`.
- **PITR** (second-level RPO) needs OS access to the PG hosts — blocked.
- **Off-site immutability**: R2 has no object-lock/versioning, and the box's
  token can delete objects, so a compromised box could wipe the off-site copies.
  Acceptable for now given the data size; revisit if the data grows critical.
