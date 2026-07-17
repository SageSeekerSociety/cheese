# Deploy unification — design draft

> Status: **draft for review** (2026-07-17). Not yet approved to build.
> Companion to [`infrastructure.md`](./infrastructure.md).

## Goal

One deploy model across all three environments — **Docker Compose pulling
per-commit GHCR images by sha** — instead of today's split (dev/prod build from
source on the box; etrip pulls images). One deploy codebase, one runtime, one
rollback semantic. dev auto-deploys on merge; prod (RUC) + etrip stay
release-gated with human approval.

## Why this is viable now

- `build.yml` **already** builds `backend`/`frontend`/`sandbox` images for every
  commit (`ghcr.io/sageseekersociety/cheese/<img>:<sha>`). dev/prod just don't
  use them — they re-build from source on the box.
- The historical reason dev/prod are bare-metal — "ONNX 绑核失败 in a container"
  (a PDF-parsing lib, `pymupdf4llm` → `pymupdf-layout` → `onnxruntime`, fails to
  pin threads under a restricted cpuset) — was **proven a non-fatal log**, not a
  real blocker. An isolated spike reproduced the exact error *and* parsed a 赛题
  PDF to correct markdown anyway. See "ONNX" below.
- The security objection to self-hosted runners is resolved for this **private**
  repo (the real runner risk is public-repo fork PRs, which don't apply).

## Non-goals / hard constraints

- **The database is never containerized.** RUC's DB is a separate managed host
  (`192.168.16.10`), dev's is `192.168.16.7`. App containers connect over
  `DATABASE_URL`. This is the **industry-standard "hybrid" pattern** (containerize
  the stateless app tier; keep the stateful DB external) — see "Database stays
  external" below; it's not a local quirk. (etrip runs its own in-container PG
  today — the anti-pattern this avoids; see the DB section.)
- **Uploads (赛题 PDFs) must survive every deploy** — they move to a named volume
  (or bind-mount to the existing `/home/nictheboy/shared/uploads`), never inside
  an image or a release dir.
- **No user-visible downtime**; every step must be rollback-able, and bare-metal
  stays as an instant fallback until the Docker model is proven.

## Target architecture (per box)

```
docker compose  →  pull ghcr.io/.../cheese/{backend,frontend,sandbox}:<sha>
                   backend (uvicorn)  ── DATABASE_URL ──▶ external managed PG
                   frontend (static)  ◀── host nginx (keeps cert/domain/OAuth)
                   sandbox (agent, where enabled)
                   uploads: named volume  →  backend STORAGE_LOCAL_PATH
```

- **Deploy = `docker compose pull <sha> && up -d && migrate`.** Rollback = point
  the tag back at the previous sha and `up -d`.
- **CPU limiting (if any) uses `cpus:` (CFS quota), NOT `cpuset`** — the spike
  showed `cpuset` triggers the (benign) ONNX affinity errors while `cpus:` quota
  is clean.
- **nginx stays on the host** (recommended): RUC's TLS cert, domain, and OAuth
  callback config already live in the host nginx; keep it and point it at the
  backend container's published port. Less churn, no cert migration. (Revisit
  containerizing nginx later if we want it uniform.)

## What's reused (not thrown away)

- The self-hosted **runners** (`cheese-dev`, `cheese-prod`) — they now run
  `docker compose` instead of a source build.
- The **gating**: `deploy-dev.yml` (auto), `deploy-prod.yml` (release + approval),
  `manual-approval`, wait-for-ci.
- `box-uptime.yml` monitoring; R2 backups (DB dump + uploads mirror).
- The uploads-external-path insight from the genesis cutover → becomes the volume.

## What's retired

- `deploy-blue-green.sh` (source build, symlink swap, release dirs).
- The permanent swap / temp-swap machinery — Docker pulls prebuilt images, so
  there's **no on-box build**, so vite never runs on the box, so swap is moot.
- The bare-metal genesis release-dir layout (image tags replace it).

## ONNX — the resolved blocker

Spike (local Docker, `onnxruntime` 1.27, both direct and via the real
`pymupdf4llm.to_markdown` app path), under `--cpuset-cpus=0`:

| config | affinity error | result |
|---|---|---|
| all cores, default threads | none | OK |
| `--cpuset-cpus=0`, default (= RUC) | logged (30×) | **PDF parsed OK anyway** |
| `--cpuset-cpus=0` + `intra_op_num_threads=1` | none | OK (clean logs) |
| `--cpuset-cpus=0` + `--cap-add=SYS_NICE` | still logged | cap does NOT help |
| `--cpus=1` (CFS quota) | none | OK (clean logs) |

Takeaway: the error is non-fatal; the feature works in a container. For clean
logs, limit CPU with `cpus:` not `cpuset`. No app-code change required.

## Migration plan (staged, each step rollback-able)

0. **Prerequisite — GHCR pull from the ghg boxes: VERIFIED (2026-07-17).** Tested
   live on the dev box (192.168.16.5): ghcr.io reachable (no GFW block, no
   proxy/VPN needed for the pull itself — the box already reaches github.com
   outbound for the runner), `docker login ghcr.io` succeeds, and
   `docker pull ghcr.io/sageseekersociety/cheese/backend:main` completed
   (exit 0, 6.04GB, 144s ≈ 43 MB/s). So a registry mirror/pull-through is NOT
   needed. Two notes: (a) the box needs **persistent GHCR read auth** — the test
   used a short-lived login; production should inject `GITHUB_TOKEN` via the
   runner or a long-lived read:packages token (provisioning, not a blocker); (b)
   the backend image is **6GB** — fine on first pull, and per-sha pulls only fetch
   changed layers, but worth slimming (multi-stage build) as a follow-up.
1. **dev first.** Bring up the Docker compose on the dev box alongside the running
   bare-metal service; cut over; validate (site 200, 赛题 PDF path, agent). Keep
   the bare-metal release as an instant fallback.
2. **prod (RUC).** After dev is proven: fresh backup → move uploads to a named
   volume (or bind to `~/shared/uploads`) → compose up pointing at the external PG
   → cut host nginx to the container → health + uploads + public-domain checks →
   rollback = restart the systemd service (kept until confident).
3. **etrip.** Already Docker; align its compose to the unified template.

## Database stays external (settled — the standard pattern, not a local quirk)

Running a production database in a container is broadly advised **against**:
containers are built for stateless, disposable workloads, while a DB is stateful
and interruption-sensitive. The recommended production shape is the **"hybrid
pattern": containerize the stateless app tier, keep the DB external** (managed
service or a dedicated host) — dominant in regulated/compliance contexts
precisely because it sidesteps stateful-in-orchestration complexity. Sources:
[vsupalov](https://vsupalov.com/database-in-docker/),
[Docker's own PostgreSQL guide](https://docs.docker.com/guides/postgresql/),
[Baeldung](https://www.baeldung.com/ops/docker-databases).

So dev + prod (RUC) are already correct — their PG lives on separate hosts and
the app connects via `DATABASE_URL`. **etrip's in-container PG is the anti-pattern
this avoids, not a second valid option.** Ideal end state: externalize etrip's PG
too (Aliyun RDS, or Postgres directly on the host).

**But that is deferred and decoupled from this work.** etrip's in-container PG is
live, holds data, and is already backed up (the etrip-backup job covers it).
Externalizing it is a **stateful data migration** (dump → external instance →
repoint `DATABASE_URL`), a distinct risk that must not ride along with app
containerization. Plan: **unify the app tier first; externalize etrip's DB as a
separate later step.** Tracked as tech debt, not a blocker for this design.

## Open questions (answer before building)

1. ~~**GHCR pull from ghg boxes**~~ — **VERIFIED** (see Migration plan step 0):
   dev box pulls `cheese/backend:main` fine (6GB/144s, no mirror needed).
2. **nginx** — keep on host (recommended) or containerize for uniformity?
3. **sandbox/agent container** — needed on dev/prod? (dev has the agent disabled
   today.)
4. **Decommission timeline** — how many green Docker deploys before we remove the
   bare-metal service + `deploy-blue-green.sh`?

## Rollback posture

Per box, the bare-metal systemd service and its last release dir stay in place
until the Docker model has N consecutive green deploys. Rollback at any point =
stop compose, start systemd. Only after confidence do we decommission bare-metal.
