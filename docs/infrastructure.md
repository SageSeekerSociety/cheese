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
on prod — the 赛题 PDFs).

The boxes ran bare-metal releases before this, and two traces of that are still
load-bearing rather than historical. `~/cheese-backend-py` is still a SYMLINK
into a release directory under `~/releases/`, and the deploy reads the backend
env file and the compose file through it — so neither the symlink nor that
release directory can be cleaned up as leftovers; moving them takes a deliberate
migration to a version-free path such as `~/ops/`. The old
`cheese-backend-py.service` systemd unit is also still installed and disabled;
it would start that same July release, so treat it as an artefact, not as a
rollback path. Rollback is `deploy-docker.sh` restoring the previous images.

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
manual-only, waits for the owner to atomically enter draining after all device
calls finish, and is never called by the normal app deployment workflows. It
reads the same box-local deploy environment and compose overlays as the app
deployment.

Each standing owner's database pool is set in the compose file
(`DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5` on `device-connection`; `3` and `2` on
`preview-connection`) and the backend's in `app/core/config.py`; together they
are sized so the two backends of a rollout plus BOTH owners fit a
100-connection server — 2×36 + 10 + 5 + 10 reserved for ops = 97, the
post-superuser budget exactly. That arithmetic is checked by
`backend/tests/unit/test_db_pool_fits_the_server.py`, which sums every owner
rather than only the device one; raising either owner pool without re-checking
it spends a connection the server does not have.

Neither side moves on its own: nothing a deploy changes reaches a running owner.
`ensure_device_connection_owner` in `deploy/deploy-docker.sh` looks only at
whether the container is running, and if it is, the deploy logs that it is
leaving it alone and returns without ever calling `docker compose up` on it. So
a change to the compose file's `device-connection` block (its pool, or an
`extra_hosts` entry) reaches the owner exactly as a change that lives only in
the image — `app/core/config.py`, or anything else the backend carries — does:
when the owner is started, which on an ordinary deploy happens only if the
deploy finds it absent, or otherwise through a release. So
on a box whose server still has the default 100 (prod, etrip), **a release that
raises the backend pool goes out owner-first**: until the owner is released it
holds the pool its running image was built with, and a backend rollout beside it
can ask the server for more connections than it has — the 2026-09-16 failure.
dev's server was raised to 200, so the order does not matter there.

Preview control traffic has its own boundary too, on the `preview-connection`
service. It owns the machine preview tunnels — both the `/preview/tunnel` and
`/api/preview/tunnel` spellings the helper dials — and the preview content hosts
(`preview-<uuid>.[-<resource>]<SITES_DOMAIN>`). A normal app release starts it if
it is absent and otherwise leaves its running container and image alone, exactly
like `device-connection`. One ordering rule is load-bearing: the owner is
started, and checked healthy, **before** api-front is pointed at it and before
any backend is replaced in owner mode. A machine helper only retries an upgrade
on 502/503/504; a 404 (which is what a half-switched route answers) makes it
exit for good, so the route must never land ahead of the owner that serves it.
If the owner cannot become healthy the deploy fails before it touches a route or
a backend.

Health is not enough on a subscription box. The owner reads static previews and
room files off `settings.workspace_root`, which the shared env file names as a
HOST path (a sandbox sibling resolves its own `-v <src>` against the host daemon,
so the backend mirrors the tree at that same absolute path —
`docker-compose.subscription.yml`). Miss that mirror on `preview-connection` and
the owner still answers `/healthz` while every preview fails on a path that was
never mounted. So after the health probe, and still before any route or backend
change, the deploy runs a one-shot check inside the owner against the same
`Settings` the app reads; an unmounted workspace or uploads path fails the deploy
with the routes untouched.

Updating the owner itself drops every live preview tunnel, and unlike the device
owner it has no drain endpoint — the helper redials on its own, so there is
nothing to wait for. It is therefore a separate manual operation: dispatch
**Release preview connection owner**, which runs
`deploy/release-preview-connection.sh`, pulls and force-recreates only
`preview-connection`, and waits for its health check. It demands
`PREVIEW_CONNECTION_INTERRUPT=1` as the operator's explicit acknowledgement that
live tunnels will drop and redial, rather than pretending it can wait for idle.

The whole cutover is behind a repo-level kill switch so reverting needs no box
access: `deploy/preview-connection.env` sets `PREVIEW_CONNECTION_MODE` to `owner`
or `legacy`. Flipping the file to `legacy` and letting one ordinary deploy run is
the supported revert, and it does three things in order, because doing them out of
order breaks live previews:

1. the backends roll back to `legacy` (they stop expecting an owner);
2. api-front moves the tunnel map and the content-host split back to app-router;
3. **the running owner is stopped and removed** (`retire_preview_connection_owner`
   in `deploy/deploy-docker.sh`).

Step 3 is not cosmetic: re-pointing the routes alone leaves helpers connected to
the owner while new tunnel requests land on an empty backend hub. On the way in
the order is the mirror image — while a legacy flip still finds a running owner,
the routes stay on that owner through the routing pass so a helper redial never
reaches a backend that has not rolled back yet. The mode is resolved before any
compose call, so a box that cannot reach an owner never renders one.

**A raw `git revert` of this change is NOT the revert.** It takes back
`deploy/preview-connection.env` and the scripts, but two things it does not touch
keep previews on the owner: `active/sites.conf` is box-generated, so the
`map $host $content_upstream` the deploy wrote into it stays there (the repo file
only supplies the template), and the orphaned `preview-connection` container
keeps running and keeps serving the tunnels whose `active/preview-routing.conf`
still points at it. If the revert is somehow required, follow it with a deploy in
`legacy` mode — or at minimum flip the switch first and revert second. A box that
has no `preview-connection` container and no split in its `sites.conf` is
unaffected either way.

Cloud-machine SSH forwards share this stable connection boundary. Normal app
deployments leave `cheese-cloud-control` running. To update it, dispatch
**Release cloud control** with the full SHA of a commit already merged into
`main`; the workflow waits for the same owner drain, restarts the service, and
confirms every previously online managed device reconnects with a new connection
generation before resuming execution. This maintenance causes one brief device
reconnection after active calls have finished.

**Application switches leave the persistent ingress running.** On boxes with
`ACTIVE_BACKEND_DIR`, `cheese-api-front` keeps its device, screen, execution,
model-tunnel, preview-tunnel and forge-event routes. Business requests pass to
the separate `cheese-app-router` nginx on loopback **:18085** (backend) and
**:18086** (frontend). Only app-router reads the changing `backend.conf` and
`frontend.conf` upstream files and reloads during an ordinary application
release.

The preview routes are the exception that moves only on a cutover. The tunnel's
target is a `map $host` in `active/preview-routing.conf`, written by
`deploy/llm-tunnel/configure-preview.sh` for the effective mode; the location
itself lives in `nginx.conf`, so reverting *that* file alone drops the route back
to the business backend — which is why the cutover is reverted by the kill switch
and not by reverting files (see above). Preview content hosts cannot be split with a `server_name`: the
box-generated `active/sites.conf` already owns them with `server_name DOMAIN
*.DOMAIN`, and no other server can beat a `*.DOMAIN` wildcard (a middle wildcard
is invalid and a regex loses). So the split lives *inside* that wildcard server:
`configure-sites.sh` writes a `map $host $content_upstream` whose default is the
active backend and whose regex sends `preview-<uuid>` hosts to the owner, and the
deploy re-renders `sites.conf` from the repo for the effective mode. A box with
no content domain, or one the operator disabled, is left alone. The owner's
internal RPC path (`/_internal/preview/`) is reachable only inside the compose
network, never through nginx.

A release switches app-router once. The backend has two slots, compose
services `backend` and `backend-b` on `BACKEND_PORT` and `BACKEND_PORT_NEXT`;
with `ACTIVE_FRONTEND_DIR` the frontend has two as well, `frontend` and
`frontend-b` on loopback `FRONTEND_SLOT_PORT` (18088) and `FRONTEND_PORT_NEXT`.
The deploy starts the idle slot of each on the new image, waits for its health
check, rewrites `backend.conf` and `frontend.conf` and reloads app-router once.
The old backend is told to hand its work over 5 seconds after that switch, and
`collab` is replaced at once, while the old frontend still runs. After a
31-second drain (`DEPLOY_DRAIN_SECONDS`) the old slots stop gracefully, in
parallel: the frontend's nginx gets up to 120 seconds to finish its requests, the
backend 60 seconds to finish its handover. The next release goes back into the
slots this one left. The `-b` services are written at deploy time from
compose's merged model, after every value the deploy exports, so they carry
every overlay and differ only in their port and in answering to the service's
network name (`backend`, dialled by `device-connection` and the office editor;
`frontend`, by the backend's docs index).

The containers therefore alternate between `cheese-backend-1` and
`cheese-backend-b-1` (likewise for the frontend); anything that needs one asks
`deploy/app-container.sh backend`. A release of a commit from before the two
slots, a revert or a manual dispatch, runs that commit's script. It installs
its own `app-router.conf` and reloads, which ends app-router's sockets 30
seconds later at that config's deadline, pulls and migrates, and then refuses
to switch while app-router names `BACKEND_PORT_NEXT` or `FRONTEND_PORT_NEXT`.
If a revert lands while the `-b` slots serve, no commit on main has the slots,
and every automatic deploy of main does that until someone dispatches the deploy
workflow on the last SHA that contains them (#2770), which moves back to
`backend` and `frontend`; the next release of main then goes through. From the first slots an older commit
releases as it always did. `deploy/tests/test-pre-slot-release.sh`
runs the last such commit's script against both states.

The box's own frontend ports, :8080 and :80, which the edge reaches directly,
belong to app-router: `frontend.conf`, written by
`deploy/llm-tunnel/configure-frontend-upstream.sh`, carries a server on them
that forwards to whichever frontend slot serves. They live in that file because
every `app-router.conf`, an older commit's included, includes it, so an older
commit's release keeps them; its own frontend switch drops them just before it
recreates the compose frontend on them, 31 seconds later. The first release with
two frontend slots adds them once the compose frontend that still published
them has stopped, which it gives 5 seconds since its traffic has moved; the
ports are closed from that stop to app-router's second reload of that release.
If app-router cannot take them, that frontend is started again to serve them
and the next release takes them at its switch. A one-off
`cheese-backend-next` or `cheese-frontend-next` left by an interrupted release
from before the slots is removed by the next release when app-router does not
send traffic to it, and stops the release when it does.

App-router's `worker_shutdown_timeout` is 240 seconds, longer than the
handover pause, the drain and the longer graceful stop together (5 + 31 + 120),
so the workers the one reload
retires keep their connections until the container they lead to stops. Device
and model connections bypass these workers. The persistent **:18080** entry
routes to app-router's frontend upstream. Frontends still reach APIs through
`API_UPSTREAM=host.docker.internal:8081`.

Each backend switch is also a handover of the running work. One backend at a
time owns it (the sessions it listens to, the turns it watches, the periodic
jobs), and a Postgres advisory lock says which (`app/core/ownership.py`). A
successor serves requests at once but holds the turns asked of it until the lock
reaches it. The outgoing backend, on SIGTERM,
lets the prompts it is still sending arrive, stops reading its sessions, lets go
of its turns without ending them and releases the lock. The successor then picks every running turn up
where it stands and starts any turn a message was left waiting for. The whole
of that fits in the backend's 60-second `stop_grace_period`, which is why the
old slot is stopped before it is removed: `docker rm -f` alone is a SIGKILL.
The in-place recreate on boxes without `ACTIVE_BACKEND_DIR` hands over the same
way, to the container that replaces it.

The first pipeline release installing app-router starts and checks it before
reloading the persistent ingress once. That migration can reconnect existing
devices; subsequent business releases do not reload their ingress. A box without
`ACTIVE_BACKEND_DIR` still recreates application containers in place.

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


`main` requires the GitHub Actions check `CI required` and the
[Main CI and merge queue ruleset](https://github.com/SageSeekerSociety/cheese/rules/23778889).
The ruleset has no bypass actors. Add a green PR through GitHub's merge queue UI
or the GraphQL `enqueuePullRequest` mutation. Repository auto-merge is enabled
so `gh pr merge` can request queue entry while required checks are pending.
The queue tests its changes against the latest main and preceding queued
changes before merging.

`.github/workflows/required-ci.yml` runs on both `pull_request` and `merge_group`.
It calls the existing suites selected by `.github/scripts/required-ci-paths.json`.
Documentation-only changes run repository guards. Failed scope detection, failed
or cancelled selected suites, and unexpected skips fail `CI required`. The same
job checks this repo's commit titles with `.github/scripts/check-commit-title.py`:
the PR title on a pull request, and every squash commit about to land in the
merge queue. Remote
execution acceptance remains advisory pending the stability target in #1279; the
MCP latest-version canary runs on schedule or manual dispatch.

Queue settings: four concurrent merge-group builds, ALLGREEN, squash merge, one
to five PRs per merge, no minimum-batch wait, and a 60-minute check-response timeout.
That timeout bounds a stalled queue; the feedback-time targets remain those in
#1279.

PRs opened before the gate was installed need a new pull-request event to report
`CI required`, for example after updating their branch or reopening the PR.

### Backend test execution

The fixture-derived layers remain `pure`, `contract` and `integration`.
`test.yml` runs pure and contract on separate hosted runners and integration on
four deterministic hash partitions. Each runner uses its own PostgreSQL and
Valkey containers.

The required gate compares executed JUnit node IDs with an independently
collected full-suite manifest. Every required case must run once, with no skips.
Artifacts retain each attempt; rerunning failed jobs uses the latest evidence
for each partition. Layer floors apply before partitioning. The existing Kotlin
exclusion and opt-in live Forgejo evaluations remain outside the required set.

Reproduce a partition from `backend/` with the same test services and pinned
tools as `test.yml`. Use a fresh output directory for each run:

```bash
uv run python -m pytest tests/ --ignore=tests/forgejo -m integration \
  -k 'not kotlin' -n 4 \
  -p scripts.ci_shard --ci-shard 0/4 \
  --ci-selection-output=../tmp/ci-selection \
  --junitxml=../tmp/ci-selection/results.xml
```

## CI runner pool (cheese-ci)

Every suite Required CI selects runs on GitHub-hosted Ubuntu runners, including
the CLI boot e2e and remote-execution acceptance. The organisation is on the
Free plan, whose documented limit is 20 concurrent hosted jobs; this repository
is not held to it. Over 2026-09-30/10-01 up to 61 hosted jobs ran at once, and
merge-queue jobs waited 0.1 minutes for a runner at the 90th percentile.
The **cheese-ci** label is a pool of MicroCloud VMs (prod tenant, customer
`cheese-ci`, offering 103 standard-vm, 8c/8G/40G, `cheese-ci-runner-{1..3}` at
`192.168.30.{3..5}`, two runner slots each), NOT on the dev box. The box
keeps `cheese-dev` exclusively for what genuinely needs it (deploy, drift,
heartbeat, backup checks) — its single slot used to serialize every heavy job
(measured: 61% of CI time was queueing).

- **Memory**: 8G per box, shared by its two runner slots, plus 4G of swap
  (`/swapfile`, in `/etc/fstab`, applied by `runner-swap.yml`). Without the swap
  two jobs that together want more than 8G did not slow down — the kernel killed
  a process, and not necessarily one belonging to the job that caused it:
  `oom-kill: cpuset=...runner-1.service, global_oom, task_memcg=...runner-1b.service,
  task=esbuild`. What that looks like from inside the job is `exit code 137`, or
  a Vite dev server that stops answering, or four pytest workers reporting "node
  down" at once — none of which name memory. Swap does not make a box bigger; it
  makes the same overload arrive as slowness, which is why `test` and `e2e` carry
  timeouts at roughly twice their median runtime rather than just above it.
- Provisioning is scripted: `deploy/ci-runner/deps.sh` (build-essential for
  `go test -race`, plus retirement of the legacy weekly Docker prune) then `deploy/ci-runner/provision.sh
  <name> <registration-token>` (runner + systemd service with Restart=always +
  OOMPolicy=continue — the dev-box runner once died silently for 25h after an
  OOM kill). Registration tokens: `gh api -X POST
  repos/SageSeekerSociety/cheese/actions/runners/registration-token`.
- Job-started disk guards reclaim host caches and warn below the free-space
  floor; they do not prune the shared Docker daemon or reject jobs at that
  threshold. Docker reclamation requires maintenance with both slots drained.
  Dispatch Runner maintenance with `operation=disk-guard`
  on main to update both slots on each CI box and retire the legacy prune cron
  and timer. Previous guard copies and schedule state are backed up on each host.
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
  of the `test` job's integration service container is a tmpfs (its size is
  set in `test.yml`): no disk in the path, and pytest went from 7m18s (#667, quiet host) to 4m58s (#670, busy
  host). A full run writes about 1 GB including WAL, measured locally; if the
  suite ever outgrows the tmpfs, Postgres fails with ENOSPC and the size in the
  workflow is the knob. The unit-test third never touched the disk and runs at
  the same pace either way.
- Two runner slots per machine, six in the pool. Slot 0 is `~/actions-runner`
  and slot 1 `~/actions-runner-1`, which is also what gives each its own
  `RUNNER_TEMP` and therefore its own uv venv rather than a
  concurrent `uv sync` into one. Postgres and Valkey are resident on the machine
  (`deploy/ci-runner/resident-services.sh`, on 5442/6389) and shared by its
  slots. What keeps
  two concurrent runs apart is the slot each declares in its runner `.env` — see
  `backend/tests/isolation.py` for the names it scopes, and note that the test
  harness creates its databases with `DROP DATABASE ... WITH (FORCE)`, so two
  runs handed one name delete each other's data mid-test.
- **Addressing one machine**: `provision.sh` gives each runner its box's own
  label beside the shared one — `cheese-ci-runner-1` and `cheese-ci-runner-1b`
  are both `cheese-ci-box-1`. `runs-on: [self-hosted, cheese-ci-box-1]`
  therefore reaches that machine and only that machine, and a job for a box
  whose slots are both busy stays **queued** rather than being served by another
  box. That is the only way to be sure a given machine was touched;
  `runner-swap.yml` uses it. An already-registered runner takes the label with
  `config.sh --replace`, which is how the three in the pool got theirs before
  provisioning assigned them — so a box rebuilt from an older `provision.sh`
  would come back reachable only through the shared label.
- **Fanning out over slots does not cover the pool.** The intuition that N jobs
  on the shared label must land on N different machines is false, in both its
  three-job and six-job forms: a job goes to whichever slot frees first, so one
  machine can take several while another, busy with a long `test`, takes none.
  Measured 2026-09-17 with six jobs: five landed on `cheese-ci-runner-2`, one on
  `cheese-ci-runner-3`, and `cheese-ci-runner-1` was never touched. This is why
  the hourly liveness check below asks the API instead of running a job per box.
- Liveness (alerting): `box-uptime.yml`'s `ci-pool` job names every machine that
  is not there, hourly, by ASKING the runner API rather than running a job on
  each — a job per machine would need a slot per machine every hour and would
  queue behind a merge burst, which the alert would have to read as death. The
  per-machine labels above are what let a half-dead machine (one slot gone) be
  named rather than averaged away. It says so in Feishu when
  `FEISHU_ALERT_WEBHOOK` is set, and reddens the run either way.
  `box-heartbeat.yml`'s `ci-pool` job remains as the "can the pool still run
  anything at all" check.
- Liveness (on demand): `box-diag.yml`'s `ci-pool` job prints hostname, disk,
  dangling-volume count, memory, swap and this boot's kernel OOM kills. It fans
  out over slots, so by the paragraph above it samples the pool rather than
  covering it — read the `host:` line of each job to see which machines you
  actually got, and dispatch it again for the ones you did not.

## Disk — what actually fills a box, and what may be deleted

Two mechanisms, deliberately different in kind:

- **`deploy/cheesex-disk-pressure-guard.sh`** (systemd timer, app box) is an
  *emergency brake*: at 85% it removes sandbox containers, and only after
  confirming no turn is active. It never touches build caches, and it is **not
  installed on the dev/agent boxes** — so on those boxes nothing was watching
  the things that actually fill them.
- **`deploy/dev-box-disk-cleanup.sh`** is the *routine* reclaim for any box.
  Reports by default; `--apply` deletes; `--self-test` checks its own arithmetic;
  `--needed` answers whether the box is above the mark at all and exits 0/1.
  `deploy/install-disk-cleanup-timer.sh` installs it, and
  `cheese-disk-cleanup.timer` runs it nightly above 75%.

  It watches **every filesystem it reclaims on, not just `/`** — including each
  temp root (`$TMPDIR`, `/var/tmp`) when that is a filesystem of its own. Which
  filesystems those are, and what the mark is, live in this script and nowhere
  else: the unit asks `--needed` rather than spelling out a `df` of its own.

  That indirection is the fix for 2026-09-22, when the dev box could not open a
  new topic. `/tmp` is a **32G tmpfs** — RAM, not the disk — and it is where
  every pytest run leaves its temp tree: 13G of them had accumulated, the
  largest single tree 5.3G. `/tmp` reached 100% while `/` sat at 51%, so every
  write under `/tmp` began failing with ENOSPC and the environment-preparation
  step for each new room died at startup. The nightly unit never ran —
  `systemctl status` says `Result: exec-condition`, because its condition was
  `df /` and the filesystem it asked about was healthy. The trees are
  regenerable by definition and nothing pruned them across runs, so the reclaim
  takes them now: the newest `CHEESE_PYTEST_TEMP_KEEP` (2) per user are left
  alone, and the rest must also be older than `CHEESE_PYTEST_TEMP_AGE_MINUTES`
  (360), so a suite still running is not swept out from under itself.
- **`deploy/reclaim-room-caches.sh`** is the *room-local* reclaim, and the only
  one of the three a deploy runs on its own (`deploy-docker.sh`, right before
  `DEPLOY OK`). Every room of a project now installs out of one store, so the
  copies a room made under its own HOME are read by nothing; the launcher drops
  them when that room next starts, and this reaches the rooms that never do.
  On dev, 2026-09-17, that was nearly all of them: of 102 checkouts, 3 had been
  touched in a week.

  Same idiom as the one above — reports by default, `--apply` deletes,
  `--self-test` checks itself — plus one thing neither of the others needs: it
  takes each room's own `.cheese-environment/lock`, the lock
  `agent/environment_runner.py` holds while a setup script installs, and skips a
  room that is installing right now. It never touches `.claude` (transcripts),
  `.cheese` (the room's spool and credentials) or `.local/share/uv` (the
  interpreter a venv points at by absolute path).

  What it CANNOT reclaim is the per-room Node install and the interpreter. Those
  need the room gone, which is archival's job — see
  `deploy/README-room-cleanup.md`.
- **`deploy/reclaim-legacy-room-checkouts.py`** is the third, and the deploy runs
  it too. Until #936 (2026-09-09) a room's working directory was
  `~/.cheese/work/<project>/<room>` and held a full checkout; that commit moved a
  room's cwd under its own home and the repository work to tasks, which
  `cheese worktree` puts in `<room home>/.cheese/tasks`. Nothing has written the
  old root since — `device_work_dir()` has had no caller, and
  `agent/resource_cleanup.py` already calls what is there "legacy checkouts". On
  dev, 2026-09-17, it was **107GB across 102 rooms**.

  Being unreachable is not what makes it safe to delete; being **published** is.
  A pre-#936 checkout can hold commits or edits that never left the box. So each
  directory goes through `check_no_writers` and `check_published` — imported from
  `resource_cleanup`, the module archival uses, so there is one definition of
  "safe to delete" rather than two — and anything that fails either check is kept
  and reported with the reason.

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
# by container name; on a box with an app-router the backend is one of two
sudo journalctl -t cheese-backend-1 -t cheese-backend-b-1 --since "2 hours ago"
sudo journalctl -t cheese-llm-tunnel -t cheese-api-front -f # the data plane
sudo journalctl -t cheese-metering-proxy -t cheese-gateway-litellm-1 --since today # model traffic
sudo journalctl -t cheese-backend-1 -t cheese-backend-b-1 --since "09:00" --until "09:30"
```

`sudo` (or membership of `systemd-journal`) is required — an ordinary user sees
only their own messages, and the command returns empty rather than refusing,
which reads exactly like "there are no logs".

Retention is set by `deploy/journald-cheese.conf`, which every deploy installs
as `/etc/systemd/journald.conf.d/cheese.conf`: up to 40 GB and a month, and
never below 40 GB free on the disk, whichever is tighter. It also raises
journald's limit on the number of journal files, which by default stops at
100 whatever their size: at about 50 MB a file that cap, not the 40 GB, set
dev's reach at about eight hours (2026-10-05). At journald's own
default (a tenth of the filesystem, at most 4 GB) dev kept about thirteen hours
on 2026-09-29, and the evidence for a failure was gone before anyone looked.
`sudo journalctl --disk-usage` and
`sudo journalctl -t cheese-backend-1 -t cheese-backend-b-1 -o short-iso | head -1` (the oldest line)
say how far back a box reaches now.

How long that is depends on what the app tier writes, so some lines are not
written at all:

- The HTTP clients' own request lines (`httpx`, `httpcore`) are logged only at
  WARNING. The backend calls the device connection tens of times a second, and
  those lines were nine in ten of its output.
- The device connection's access log skips internal calls and health probes
  that succeeded; a failed one is still logged.
- The backend runs without uvicorn's access log, because its own `req` line
  already records every request.

The nginx access logs (`cheese-api-front`, `cheese-app-router`, the frontend)
record the path and never the query string, because several URLs carry a
credential in it (the room chat socket's `?token=`, `/llm/tunnel?token=`).

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
   SHA=$(docker inspect "$(bash deploy/app-container.sh backend)" --format '{{.Config.Image}}' | sed 's/.*://')
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

## Backups

Every box runs the same scripts (only the R2 prefix and host differ); details and
restore/DR runbook in [`deploy/README-backup.md`](../deploy/README-backup.md).

- **DB**: hourly `pg_dump -Fc` → verify → off-site to Cloudflare R2 (bucket
  `cheese-db-backups`). Prefixes: `db/` (dev), `prod-db/` (prod), `etrip/`.
- **Uploads** (prod, local disk): hourly additive mirror to R2 `prod-uploads/`.
- **Transcripts** are not backed up. Claude Code sessions run on the central
  session host, and their transcripts stay there: the platform does not upload
  them. Archived-room cleanup keeps a room's transcripts compressed on that host
  for 30 days, then deletes them — see
  [archived-room cleanup deployment](../deploy/README-room-cleanup.md).
  Transcript objects uploaded to the private `TRANSCRIPT_S3_BUCKET` before
  uploading stopped are still there, indexed by the `raw_transcripts` table;
  nothing reads or writes either.
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

## Public edge: okcheese.com through Hong Kong, hand-managed

`okcheese.com`, `www.okcheese.com`, `hk.okcheese.com` and `docs.okcheese.com`
resolve to the etrip box (8.217.1.152), and TLS for them ends there. Their A
records in the Cloudflare zone are DNS only, not proxied. Its Caddy owns
public :443 with a layer4 router
([`scripts/ops/Caddyfile`](../scripts/ops/Caddyfile)) that hands those names
to Caddy's own HTTPS sites, which hold their certificates (ACME, renewed by
Caddy). The okcheese.com site redirects `www` and `hk` to the apex. Both sites
proxy plain HTTP to `127.0.0.1:18453` or `127.0.0.1:18454` here. Each is the
far end of a reverse SSH tunnel opened by the dev box, and both land on
api-front's plain listener `127.0.0.1:18080` there (set up by
`deploy/llm-tunnel/configure-frontend.sh`). The SSH tunnel encrypts that leg.
The dev box has no public inbound, so the site is up while at least one
tunnel is up.

`docs.okcheese.com` is the docs site's own host. Its site block has the same
`reverse_proxy` settings as the okcheese.com block but is separate from it,
because the okcheese.com block imports the snippet that answers `/assets/`
from the SPA's files on this box (see below), and the docs host's `/assets/`
are different files. Behind the tunnels the frontend container answers that
Host with the docs server (`frontend/nginx/docs/host.conf`). The dev box's
`~/ops/deploy.env` sets `DOCS_ORIGIN=https://docs.okcheese.com` and
`FRONTEND_URL=https://okcheese.com`, so `okcheese.com/docs/…` answers with a
301 to the same page on the docs host
([docs site](manual/dev/docs-site.md)).

Caddy keeps those upstream connections open and shares them between
visitors, so a new visitor's connection pays only its TLS handshake with
etrip, not tunnel round trips. Measured from etrip on 2026-10-04, a new
connection plus one request took 128 ms (p50 of 12) against 231-261 ms when
TLS ended on the dev box. Requests on an open connection cost the same either
way, one tunnel round trip.

The client address travels in `X-Forwarded-For`. Caddy drops whatever the
visitor sent there and writes the address the layer4 router's PROXY header
gave it; the dev box's front door appends its own and the backend resolves the
client through `FORWARDED_ALLOW_IPS`. The upstream is not TLS with PROXY
protocol to the dev box's `:18443`: with PROXY on, Caddy pools upstream
connections per client address, so every new visitor would open its own and
the round trips would come back.

Caddy spreads requests over both ports with `least_conn`. Neither line is
reliably the faster: on 2026-10-04 tunnel A (18453, over a dorm Unicom line)
answered in 47 ms one minute and 0.3-1.5 s the next, while tunnel B (18454,
over the campus line) held at 90-170 ms. A line that slows down
collects requests in flight, so new ones go to the other. Its active health
check asks each port for `/_internal/edge-health`, which the dev front door
answers with 404 itself, so the check covers the tunnel and the front door
without depending on an application rollout.

Each tunnel also still forwards `127.0.0.1:18443` (tunnel B: `18444`) to
api-front's TLS listener `127.0.0.1:18443`, which terminates TLS on the dev
box. No public traffic uses it. The watchdog below probes through it, and
the rollback at the end of this section sends the public names back to it.

The platform's own Cloud machines are on the dev box's private network, but
their default route reaches the public name the way any visitor's does: out
through router-2 to Hong Kong, then back here through a tunnel. So api-front
serves the same names over TLS on a third listener, `127.0.0.1:18445`, with
the same certificate and no PROXY protocol, and `deploy/cloud-control.py`
forwards each machine's `127.0.0.1:18445` to it on the SSH session that
already carries the machine's backend forward. A session's sandbox on such a
machine resolves the site's name to that forward
([`remote-execution.md`](remote-execution.md)), so a browser there keeps the
public origin and certificate. Measured from a Cloud machine
on 2026-10-05, a new connection plus `/api/healthz` took 25-40 ms this way
against 1.4-3.9 s through Hong Kong.

None of it is deployed by CI. The units below were installed by hand; change
them by hand, keep a timestamped copy of every file you edit next to it, and
note the rollback command before you start.

Before reloading Caddy on etrip, validate the edited file with

    /usr/local/lib/caddy-l4/caddy validate --config <file> --adapter caddyfile

The service runs that binary (set by the drop-in
`/etc/systemd/system/caddy.service.d/50-layer4.conf`), which has the layer4
plugin. The bare `caddy` on that box's PATH does not, so it rejects this
file at the `layer4` global option whatever else is in it.

Each tunnel travels inside TLS on :443, not as SSH on :22:

    dev box: ssh -R 127.0.0.1:18443:127.0.0.1:18443 -R 127.0.0.1:18453:127.0.0.1:18080
             (tunnel B: 18444 and 18454)
      -> tls-proxy.py (TLS, SNI relay.okcheese.com, pinned certificate)
      -> etrip :443, Caddy layer4 route for SNI relay.okcheese.com
      -> socat on 127.0.0.1:2222 (terminates that TLS)
      -> sshd :22, user hkrelay

Bare SSH from the dev box's egress to etrip :22 stalls in the key exchange for
several minutes at a time, several times a day. The TCP connection and the
server banner still get through, and other hosts reach the same sshd without
trouble, so neither the host nor the tunnel's keepalive settings are the cause.
TLS on :443 over the same egress keeps working through most of those periods,
but not all: on 09-30 and 10-01 that egress passed no data to etrip :443 for
8 to 17 minutes at a time.

So the two tunnels leave the dev box by different lines:

- Tunnel A (18443 and 18453) takes the default route: router-2 (192.168.16.2, Clash) hands
  it to the dorm OpenWrt's proxy (192.168.200.1:7891), which exits through a
  Beijing Unicom line.
- Tunnel B (18444 and 18454) binds source ports 41000-41099 (`TLS_PROXY_SOURCE_PORTS` in
  its unit), and a policy route on the dev box (`route-b.sh`, run before every
  start) sends those connections to 192.168.16.1, the 119pve host, which
  masquerades them out its campus uplink (seen outside as 211.71.28.46).
  119pve needs two raw-table rules that put these connections in conntrack
  zone 1 on `fwbr115i0`; without them, traffic routed through the host past
  the VM's firewall bridge never gets its NAT reply back.

A failure of either line leaves the other tunnel up. The campus uplink depends
on 119pve's campus portal login, which expired once (09-24), so it backs the
Unicom line up rather than replacing it.

| Box | Path | What it is |
|---|---|---|
| dev | `/etc/systemd/system/cheese-hk-relay-tls443.service` | tunnel A, 18443 and 18453 (enabled) |
| dev | `/etc/systemd/system/cheese-hk-relay-tls443-b.service` | tunnel B, 18444 and 18454, out the campus line (enabled) |
| 119pve | `/etc/network/interfaces`, `vmbr0` post-up | raw-table conntrack zone rules for source ports 41000-41099 |
| dev | `/usr/local/libexec/cheese-hk-relay/tls-proxy.py` | the tunnels' `ProxyCommand`; binds a source port from `TLS_PROXY_SOURCE_PORTS` when set |
| dev | `/usr/local/libexec/cheese-hk-relay/route-b.sh` | policy route for tunnel B: table 18443, rule priority 18443 |
| dev | `/home/nictheboy/.ssh/id_hkrelay`, `relay-okcheese.crt` | login key; the certificate `tls-proxy.py` pins etrip to |
| dev | `/etc/systemd/system/cheese-hk-relay-tls.service` | previous tunnel, bare SSH on :22; installed but disabled |
| dev | `/etc/systemd/system/cheese-hk-relay.service` | plain relay to `127.0.0.1:18080`; nothing routes there; installed but disabled |
| etrip | `/etc/systemd/system/cheese-ssh-relay-tls.service` | socat, TLS on 127.0.0.1:2222 to sshd |
| etrip | `/etc/systemd/system/cheese-relay-watchdog.service`, `/usr/local/libexec/cheese-hk-relay/relay-watchdog.sh` | frees a port held by a dead tunnel session; pages when both are down |
| etrip | `/etc/cheese-hk-relay/alert.env` | `FEISHU_ALERT_WEBHOOK`, the same webhook the backend alerts use |
| etrip | `/etc/ssl/relay/relay.pem` | certificate and key for `relay.okcheese.com` |
| etrip | `~hkrelay/.ssh/authorized_keys` | the key may only open `127.0.0.1:18080`, `:18443`, `:18444`, `:18453` and `:18454` |
| etrip | `/etc/ssh/sshd_config`, last block | `Match User hkrelay`: forwarding only, 10 s × 2 keepalive |

The TLS client is `tls-proxy.py` rather than `openssl s_client`. Used as a
`ProxyCommand`, `s_client` closes the connection within a second or two of a
few hundred kilobytes flowing through it, which turns every page load into a
reconnect.

Why two tunnels and a watchdog: when a tunnel's path dies, etrip's sshd can
keep its port bound for minutes. OpenSSH sends a ClientAlive probe only after
a full `ClientAliveInterval` with no activity on the session, and the
connections Caddy forwards into the port keep it busy, so the 10 s × 2
keepalive never fires. The session ends when the TLS connection underneath
finally errors. Until then the port still accepts connections, which hang, so
a dial-based health check calls it healthy, and the dev box's tunnel cannot
bind it again (`remote port forwarding failed for listen port 18443`).

- Caddy's active health check gives each port 2 s to answer and stops
  choosing a port that does not, and `least_conn` moves requests off a port
  where they pile up. A port that refuses the dial is marked down for 10 s,
  and `lb_try_duration` retries the other one.
- The watchdog sends an HTTPS request through 18443 and 18444 every 3 s. After
  three in a row get no HTTP answer, it kills the `sshd: hkrelay` process
  holding that port. That is the whole tunnel's session, so the tunnel's other
  port (18453 or 18454) closes with it; Caddy stops choosing it, and the dev
  box binds both again on its next attempt, about 25 s after the path died.
- When neither port has carried a request for 30 s, the site is down for
  everyone, and the watchdog posts to the Feishu alert group; it posts again
  with the duration when a port comes back.

Each tunnel restarts after 5 s, with no growing backoff: systemd never resets
its restart-step counter after a healthy run, so a backoff would add its
maximum to every later reconnect. A login that stalls after TLS is set up holds
one of etrip sshd's ten unauthenticated slots for up to two minutes; one
attempt per tunnel every 25 s or so stays under that.

Rollback, on the dev box. Second tunnel back onto the default route: restore
`cheese-hk-relay-tls443-b.service` from its `.bak-*` copy (drops
`TLS_PROXY_SOURCE_PORTS` and the `route-b.sh` step), then

    sudo ip rule del priority 18443; sudo systemctl daemon-reload && sudo systemctl restart cheese-hk-relay-tls443-b

Back to one tunnel:

    sudo systemctl disable --now cheese-hk-relay-tls443-b

Back to bare SSH on :22. That unit forwards 18443 only, so first put the
public names back on the TLS passthrough (below), then on the dev box:

    sudo systemctl disable --now cheese-hk-relay-tls443 cheese-hk-relay-tls443-b && sudo systemctl enable --now cheese-hk-relay-tls cheese-hk-relay

TLS back on the dev box, on etrip: install the `scripts/ops/Caddyfile` from
before the commit that moved TLS to etrip (its layer4 block routes SNI
`okcheese.com`, `www.okcheese.com` and `hk.okcheese.com` encrypted to
`127.0.0.1:18443` and `:18444` with `proxy_protocol v1`), keeping a dated copy
of the current one, then `sudo systemctl reload caddy`. That file has no site
for `docs.okcheese.com`; carry over the current docs block and its `http://`
redirect, or the docs host goes down with the rollback. The dev box's
certificate is still renewed daily, so that listener is ready.

The watchdog frees 18443 for the :22 tunnel as well, since it acts on whatever
`sshd: hkrelay` process holds the port. By hand: `sudo ss -ltnp | grep 18443`
on etrip names that process; killing it releases the port.

To check the public path from anywhere, run
[`scripts/ops/probe-okcheese.sh`](../scripts/ops/probe-okcheese.sh).

### Static assets are answered in Hong Kong

The page's built files under `/assets/` are hashed: a name never changes its
content. Since TLS ends on etrip, Caddy answers them from a copy on etrip
instead of sending each one through a tunnel, so a cold page load no longer
waits on the tunnels for about a megabyte of script, and a fresh service
worker's precache (about 6 MB gzipped, most of it again after every deploy)
stops competing with API calls inside them.

- `cheese-edge-asset-sync.service` runs
  [`scripts/ops/edge-asset-sync.py`](../scripts/ops/edge-asset-sync.py) as
  user `cheese-edge`. Every 15 s it reads the live `index.html` and `sw.js`
  through the same tunnel ports Caddy uses, and fetches every `/assets/` file
  they name that is missing from `/srv/okcheese-edge/assets/`. A file appears
  under its name only after its size matched the origin's `Content-Length`,
  with a `.gz` twin beside the compressible ones.
- [`scripts/ops/okcheese-edge-assets.caddy`](../scripts/ops/okcheese-edge-assets.caddy),
  installed as `/etc/caddy/okcheese-edge-assets.caddy` and imported inside the
  okcheese.com site, serves a file only when the copy has it. Anything else,
  including a file from a deploy the job has not caught up with yet, goes
  through the tunnels as before, so the job being down costs speed and nothing
  else.
- The job never deletes a file that is still referenced, and keeps every
  other one for 14 days, so a tab still running an older build finds its lazy
  chunks here after the dev box has replaced them.

Rollback: delete the `import` line from the okcheese.com site and
`systemctl reload caddy`; then `systemctl disable --now cheese-edge-asset-sync`.

The `reverse_proxy` of both sites carries `stream_close_delay 10m`. Without
it, a reload of this Caddy closes every WebSocket it proxies at once (room
sockets, device connectors, preview tunnels); with it, sockets open at the
reload stay up for up to ten minutes, and clients reconnect on their own
schedule.

## Beijing edge (pre-filing): the mainland entry for okcheese, hand-managed

A second edge sits in the mainland so that visitors in China stop paying the
Beijing → Hong Kong → Beijing detour. It is the Hong Kong design moved to
Beijing: an Aliyun lightweight server (`47.95.114.66`, Ubuntu 24.04) whose
Caddy ends TLS and sends plain HTTP into two reverse SSH tunnels from the dev
box, each landing on the dev front door's `127.0.0.1:18080`. The client address
travels in `X-Forwarded-For`, as through Hong Kong.

**It serves nothing public until the ICP filing for `okcheese.cn` is approved.**
A mainland server may not serve an unfiled domain on 80/443, so:

- Caddy listens only on `:8443` with its internal CA (`scripts/ops/Caddyfile.beijing`).
- Both firewalls (Aliyun's instance firewall and ufw) allow `:8443` only from a
  few test addresses; 80 and 443 are disabled in the Aliyun firewall, not deleted.
- No DNS record points at the box.

When the filing is approved: give the site a public certificate, move it to
`:443`, re-enable 80/443 in the Aliyun firewall, drop the `:8443` rules, and point
`okcheese.cn` at the box.

The tunnels are plain SSH on `:22`. The cross-border stalls that put the Hong
Kong tunnels inside TLS on `:443` do not apply inside the mainland.

| Box | Path | What it is |
|---|---|---|
| dev | `/etc/systemd/system/cheese-bj-relay-a.service` | tunnel A: Beijing `127.0.0.1:18463` → dev `127.0.0.1:18080` |
| dev | `/etc/systemd/system/cheese-bj-relay-b.service` | tunnel B: Beijing `127.0.0.1:18464` → dev `127.0.0.1:18080` |
| dev | `/usr/local/libexec/cheese-bj-relay/tcp-proxy.py` | the tunnels' `ProxyCommand`; binds a source port from `TCP_PROXY_SOURCE_PORTS` |
| dev | `/home/nictheboy/.ssh/id_bjrelay` | the tunnels' login key, used for nothing else |
| Beijing | `/etc/caddy/Caddyfile` | `scripts/ops/Caddyfile.beijing` |
| Beijing | `~bjrelay/.ssh/authorized_keys` | `restrict,port-forwarding`, may only listen on `127.0.0.1:18463` and `:18464` |
| Beijing | `/etc/ssh/sshd_config.d/60-bjrelay.conf` | no passwords; `bjrelay` gets remote forwarding only, 10 s × 2 keepalive |

Both tunnels currently leave through the default (Unicom) line. From the campus
exit that Hong Kong's tunnel B uses (table 18443 via 119pve), the Beijing address
times out on every port while Hong Kong answers, which points at 119pve's
raw-table rules being keyed to the Hong Kong address. Giving Beijing tunnel B the
campus line needs those rules extended on 119pve. Until then the two tunnels still
cover a port held by a dead session, but not a failed line.

Measured on 2026-10-05, new connection / reused connection, median of 12:

| From | Hong Kong edge | Beijing edge |
|---|---|---|
| a Beijing Mobile line | 263 / 121 ms | 77 / 35 ms |
| a Beijing Unicom line | 273 / 130 ms | 97 / 43 ms |

A reused request through one Beijing tunnel costs 18–20 ms. Through Hong Kong it
costs 117–152 ms.

Rollback, on the dev box: `sudo systemctl disable --now cheese-bj-relay-a
cheese-bj-relay-b`. Nothing else depends on the Beijing box, and the Hong Kong
units are untouched by it.

## Access

- **ghg private net (dev/prod boxes)**: reachable via the OpenVPN split-tunnel
  into ghg. Credentials are not in this repo.
- **etrip**: SSH target (`ssh etrip`); GitHub Actions reaches it over Tailscale.
- Self-hosted runners pull outbound, so no public inbound is needed on the boxes.

## Persistent volume ownership

`backend/Dockerfile` creates the backend user with uid/gid 1000. Existing host
bind mounts must remain accessible to that user, including files written by
older images as uid 1001.

`deploy/deploy-docker.sh` calls `deploy/fix-workspace-ownership.sh` to transfer
ownership when needed. The script uses a marker to skip completed migrations
and reports whether it changed ownership. A health-check rollback restores
the previous ownership before starting the old image when the deployment
performed that transfer. The ownership script is covered by `deploy/tests/`.

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
