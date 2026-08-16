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
| **dev / test** | xiaoyuer's test domain | `cheese-dev-env1-app` (192.168.16.5, ghg private net) | `cheese-dev-env1-postgresql` (192.168.16.7) | bare-metal (systemd + local `.venv`) | **auto on merge to `main`** |
| **prod (RUC)** | `cheese.ruc.edu.cn` | `cheese-prod-app` (192.168.16.8, ghg private net) | `cheese-prod-postgresql` (192.168.16.10) | bare-metal (systemd + local `.venv`) | **published GitHub Release → approval** |
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
`frontend` images, migrate, `up`, health-check, auto-rollback. The frontend image
bundles nginx (SPA + `/api` reverse-proxy), so there's **no host nginx** — the ghg
edge (APISIX) proxies to the box on **:8080** (frontend) + **:8081** (backend),
bound `0.0.0.0` (private net, safe). DB/Redis are **external** ghg hosts (the app
only holds `DATABASE_URL`/`REDIS_URL`); uploads bind-mount a host dir outside the
containers (`/home/nictheboy/shared/uploads` on prod — the 赛题 PDFs). Each box
was cut over from bare-metal once (`deploy/{dev,prod}-docker-cutover.sh`); the old
systemd service is kept **installed-but-disabled** as an instant rollback.

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

3. The box holds no ghcr login outside workflow runs (deploy-dev.yml logs in
   per-run). If the pull is denied, use local-image mode:

   ```bash
   DEPLOY_APP_IMAGE_SOURCE=local \
   BACKEND_IMAGE=ghcr.io/sageseekersociety/cheese/backend:$SHA \
   FRONTEND_IMAGE=ghcr.io/sageseekersociety/cheese/frontend:$SHA \
   bash deploy/deploy-docker.sh "$SHA"
   ```

4. Verify: container env via `docker inspect` (parse the JSON — don't split on
   commas, values like `OAUTH_ENABLED_PROVIDERS=ruc,github_app` get chopped),
   then `curl -sf localhost:8081/healthz`.

Related: never hand-install files INTO a running container (they evaporate on
the next recreate); the gateway's own env keys follow the same
recreate-not-restart rule (`deploy/gateway/README.md`).

## Backups

Every box runs the same scripts (only the R2 prefix and host differ); details and
restore/DR runbook in [`deploy/README-backup.md`](../deploy/README-backup.md).

- **DB**: hourly `pg_dump -Fc` → verify → off-site to Cloudflare R2 (bucket
  `cheese-db-backups`). Prefixes: `db/` (dev), `prod-db/` (prod), `etrip/`.
- **Uploads** (prod, local disk): hourly additive mirror to R2 `prod-uploads/`.
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

The backend process and the agent inside a sandbox container share one jj store:
`ws.sandbox_vcs_mounts` bind-mounts a project's main-repo `.jj`/`.git` into every
sandbox container, read-write. jj writes its store objects — `.jj/repo/config-id`
above all — with a **hardcoded 0600**, so if the two sides run as different uids,
whichever writes first locks the other out of every jj command
(`Failed to determine the secure config for a repo … Permission denied`). That is
not a theoretical risk: both directions have hit production — the file panel
422ing for every topic in a project, and jj being unusable inside sandboxes.

umask, a shared group, and default ACLs are all powerless against a mode the
writer sets explicitly. The only fix is that both sides ARE the same uid:

- sandbox: `node:22` + `USER node` = **1000**, started with `--user node`;
  `backend/sandbox/Dockerfile` asserts the uid at build time.
- backend: `backend/Dockerfile` creates its user with uid/gid **1000** to match.
- single source of truth: `app.domain.workspace.service.AGENT_UID`, pinned
  against both Dockerfiles by `tests/unit/test_workspace_uid_alignment.py`.

**Ops consequence.** The host bind mounts (`WORKSPACES_HOST_PATH`,
`UPLOADS_HOST_PATH`, `APPHOME_HOST_PATH` — the last one is the backend's `HOME`,
where jj keeps the per-repo secure config that `config-id` points at) hold files
written by the pre-2026-08 backend as uid 1001. `deploy/deploy-docker.sh` hands
them over once via `deploy/fix-workspace-ownership.sh` before the swap —
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

`GIT_CREDENTIALS_FILE` **is** handed over with everything else, mode untouched
(600 before, 600 after). It is operator-owned and outside git, but it is mounted
read-only into the backend at a fixed path, so its owner has to *be* the
backend's uid — it was 1001 only because the backend was. An earlier version of
this script deliberately refused to move it and only checked readability; that
protected nothing and stopped the deploy on a step whose only remedy was a sudo
nobody in the deploy path has. The readability check survives and still fails the
deploy loudly with the exact `chown` to run, but it now runs *after* the
handover, so it only fires on something a chown cannot fix. The default
`/dev/null` (feature off) is a device node and is skipped, never chowned.

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
