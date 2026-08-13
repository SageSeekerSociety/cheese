#!/usr/bin/env bash
# Unified Docker deploy for the bare-metal-turned-container boxes (dev / prod RUC).
#
# Selects the per-commit images, migrates, and brings the app tier up — the
# Docker equivalent of deploy-blue-green.sh. Registry images remain the default;
# an operator may instead use images built locally on the box. The DB/Redis are
# EXTERNAL (this script never touches them); uploads live on a host path outside
# the containers. Rollback restores the exact image references captured below.
#
# Usage: deploy-docker.sh <image-sha> [compose-file]
#   image-sha:    the commit sha to deploy (pins backend+frontend together)
#   compose-file: default deploy/compose/docker-compose.base.yml
#
# Env (with safe defaults baked into the compose file):
#   BACKEND_ENV_FILE   path to the box's backend/.env   (default in compose)
#   UPLOADS_HOST_PATH  host dir holding uploads          (default in compose)
#   PROJECT            compose project name              (default cheese)
#   DEPLOY_APP_IMAGE_SOURCE  registry (default) or local. In local mode,
#                      BACKEND_IMAGE and FRONTEND_IMAGE must name existing images.
#   DEPLOY_PULL_ATTEMPTS         how many times to try each pull   (default 3)
#   DEPLOY_PULL_BACKOFF_SECONDS  waits between pull attempts       (default "5 15")
#   CI_POSTGRES_IMAGE / CI_REDIS_IMAGE  pinned refs to protect from image
#                      prune, see retain_ci_service_images() below (defaults
#                      match test.yml/e2e.yml `services:`)
#   PREVIOUS_AGENT_UID / PREVIOUS_AGENT_GID  the uid the pre-2026-08 images ran
#                      as (default 1001). Only used to hand the bind mounts back
#                      when a health-check rollback returns to such an image.
set -euo pipefail

# Box-local deploy overrides (chmod-600, NOT in git — same pattern as ~/ops/r2.env):
# e.g. prod (RUC) pins its release images (BACKEND_IMAGE/FRONTEND_IMAGE, old
# pre-rename path for v0.16.4) and its WS-edge override (VITE_CONNECTOR_WS_BASE).
if [ -f "$HOME/ops/deploy.env" ]; then
  set -a; . "$HOME/ops/deploy.env"; set +a
fi

SHA="${1:?usage: deploy-docker.sh <image-sha> [compose-file]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${2:-$HERE/compose/docker-compose.base.yml}"
PROJECT="${PROJECT:-cheese}"
HEALTH_ATTEMPTS="${DEPLOY_HEALTH_ATTEMPTS:-15}"
HEALTH_INTERVAL_SECONDS="${DEPLOY_HEALTH_INTERVAL_SECONDS:-3}"
APP_IMAGE_SOURCE="${DEPLOY_APP_IMAGE_SOURCE:-registry}"
# Three attempts, 5s then 15s apart. A pull dies here for transient reasons far
# more often than for real ones — a ghcr blip, or containerd failing to commit a
# downloaded layer because its ingest file vanished mid-pull (Deploy run #174,
# 2026-08-09: `rename …/ingest/…/data -> …/blobs/sha256/…: no such file or
# directory`). A fresh pull opens a NEW ingest record rather than re-committing
# the broken one, so a retry clears exactly that class of failure. The 20s total
# is deliberately small: a genuinely unavailable image must still fail this
# deploy quickly instead of holding the box's single shared runner.
PULL_ATTEMPTS="${DEPLOY_PULL_ATTEMPTS:-3}"
PULL_BACKOFF_SECONDS="${DEPLOY_PULL_BACKOFF_SECONDS:-5 15}"
# This box's only runner also serves test.yml/e2e.yml, whose `services:`
# postgres/redis images are pulled straight by Docker for the job and belong
# to no container once it ends — `docker image prune -af` below reclaims them
# like anything else unused, so the next CI run re-pulls from scratch. Kept
# in sync with those workflows' pinned digests; see retain_ci_service_images.
CI_POSTGRES_IMAGE="${CI_POSTGRES_IMAGE:-mirror.gcr.io/paradedb/paradedb:v0.18.8-pg16@sha256:8a14fee5257f554a60d70afc89490a6460a9833c3f7f99f7d88dbbf12e4042a2}"
CI_REDIS_IMAGE="${CI_REDIS_IMAGE:-mirror.gcr.io/valkey/valkey:8.0.2@sha256:57bcc49c6ade1813ef25206c571b65b66bb0094235ff7fb767941622892297d9}"
export IMAGE_TAG="$SHA"
export SANDBOX_IMAGE="${SANDBOX_IMAGE:-ghcr.io/sageseekersociety/cheese/sandbox:$SHA}"
export TMUX_SANDBOX_IMAGE="${TMUX_SANDBOX_IMAGE:-ghcr.io/sageseekersociety/cheese/sandbox-tmux:$SHA}"
export QUALITY_GATE_IMAGE="${QUALITY_GATE_IMAGE:-$TMUX_SANDBOX_IMAGE}"

# Optional overlay compose files layered on top of the base (space-separated).
# Bare names resolve against the committed compose dir; absolute paths pass
# through. Set in the box-local ops/deploy.env — e.g. dev adds the subscription
# overlay (docker.sock + workspace path parity, so a turn can spawn the agent
# sandbox); prod leaves it empty and is untouched. Committed overlays survive the
# runner's per-run re-checkout, so the deploy carries them itself — no box-side
# heal hack needed to re-apply them after each CI redeploy.
COMPOSE_OVERLAYS="${COMPOSE_OVERLAYS:-}"
_overlay_args=()  # populated after fail() exists so a missing overlay aborts loudly

# Only environments wired for sibling agent containers need the large runtime
# images. Dev's subscription overlay is that signal; production app-only boxes
# stay compatible with historical release SHAs that predate these image tags.
AGENT_RUNTIME_IMAGES_REQUIRED="${AGENT_RUNTIME_IMAGES_REQUIRED:-auto}"
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = auto ]; then
  case "$COMPOSE_OVERLAYS" in
    *docker-compose.subscription.yml*) AGENT_RUNTIME_IMAGES_REQUIRED=true ;;
    *) AGENT_RUNTIME_IMAGES_REQUIRED=false ;;
  esac
fi

dc() {
  docker compose -f "$COMPOSE" ${_overlay_args[@]+"${_overlay_args[@]}"} \
    -p "$PROJECT" "$@"
}
log() { echo "[deploy-docker $(date '+%H:%M:%S')] $*"; }
fail() { echo "[deploy-docker $(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

[ -f "$COMPOSE" ] || fail "compose file not found: $COMPOSE"

# Resolve overlays now that fail() is defined; abort if deploy.env names one that
# was not checked out, rather than silently deploying without the wiring.
for _f in $COMPOSE_OVERLAYS; do
  case "$_f" in
    /*) : ;;
    *)  _f="$HERE/compose/$_f" ;;
  esac
  [ -f "$_f" ] || fail "overlay compose not found: $_f"
  _overlay_args+=(-f "$_f")
  log "overlay: $_f"
done

# An unpinned SANDBOX_TOKEN makes every restart deafen every sandbox.
#
# `sandbox_auth.SANDBOX_TOKEN` falls back to a fresh random per PROCESS when the
# env does not pin it. That secret signs the scoped hook tokens, and a box bakes
# its token in at CREATION — so a restart re-signs with a new secret and every
# existing container's hook is 401'd at once. The turn then runs to its ceiling
# producing NOTHING: no output, no tools, indistinguishable from a model that
# never spoke, which is exactly why this cost a full day to find (#316).
#
# The backend now rebuilds such a box and says so at boot, but the box loses its
# tmux session to do it — so this is still worth catching one layer earlier,
# where someone is actually watching. Warn, never fail: a deploy that refuses to
# proceed over a config preference is a worse outage than the one it prevents.
#
# Only greps for the key's presence — the value is a secret and never printed.
# Not applicable to app-only boxes (prod), which run no sibling containers.
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = true ]; then
  _envf="${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}"
  if [ -r "$_envf" ] && ! grep -Eq '^[[:space:]]*SANDBOX_TOKEN=.+' "$_envf"; then
    log "WARNING: SANDBOX_TOKEN is not pinned in $_envf — the scoped-token"
    log "         signing secret is regenerated on every restart, so every"
    log "         existing sandbox's hook token stops verifying and its box"
    log "         must be rebuilt (losing that topic's tmux session). Pin it"
    log "         (and keep the metering proxy's CHEESE_SCOPED_SECRET equal)."
  fi
fi

case "$PULL_ATTEMPTS" in
  ''|*[!0-9]*|0) fail "DEPLOY_PULL_ATTEMPTS must be a positive integer (got: '$PULL_ATTEMPTS')" ;;
esac

# Disk watermark around every pull and every reclaim. Triaging the 2026-08-09
# pull failures from CI was impossible because the deploy log carried no disk
# information at all — answering "was the box simply full?" required SSH access
# nobody on the thread had. `df` is read-only and needs no privileges; the docker
# data root is where the image layers and the containerd ingest area actually
# live, so it is the number that matters, and it is reported separately only when
# it is a different filesystem from /.
log_disk() {
  local when="$1" path row key seen=""
  command -v df >/dev/null 2>&1 || return 0
  for path in / "${DEPLOY_DOCKER_DATA_ROOT:-/var/lib/docker}"; do
    row="$(df -Pk "$path" 2>/dev/null | awk 'NR == 2')" || row=""
    [ -n "$row" ] || continue
    key="$(printf '%s\n' "$row" | awk '{ print $1 "|" $6 }')" || continue
    case " $seen " in *" $key "*) continue ;; esac
    seen="$seen $key"
    log "disk ($when) $(printf '%s\n' "$row" | awk '{
      printf "%s on %s: size=%.1fGiB used=%.1fGiB avail=%.1fGiB use=%s",
        $1, $6, $2 / 1048576, $3 / 1048576, $4 / 1048576, $5 }')"
  done
}

# Keeps a CI service-container image (see CI_POSTGRES_IMAGE/CI_REDIS_IMAGE
# above) out of `docker image prune -af` below. `docker image prune` — even
# with `-a` — only ever removes images no container references, running or
# stopped; it has no visibility into container labels, so there is no prune
# --filter that reaches this from the image side. A stopped, labeled
# reference container is the mechanism, and it is not new: it is the exact
# trick build.yml's cheese-buildkit-image-retainer and this script's own
# prepare_image_retainer() below already use for the same reason. Opportunistic
# and best-effort — this script never pulls these images itself (they land on
# the box as a side effect of a CI job's `services:` block running here), so a
# deploy before CI has ever run on this box is simply a no-op, not a forced
# pull.
retain_ci_service_images() {
  local kind image retainer
  for kind in postgres redis; do
    case "$kind" in
      postgres) image="$CI_POSTGRES_IMAGE" ;;
      redis) image="$CI_REDIS_IMAGE" ;;
    esac
    docker image inspect "$image" >/dev/null 2>&1 || continue
    retainer="cheese-ci-${kind}-image-retainer"
    docker rm -f "$retainer" >/dev/null 2>&1 || true
    docker create --name "$retainer" \
      --label "com.cheese.image-retainer=ci-${kind}" \
      --entrypoint /bin/true "$image" >/dev/null 2>&1 || true
  done
}

# Reclaim is best-effort by construction: a deploy that is otherwise fine must
# never be failed by a prune, which is why every command here ends in `|| true`.
#
# prune_images is NOT unconditional. `docker image prune -af` keeps whatever a
# container references, so on the success path the images just deployed are safe.
# On a FAILURE path they are not running yet, and in DEPLOY_APP_IMAGE_SOURCE=local
# mode they were built on the box and cannot be pulled back — wiping them would
# force an operator to rebuild. Registry images are re-pullable by definition, so
# there the failure path prunes too; that is the whole point of this card.
reclaim_docker_disk() {
  local when="$1" prune_images="$2"
  if [ "$prune_images" = true ]; then
    retain_ci_service_images
    log "pruning all unused docker images ($when)…"
    docker image prune -af >/dev/null 2>&1 || true
  else
    log "skipping image prune ($when): locally built images are not re-pullable"
  fi
  # Keep a bounded hot cache on the persistent runner. Wiping it here made every
  # subsequent build re-download and rebuild all dependency layers; the build
  # workflow independently caps its named BuildKit cache at the same size.
  docker builder prune -af --max-used-space 6GB --min-free-space 10GB \
    >/dev/null 2>&1 || true
  log_disk "after reclaim, $when"
}

# Until 2026-08-09 both prunes lived at the very bottom of this script, so a
# deploy that died at the pull (line ~113) reclaimed nothing at all: every failed
# attempt left its 6-7GB of fresh layers behind. With no image-level reclaim
# anywhere else on the box — cheesex-disk-pressure-guard.sh only deletes sandbox
# containers and stale /tmp dirs, never images — that is a self-reinforcing loop:
# pull fails, nothing is freed, the disk gets tighter, the next pull is likelier
# to fail. The trap closes every exit path, including `fail`, `set -e` aborts and
# a runner cancelling the job (the concurrency policy does this routinely).
RECLAIM_PENDING=0
on_exit() {
  local rc=$?
  [ "$#" -eq 0 ] || rc="$1"
  trap - EXIT INT TERM
  if [ "$RECLAIM_PENDING" = 1 ]; then
    RECLAIM_PENDING=0
    log "deploy exiting with status $rc — reclaiming disk on the way out"
    if [ "$APP_IMAGE_SOURCE" = registry ]; then
      reclaim_docker_disk "failed deploy" true
    else
      reclaim_docker_disk "failed deploy" false
    fi
  fi
  exit "$rc"
}

# Backoff for the Nth failed attempt; the last configured value repeats if there
# are more attempts than delays.
pull_backoff_delay() {
  local attempt="$1" i=0 delay=0 candidate
  for candidate in $PULL_BACKOFF_SECONDS; do
    i=$((i + 1))
    delay="$candidate"
    [ "$i" -ge "$attempt" ] && break
  done
  printf '%s' "$delay"
}

# retry_pull <description> <command…>
#
# Keeps `set -euo pipefail` semantics intact: failures are captured explicitly
# rather than swallowed, and once the attempts are spent this still calls fail(),
# so the deploy exits non-zero exactly as it did before. The docker output of
# each attempt streams to the deploy log as it happens (capturing it would kill
# the progress lines that made the #174 stall diagnosable), so the summary names
# the attempts and their exit codes and points at that output.
retry_pull() {
  local what="$1"
  shift
  local attempt=1 rc=0 delay errors=""
  while :; do
    rc=0
    "$@" || rc=$?
    if [ "$rc" -eq 0 ]; then
      [ "$attempt" -eq 1 ] || log "$what: succeeded on attempt $attempt/$PULL_ATTEMPTS"
      return 0
    fi
    errors="${errors:+$errors; }attempt $attempt exited $rc"
    log_disk "$what failed, attempt $attempt"
    [ "$attempt" -lt "$PULL_ATTEMPTS" ] || break
    delay="$(pull_backoff_delay "$attempt")"
    log "$what: attempt $attempt/$PULL_ATTEMPTS failed (exit $rc); reclaiming disk, retrying in ${delay}s"
    # Idempotent and safe on a live box: prune only removes what nothing
    # references. Deliberately NOT touching containerd's ingest area, stopping
    # dockerd, or killing containers — a failed retry is recoverable, a wedged
    # box is not.
    if [ "$APP_IMAGE_SOURCE" = registry ]; then
      reclaim_docker_disk "before pull retry" true
    else
      reclaim_docker_disk "before pull retry" false
    fi
    sleep "$delay"
    attempt=$((attempt + 1))
  done
  fail "$what failed after $PULL_ATTEMPTS attempts ($errors); see each attempt's docker output above for the underlying error"
}

# Capture the exact currently-running images so rollback also works when an
# environment overrides BACKEND_IMAGE / FRONTEND_IMAGE instead of using IMAGE_TAG.
service_container() {
  dc ps -q "$1" 2>/dev/null | head -n 1 || true
}
service_image() {
  local container="$1"
  [ -n "$container" ] || return 0
  docker inspect --format '{{.Config.Image}}' "$container" 2>/dev/null || true
}

PREV_BACKEND_CONTAINER="$(service_container backend)"
PREV_FRONTEND_CONTAINER="$(service_container frontend)"
PREV_BACKEND_IMAGE="$(service_image "$PREV_BACKEND_CONTAINER")"
PREV_FRONTEND_IMAGE="$(service_image "$PREV_FRONTEND_CONTAINER")"
PREV_SHA=""
if [ -n "$PREV_BACKEND_CONTAINER" ]; then
  PREV_SHA="$(docker inspect \
    --format '{{ index .Config.Labels "com.cheese.image_tag" }}' \
    "$PREV_BACKEND_CONTAINER" 2>/dev/null || true)"
fi
if [ -z "$PREV_SHA" ]; then
  # Fall back to reading the image tag actually in use.
  PREV_SHA="$(dc images backend 2>/dev/null | awk 'NR==2{print $3}' || true)"
fi
log "deploying sha=$SHA (previous=${PREV_SHA:-none}) via $COMPOSE"

# From here on the script may leave pulled layers on disk, so every exit path
# owes the box a reclaim.
RECLAIM_PENDING=1
trap on_exit EXIT
trap 'on_exit 130' INT
trap 'on_exit 143' TERM

log_disk "before pull"

case "$APP_IMAGE_SOURCE" in
  registry)
    log "pulling app images…"
    retry_pull "image pull" dc pull backend frontend
    ;;
  local)
    [ -n "${BACKEND_IMAGE:-}" ] || \
      fail "BACKEND_IMAGE is required when DEPLOY_APP_IMAGE_SOURCE=local"
    [ -n "${FRONTEND_IMAGE:-}" ] || \
      fail "FRONTEND_IMAGE is required when DEPLOY_APP_IMAGE_SOURCE=local"
    log "verifying locally built app images…"
    docker image inspect "$BACKEND_IMAGE" >/dev/null 2>&1 || \
      fail "local backend image not found: $BACKEND_IMAGE"
    docker image inspect "$FRONTEND_IMAGE" >/dev/null 2>&1 || \
      fail "local frontend image not found: $FRONTEND_IMAGE"
    ;;
  *)
    fail "DEPLOY_APP_IMAGE_SOURCE must be registry or local (got: $APP_IMAGE_SOURCE)"
    ;;
esac

# Runtime images are launched on demand through docker.sock, so compose cannot
# pull or retain them for us. Pull both execution paths and run the same minimum
# binary check a real tmux turn needs BEFORE touching the live app.
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = true ]; then
  log "pulling agent runtime images…"
  retry_pull "SDK sandbox image pull ($SANDBOX_IMAGE)" docker pull "$SANDBOX_IMAGE"
  retry_pull "tmux sandbox image pull ($TMUX_SANDBOX_IMAGE)" \
    docker pull "$TMUX_SANDBOX_IMAGE"
  docker run --rm --entrypoint sh "$TMUX_SANDBOX_IMAGE" -c \
    'command -v tmux >/dev/null && command -v ttyd >/dev/null && command -v cheese >/dev/null' \
    || fail "tmux sandbox smoke test failed: $TMUX_SANDBOX_IMAGE"

  # `docker image prune -a` considers an on-demand image unused when no turn is
  # active. Stopped zero-cost containers make the desired runtime images explicit
  # roots, while still allowing superseded versions to be reclaimed each deploy.
  prepare_image_retainer() {
    local kind="$1"
    local image="$2"
    local next="${PROJECT}-${kind}-image-retainer-next"
    docker rm -f "$next" >/dev/null 2>&1 || true
    docker create --name "$next" \
      --label "com.cheese.image-retainer=$kind" \
      --entrypoint /bin/true "$image" >/dev/null \
      || fail "could not retain $kind runtime image: $image"
  }
  prepare_image_retainer sandbox "$SANDBOX_IMAGE"
  prepare_image_retainer tmux "$TMUX_SANDBOX_IMAGE"
fi

log_disk "after pull"

log "running DB migrations (alembic upgrade head)…"
# Production image ships no pyproject, so call alembic directly from the venv.
dc run --rm backend sh -c "alembic upgrade head" || fail "migration failed — aborting before swap"

# The backend now runs as the same uid as the sandbox's `node` (1000) so the two
# stop locking each other out of the shared jj store — see
# fix-workspace-ownership.sh. Files the old uid (1001) left behind have to change
# hands once, BEFORE the new backend starts and finds it cannot read them.
# Idempotent: a marker in each path makes later deploys a no-op.
# APPHOME matters as much as the workspaces themselves: it is the backend's HOME,
# and jj keeps its per-repo secure config there (the other half of the
# `.jj/repo/config-id` pointer).
#
# ORDER MATTERS, and it is why this block sits here rather than before the
# migration. Handing 2.2M files to another uid is the one step of this deploy
# that cannot be undone by simply not proceeding: whatever fails after it leaves
# the box holding a backend of one uid and a workspace tree of another, which is
# a project-wide 422 on the file panel. It ran before the migration and the
# credential check until 2026-08-11, when the credential check aborted
# deploy-dev *after* the trees had already moved and took dev down until the
# next deploy (run 31466502982). So: last fallible step first, irreversible step
# last, and nothing between it and `dc up` that can fail.
OWNERSHIP_REPORT="$(mktemp)"
OWNERSHIP_PATHS=(
  "${WORKSPACES_HOST_PATH:-/home/nictheboy/cheese-workspaces}"
  "${UPLOADS_HOST_PATH:-/home/nictheboy/shared/uploads}"
  "${APPHOME_HOST_PATH:-/home/nictheboy/cheese-app-home}"
)
OWNERSHIP_IMAGE="${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$SHA}"
OWNERSHIP_SECRETS="${GIT_CREDENTIALS_FILE:-/dev/null}"
log "checking workspace/uploads ownership…"
SECRET_FILE_PATHS="$OWNERSHIP_SECRETS" \
OWNERSHIP_REPORT_FILE="$OWNERSHIP_REPORT" \
"$HERE/fix-workspace-ownership.sh" \
  "$OWNERSHIP_IMAGE" \
  "${OWNERSHIP_PATHS[@]}" \
  || fail "workspace ownership migration failed — aborting before swap"
OWNERSHIP_MIGRATED="$(cat "$OWNERSHIP_REPORT" 2>/dev/null || echo no)"
rm -f "$OWNERSHIP_REPORT"

log "bringing up backend + frontend…"
dc up -d backend frontend || fail "compose up failed"

log "waiting for health…"
code=""
app_tier=""
for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
  sleep "$HEALTH_INTERVAL_SECONDS"
  app_tier="$(docker ps -a \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.Label "com.docker.compose.service"}}\t{{.Image}}\t{{.State}}\t{{.Status}}' \
    2>/dev/null || true)"
  if printf '%s\n' "$app_tier" | "$HERE/check-app-tier.sh" "$SHA" \
    >/dev/null 2>&1; then
    code=ok
    break
  fi
done

if [ "$code" != ok ]; then
  log "HEALTH CHECK FAILED"
  printf '%s\n' "$app_tier" | "$HERE/check-app-tier.sh" "$SHA" || true
  if [ -n "${PREV_SHA:-}" ] && [ "$PREV_SHA" != "$SHA" ]; then
    log "rolling back to ${PREV_SHA}…"
    # Rolling the images back without rolling the ownership back is not a
    # rollback: $PREV_SHA is by definition the image from before the uid change,
    # so it comes up on a tree it can no longer read and the box stays broken
    # while every signal says "rolled back". Only when THIS run actually moved
    # the trees — the report says so — is the old uid the right one to restore;
    # once the box is past the migration the previous image shares the current
    # uid and handing anything back would be the thing that breaks it.
    if [ "${OWNERSHIP_MIGRATED:-no}" = yes ]; then
      log "handing the bind mounts back to ${PREVIOUS_AGENT_UID:-1001} before starting $PREV_SHA…"
      AGENT_UID="${PREVIOUS_AGENT_UID:-1001}" \
      AGENT_GID="${PREVIOUS_AGENT_GID:-1001}" \
      SECRET_FILE_PATHS="$OWNERSHIP_SECRETS" \
      FORCE_OWNERSHIP_FIX=1 \
      "$HERE/fix-workspace-ownership.sh" \
        "$OWNERSHIP_IMAGE" \
        "${OWNERSHIP_PATHS[@]}" \
        || log "WARNING: could not hand the mounts back to ${PREVIOUS_AGENT_UID:-1001} — $PREV_SHA will come up on a tree it cannot read; re-run the deploy or chown by hand"
    fi
    if [ -n "$PREV_BACKEND_IMAGE" ] && [ -n "$PREV_FRONTEND_IMAGE" ]; then
      BACKEND_IMAGE="$PREV_BACKEND_IMAGE" \
        FRONTEND_IMAGE="$PREV_FRONTEND_IMAGE" \
        IMAGE_TAG="$PREV_SHA" \
        dc up -d backend frontend || true
    else
      IMAGE_TAG="$PREV_SHA" dc up -d backend frontend || true
    fi
  fi
  fail "deploy failed health check${PREV_SHA:+, rolled back to $PREV_SHA}"
fi

promote_image_retainer() {
  local kind="$1"
  local current="${PROJECT}-${kind}-image-retainer"
  local next="${current}-next"
  # `next` already protects the new image, so removing the old retainer never
  # leaves either deployment's image unreferenced during the handoff.
  docker rm -f "$current" >/dev/null 2>&1 || true
  docker rename "$next" "$current" \
    || fail "could not promote $kind runtime image retainer"
}
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = true ]; then
  promote_image_retainer sandbox
  promote_image_retainer tmux
fi

# Reclaim disk from superseded per-commit images: every deploy pulls a fresh
# 6-7GB image set and nothing ever pruned them — the dev box filled its disk to
# 100% (2026-07-18) and CD wedged for a day. Best-effort, never fails a deploy.
# NOT time-filtered: under a busy merge day every image is "too new" to prune
# and the disk fills anyway (happened twice on 2026-07-18/19 — 8 image sets in
# an afternoon). Keep what running containers and the two explicit runtime-image
# retainers use; rollback re-pulls superseded images from ghcr.
#
# Done inline rather than left to the EXIT trap so the reclaim and its disk
# watermark still print before "DEPLOY OK"; clearing RECLAIM_PENDING is what
# stops the trap from repeating it.
reclaim_docker_disk "successful deploy" true
RECLAIM_PENDING=0
echo "$(date -Iseconds) $SHA" >> "$HERE/deploy-docker.log"
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = true ]; then
  log "DEPLOY OK: sha=$SHA healthy; agent runtime images verified and retained"
else
  log "DEPLOY OK: sha=$SHA healthy"
fi
