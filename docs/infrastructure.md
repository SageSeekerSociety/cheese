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
  collide (`port is already allocated`).

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
  if last backup > 26h), `box-uptime.yml` (hourly, fails if a box's runner goes
  offline), `backup-restore-test.yml` (weekly, restores the newest dump into a
  throwaway postgres and fails if it doesn't come back).

The backup scripts are version-controlled, but **installing them on a box**
(copying to `~/ops/`, systemd timers, the R2 credential in `~/ops/r2.env`) is a
manual runbook, not automated provisioning — see `deploy/README-backup.md`.

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
`UPLOADS_HOST_PATH`) hold files written by the pre-2026-08 backend as uid 1001.
`deploy/deploy-docker.sh` hands them over once via
`deploy/fix-workspace-ownership.sh` before the swap — idempotent, marker-guarded,
and it runs the chown in a throwaway root container (no sudo on the box). If a
backend ever boots onto an unmigrated path it logs `workspace_ownership` at ERROR
naming the offending file; the fix is to run that script and restart.

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

- **PITR** (second-level RPO) needs OS access to the PG hosts — blocked.
- **Off-site immutability**: R2 has no object-lock/versioning, and the box's
  token can delete objects, so a compromised box could wipe the off-site copies.
  Acceptable for now given the data size; revisit if the data grows critical.
