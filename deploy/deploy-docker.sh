#!/usr/bin/env bash
# Unified Docker deploy for the dev and prod (RUC) boxes.
#
# Selects the per-commit images, migrates, and brings the app tier up.
# Registry images remain the default;
# an operator may instead use images built locally on the box. The DB/Redis are
# EXTERNAL (this script never touches them); uploads live on a host path outside
# the containers. Rollback restores the exact image references captured below.
#
# Usage: deploy-docker.sh <image-sha> [compose-file]
#   image-sha:    the commit sha to deploy (pins backend+frontend together)
#   compose-file: default deploy/compose/docker-compose.base.yml
#
# Env (with safe defaults baked into the compose file):
#   APP_RELEASE        the full commit this release ships. The backend reports
#                      it at /api/version: the image under <image-sha> can be an
#                      older commit's, retagged because nothing in it changed.
#                      Ignored when BACKEND_IMAGE names the image, which then
#                      reports the commit it was built from.
#   BACKEND_ENV_FILE   path to the box's backend/.env   (default in compose)
#   UPLOADS_HOST_PATH  host dir holding uploads          (default in compose)
#   CLAUDE_CACHE_HOST_PATH  host dir holding the claude binaries served to
#                      enrolling machines (same treatment; default in compose)
#   TOOLCHAIN_CACHE_HOST_PATH  host dir holding what a connector places on a
#                      machine it enrols (same treatment; default in compose)
#   PROJECT            compose project name              (default cheese)
#   ACTIVE_FRONTEND_DIR  optional api-front active directory. Enable only after
#                      ingress targets FRONTEND_PROXY_PORT (default 18080).
#   FRONTEND_PORT_NEXT  the second frontend slot's port (default 18084, loopback only)
#   DEPLOY_APP_IMAGE_SOURCE  registry (default) or local. In local mode,
#                      BACKEND_IMAGE, FRONTEND_IMAGE and COLLAB_IMAGE must name
#                      existing images.
#   DEPLOY_PULL_BUDGET_SECONDS   how long the pulls keep being retried, counted
#                                from the first pull (default 600; see
#                                registry-retry.sh)
#   DEPLOY_PULL_BACKOFF_CAP_SECONDS  the longest wait between attempts (default 30)
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
# How long a separately released owner is given to answer /healthz after the
# deploy starts it. Only shortened in the deploy-scripts tests, which drive the
# "owner never becomes healthy" path without waiting the real minute.
OWNER_HEALTH_SECONDS="${DEPLOY_OWNER_HEALTH_SECONDS:-60}"
APP_IMAGE_SOURCE="${DEPLOY_APP_IMAGE_SOURCE:-registry}"
# This box's only runner also serves test.yml/e2e.yml, whose `services:`
# postgres/redis images are pulled straight by Docker for the job and belong
# to no container once it ends — `docker image prune -af` below reclaims them
# like anything else unused, so the next CI run re-pulls from scratch. Kept
# in sync with those workflows' pinned digests; see retain_ci_service_images.
CI_POSTGRES_IMAGE="${CI_POSTGRES_IMAGE:-mirror.gcr.io/paradedb/paradedb:v0.24.0-pg17@sha256:663ecc6dac5165ae2a664c7bd16fb8d8970867e89006ae4f6aa9cd26b1a2a3a4}"
CI_REDIS_IMAGE="${CI_REDIS_IMAGE:-mirror.gcr.io/valkey/valkey:8.0.2@sha256:57bcc49c6ade1813ef25206c571b65b66bb0094235ff7fb767941622892297d9}"
export IMAGE_TAG="$SHA"
# An image named outright is not this release's, so it must not claim to be.
if [ -n "${BACKEND_IMAGE:-}" ]; then
  APP_RELEASE=""
fi
export APP_RELEASE="${APP_RELEASE:-}"

# Optional overlay compose files layered on top of the base (space-separated).
# Bare names resolve against the committed compose dir; absolute paths pass
# through. Set in the box-local ops/deploy.env — e.g. dev adds the subscription
# overlay (workspace path parity and the metering proxy's log and CA); prod
# leaves it empty and is untouched. Committed overlays survive the
# runner's per-run re-checkout, so the deploy carries them itself — no box-side
# heal hack needed to re-apply them after each CI redeploy.
COMPOSE_OVERLAYS="${COMPOSE_OVERLAYS:-}"
case " $COMPOSE_OVERLAYS " in
  *docker-compose.forgejo.yml*) ;;
  *) COMPOSE_OVERLAYS="${COMPOSE_OVERLAYS:+$COMPOSE_OVERLAYS }docker-compose.forgejo.yml" ;;
esac
# The api pool beside each backend slot's realtime process
# (backend/app/core/process_role.py, backend/app/serve.py): how many worker
# processes answer the plain reads app-router sends them. 0, the default, runs
# none, publishes no pool port and routes every request to the backend as
# before. dev's release workflow sets it (.github/workflows/deploy-dev.yml).
API_POOL_WORKERS="${API_POOL_WORKERS:-0}"
case "$API_POOL_WORKERS" in ''|*[!0-9]*) API_POOL_WORKERS=0 ;; esac
export API_POOL_WORKERS
if [ "$API_POOL_WORKERS" -gt 0 ]; then
  case " $COMPOSE_OVERLAYS " in
    *docker-compose.api-pool.yml*) ;;
    *) COMPOSE_OVERLAYS="$COMPOSE_OVERLAYS docker-compose.api-pool.yml" ;;
  esac
fi
_overlay_args=()  # populated after fail() exists so a missing overlay aborts loudly
FORGE_CUTOVER_PENDING="${APPHOME_HOST_PATH:-/home/nictheboy/cheese-app-home}/forge-migration/cutover-pending"
FORGE_EXECUTOR_RESTART="$(dirname "$FORGE_CUTOVER_PENDING")/restart-host-executor"

dc() {
  docker compose -f "$COMPOSE" ${_overlay_args[@]+"${_overlay_args[@]}"} \
    ${_slot_args[@]+"${_slot_args[@]}"} -p "$PROJECT" "$@"
}

ensure_device_connection_owner() {
  local container started=false waited=0
  container="$(dc ps -q device-connection 2>/dev/null | head -n 1 || true)"
  if [ -n "$container" ] && [ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" = true ]; then
    log "leaving device connection owner $container running across this app release"
  else
    log "starting the independently released device connection owner"
    dc up -d --no-deps device-connection \
      || fail "device connection owner did not start; the running backend was not touched"
    started=true
  fi
  while [ "$waited" -lt "$OWNER_HEALTH_SECONDS" ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${DEVICE_CONNECTION_PORT:-18083}/healthz" >/dev/null 2>&1; then
      [ "$started" = false ] || log "device connection owner is healthy"
      return
    fi
    sleep 2
    waited=$((waited + 2))
  done
  fail "device connection owner is not healthy; the running backend was not touched"
}

preview_connection_container() {
  dc ps -q preview-connection 2>/dev/null | head -n 1 || true
}

preview_connection_running() {
  local container
  container="$(preview_connection_container)"
  [ -n "$container" ] \
    && [ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" = true ]
}

# ---- Owners behind the schema ----
# The connection owners outlive app releases, so this release's migrations run
# under owners still on an older image. Their models map every column their
# tables had when that image was built, and a query that loads a model asks for
# all of them: once a migration drops one, every such query fails. #3411 dropped
# topics.compute_config and cloud_host_homes on 2026-10-11, and the dev device
# owner answered ~1500 executor calls with 500 until it was released by hand.
# Keeping every owner query column-limited (app/domain/device/owner_reads.py)
# had not prevented it.
#
# So before migrating, the deploy reads what the pending migrations drop,
# rename or alter (deploy/migration-touches.py, run in this release's image
# against the database) and what each running owner's image maps
# (deploy/schema-map.py, run in the owner). An owner is released in this deploy
# only when the two meet: the device owner is drained before the migrations, so
# its callers wait and retry rather than fail, and both are recreated on this
# release's image after them. Not before: the new image may need what the
# migrations add. A release that only stops mapping a column (the first half of
# a drop, .claude/rules/migrations.md) leaves the owners running; the deploy
# that carries the drop releases whichever owner still maps it.
#
# An owner that already maps something the database lacks is released the
# same way. Migrations commit one by one, so a deploy whose second migration
# failed keeps the first one's drop and resumes the old owner on it; the next
# deploy no longer lists that migration as pending, and only the database
# itself says the owner is behind.
SCHEMA_HELD_OWNERS=""
PENDING_TOUCHES=""

# schema_map <command…>: run deploy/schema-map.py in an image, one table or
# table.column per line.
schema_map() {
  "$@" python - < "$HERE/schema-map.py" | sed -n 's/^schema-map //p' | LC_ALL=C sort -u
}

# What the pending migrations touch, one table, table.column or * per line, and
# a `pending <revision>` line for each of them. An answer that did not finish
# is read as touching everything: unreadable is not proof that nothing moves.
read_pending_touches() {
  local answer
  answer="$(dc run --rm --no-deps -T backend python - < "$HERE/migration-touches.py" \
    | sed -n 's/^migration-touches //p' || true)"
  if [ "$(printf '%s\n' "$answer" | tail -n 1)" != ok ]; then
    log "WARNING: could not read what the pending migrations change; treating them as touching every table"
    answer="pending (unreadable)
*
ok"
  fi
  PENDING_TOUCHES="$(printf '%s\n' "$answer" | sed '$d')"
}

# owner_schema_touched <container>: what the owner maps that the pending
# migrations touch, or that the database no longer has.
owner_schema_touched() {
  local owner_map touches present
  owner_map="$(schema_map docker exec -i "$1" || true)"
  if [ -z "$owner_map" ]; then
    # Unreadable is not proof of agreement.
    echo "(its schema map could not be read)"
    return
  fi
  if printf '%s\n' "$PENDING_TOUCHES" | grep -Fqx '*'; then
    echo "(a pending migration whose changes could not be read)"
    return
  fi
  touches="$(printf '%s\n' "$PENDING_TOUCHES" | sed '/^present /d; /^pending /d')"
  present="$(printf '%s\n' "$PENDING_TOUCHES" | sed -n 's/^present //p')"
  printf '%s\n' "$owner_map" | awk -v touched="$touches" -v present="$present" '
    BEGIN {
      n = split(touched, items, "\n"); for (i = 1; i <= n; i++) hit[items[i]] = 1
      m = split(present, have, "\n"); for (i = 1; i <= m; i++) there[have[i]] = 1
    }
    ($0 in hit) || (substr($0, 1, index($0 ".", ".") - 1) in hit) { print; next }
    m > 0 && !($0 in there) { print $0 " (not in the database)" }
  '
}

hold_owners_behind_schema() {
  local container touched pending
  read_pending_touches
  pending="$(printf '%s\n' "$PENDING_TOUCHES" | sed -n 's/^pending //p')"
  if [ -n "$pending" ]; then
    log "pending migrations: $(printf '%s\n' "$pending" | tr '\n' ' ')"
  else
    log "no migrations pending"
  fi
  container="$(dc ps -q device-connection 2>/dev/null | head -n 1 || true)"
  if [ -n "$container" ] \
    && [ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" = true ]; then
    touched="$(owner_schema_touched "$container")"
    if [ -n "$touched" ]; then
      log "the device connection owner maps schema the migrations change or the database lacks: $(printf '%s\n' "$touched" | tr '\n' ' ')"
      log "draining the device connection owner across the migrations; it is recreated on this release after them"
      # A call still running after the drain's wait is interrupted rather than
      # stopping the deploy: left alone, the owner would fail every call that
      # touches what the migrations drop.
      DEVICE_CONNECTION_INTERRUPT=late bash "$HERE/release-device-connection.sh" "$SHA" "$COMPOSE" drain \
        || fail "could not drain the device connection owner; nothing was migrated"
      SCHEMA_HELD_OWNERS="device"
    else
      log "the migrations change nothing the device connection owner maps, and the database has all of it; it is left running"
    fi
  fi
  if [ "$PREVIEW_CONNECTION_MODE" = owner ] && preview_connection_running; then
    touched="$(owner_schema_touched "$(preview_connection_container)")"
    if [ -n "$touched" ]; then
      log "the preview connection owner maps schema the migrations change or the database lacks: $(printf '%s\n' "$touched" | tr '\n' ' ')"
      log "the preview connection owner is recreated on this release after the migrations; its tunnels redial"
      SCHEMA_HELD_OWNERS="${SCHEMA_HELD_OWNERS:+$SCHEMA_HELD_OWNERS }preview"
    else
      log "the migrations change nothing the preview connection owner maps, and the database has all of it; it is left running"
    fi
  fi
}

release_owners_held_for_schema() {
  local held="$SCHEMA_HELD_OWNERS"
  # Cleared first: an owner being recreated is no longer one to resume.
  SCHEMA_HELD_OWNERS=""
  case " $held " in
    *" device "*)
      bash "$HERE/release-device-connection.sh" "$SHA" "$COMPOSE" recreate \
        || fail "device connection owner did not come back on this release after the migrations"
      log "device connection owner recreated on this release"
      ;;
  esac
  case " $held " in
    *" preview "*)
      PREVIEW_CONNECTION_INTERRUPT=1 bash "$HERE/release-preview-connection.sh" "$SHA" "$COMPOSE" \
        || fail "preview connection owner did not come back on this release after the migrations"
      log "preview connection owner recreated on this release"
      ;;
  esac
}

# A deploy that stops between the drain and the recreate (a failed migration)
# lets the old device owner take calls again: it is no worse off than before.
resume_owners_held_for_schema() {
  case " $SCHEMA_HELD_OWNERS " in
    *" device "*)
      log "resuming the device connection owner drained for the migrations"
      bash "$HERE/release-device-connection.sh" "$SHA" "$COMPOSE" resume \
        || log "WARNING: could not resume the device connection owner; dispatch 'Release device connection owner'"
      ;;
  esac
  SCHEMA_HELD_OWNERS=""
}

# The preview owner carries the machine preview tunnels and the preview content
# hosts. It is released on its own (deploy/release-preview-connection.sh) exactly
# like device-connection, so an ordinary app release leaves it alone. It is
# started here, before any route switches and before any backend is rendered in
# owner mode, so a helper never finds a backend that dropped the tunnel route:
# the owner is up and healthy first, then api-front points at it, then the
# backends are replaced into owner mode.
ensure_preview_connection_owner() {
  if [ "$PREVIEW_CONNECTION_MODE" != owner ]; then
    # A legacy flip does not retire a running owner here: the routes still point
    # at it, and the backends have not rolled back yet. Only once they have —
    # in retire_preview_connection_owner, after the rollout — is it safe to stop
    # it. Say which of the two situations this is, because "previews stay on the
    # business backend" would be a lie while an owner is still serving them.
    if preview_connection_running; then
      log "previews stay on the running owner until the business backends are legacy"
    else
      log "previews stay on the business backend (PREVIEW_CONNECTION_MODE=legacy)"
    fi
    return 0
  fi
  local container started=false waited=0
  container="$(preview_connection_container)"
  if [ -n "$container" ] && [ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" = true ]; then
    log "leaving preview connection owner $container running across this app release"
  else
    log "starting the independently released preview connection owner"
    dc up -d --no-deps preview-connection \
      || fail "preview connection owner did not start; no route or backend was changed"
    started=true
  fi
  while [ "$waited" -lt "$OWNER_HEALTH_SECONDS" ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${PREVIEW_CONNECTION_PORT}/healthz" >/dev/null 2>&1; then
      [ "$started" = false ] || log "preview connection owner is healthy"
      check_preview_connection_owner_sees_its_files
      return
    fi
    sleep 2
    waited=$((waited + 2))
  done
  fail "preview connection owner is not healthy; no route or backend was changed"
}

# A healthy owner is not enough: WORKSPACE_ROOT is a HOST path in the box env, so
# a subscription box mounts the workspace tree at that same absolute path inside
# the backend too —
# see deploy/compose/docker-compose.subscription.yml. Miss that mount on
# preview-connection and the owner still reports /healthz while every static
# preview and room-file read fails with a path that does not exist. Check the
# owner's OWN resolved paths, with the same Settings the app reads, so a box-local
# env that points them somewhere unmounted fails here and not on the first
# preview. This runs after /healthz and BEFORE any route or backend change, so a
# failure leaves the running previews untouched.
check_preview_connection_owner_sees_its_files() {
  if ! dc exec -T preview-connection python -c '
import os
from app.core.config import Settings

settings = Settings()
missing = []
for name, path in (
    ("workspace_root", settings.workspace_root),
    ("storage_local_path", settings.storage_local_path),
):
    if not os.path.isdir(path):
        missing.append(f"{name}={path}")
if missing:
    raise SystemExit("unmounted: " + ", ".join(missing))
'; then
    # The common cause is an owner that predates this check (started by an older
    # compose without the host-path mirror): its mounts cannot be changed in
    # place, so release it and deploy again. Say so, because "cannot see the
    # files" on its own reads like a box-local env mistake.
    fail "preview connection owner cannot see the files it serves (see the error above); no route or backend was changed — if the owner predates this release, dispatch 'Release preview connection owner' and deploy again"
  fi
}

ensure_forgejo() {
  local container waited=0
  container="$(dc ps -q forgejo 2>/dev/null | head -n 1 || true)"
  if [ -z "$container" ] || [ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" != true ]; then
    dc up -d --no-deps forgejo || fail "repository service did not start"
  fi
  while [ "$waited" -lt 90 ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${FORGEJO_PORT:-3300}/api/healthz" >/dev/null; then
      log "repository service is healthy; its data volume survives app releases"
      container="$(dc ps -q forgejo)"
      python3 "$HERE/bootstrap-forgejo.py" \
        --container "$container" \
        --backend-env "${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}" \
        --api-url "http://127.0.0.1:${FORGEJO_PORT:-3300}/api/v1" \
        || fail "repository administrator setup failed; app release aborted"
      return
    fi
    sleep 2
    waited=$((waited + 2))
  done
  fail "repository service is not healthy; app release aborted"
}

ensure_forge_events() {
  [ "$FORGE_EVENTS_LOCAL" = true ] || return 0
  local waited=0 public_config
  public_config="$(dc run --rm --no-deps backend python -m scripts.forge_event_public_key)" \
    || fail "could not export the GitHub App public key"
  printf '%s' "$public_config" | python3 "$HERE/bootstrap-forgejo.py" \
    --configure-github-events \
    --backend-env "${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}" \
    --relay-env "$FORGE_EVENTS_ENV_FILE" \
    || fail "could not configure GitHub event subscriptions"
  pin_relay_by_code
  dc up -d --no-deps forge-events || fail "forge event relay did not start"
  while [ "$waited" -lt 60 ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${FORGE_EVENTS_PORT:-8093}/healthz" >/dev/null; then
      log "forge event relay is healthy"
      return
    fi
    sleep 2
    waited=$((waited + 2))
  done
  fail "forge event relay is not healthy; app release aborted"
}

migrate_project_repositories() {
  local pending=0 legacy
  # The backend's persistent HOME keeps immutable source backups and receipts.
  # Check before stopping writers: later releases retain their normal rollout.
  dc run --rm --no-deps backend python -m scripts.migrate_forge \
    --backup-root /data/apphome/forge-migration --check || pending=$?
  case "$pending" in
    0) log "all project repositories have migration receipts"; return 0 ;;
    2) ;;
    *) fail "repository migration preflight failed; running backend was not touched" ;;
  esac
  log "pausing repository writers for the first forge migration"
  mkdir -p "$(dirname "$FORGE_CUTOVER_PENDING")"
  touch "$FORGE_CUTOVER_PENDING"
  # shellcheck disable=SC2046 # one word per slot
  dc stop $(backend_services) || fail "could not stop repository writers"
  # Native sessions can share the legacy worktrees without a Docker mount.
  # Retain the restart receipt across failures, just like the cutover guard.
  if command -v systemctl >/dev/null && systemctl is-active --quiet cheese.service; then
    [ "$(systemctl show cheese.service -p KillMode --value)" = control-group ] \
      || fail "host executor preserves child processes on stop; quiesce its sessions before retrying migration"
    touch "$FORGE_EXECUTOR_RESTART"
    sudo -n systemctl stop cheese.service || fail "could not stop the host executor"
  fi
  # This service is absent from the new compose file but may still be running
  # from the previous release; stop it before freezing its receive-pack store.
  while IFS= read -r legacy; do
    [ -z "$legacy" ] || docker stop "$legacy" \
      || fail "could not stop the previous Git receiver"
  done < <(docker ps -q \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=git")
  sudo -n python3 "$HERE/check-forge-workspace-writers.py" \
    "${WORKSPACES_HOST_PATH:-/home/nictheboy/cheese-workspaces}" \
    || fail "legacy workspace users remain; migration has not started"
  dc run --rm --no-deps backend python -m scripts.migrate_forge \
    --backup-root /data/apphome/forge-migration --apply --writers-stopped \
    || fail "repository migration failed; writers remain stopped; retry this release to resume from receipts"
  log "project repositories migrated; backups and migration.log are in the persistent app home under forge-migration"
}

ensure_journal_retention() {
  local target=/etc/systemd/journald.conf.d/cheese.conf
  sudo -n cmp -s "$HERE/journald-cheese.conf" "$target" 2>/dev/null && return 0
  # A box whose deploy user cannot write the journal's config still gets its
  # release: retention decides how far back logs reach, not whether the app
  # runs. Said loudly, since the journal then keeps its default of hours.
  if ! sudo -n install -D -m 0644 "$HERE/journald-cheese.conf" "$target" \
    || ! sudo -n systemctl restart systemd-journald; then
    log "WARNING: journal retention not applied; this box keeps journald's default"
    return 0
  fi
  log "journal retention applied from journald-cheese.conf"
}

# What api-front's configuration says, as one digest: every file it reads that
# a deploy writes, without the comments, indentation and blank lines that change
# nothing it does. A reload retires api-front's workers, and with them, once
# their shutdown deadline passes, every device link, terminal, model tunnel and
# preview tunnel they carry; so the deploy reloads it only when this differs
# from the digest it last loaded, kept in .api-front-loaded beside those files.
# That is what api-front runs, which the files on disk are not when a deploy
# stopped between writing them and the reload.
api_front_digest() {
  local file
  for file in "$1" "$ACTIVE_BACKEND_DIR/preview-routing.conf" "$ACTIVE_BACKEND_DIR"/sites*.conf \
    ${ACTIVE_FRONTEND_DIR:+"$ACTIVE_FRONTEND_DIR/sites-frontend.conf"}; do
    [ -f "$file" ] || continue
    printf '== %s\n' "${file##*/}"
    sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^#/d' -e '/^$/d' "$file"
  done | sha256sum | cut -d ' ' -f 1
}

ensure_application_router() {
  [ -n "$ACTIVE_BACKEND_DIR" ] || return 0
  local config backup frontend_backup router_changed=false loaded rendered
  local owner_port routing_backup sites_backup site_domain
  config="${API_FRONT_CONF:-$(dirname "$ACTIVE_BACKEND_DIR")/nginx.conf}"
  [ -f "$config" ] || fail "api-front config not found: $config"
  # A box that has never switched its frontend serves the compose frontend on
  # its published port; rollout_app moves it between the slots from there.
  if [ ! -f "$ACTIVE_BACKEND_DIR/frontend.conf" ]; then
    printf 'upstream frontend_active { server 127.0.0.1:%s; }\n' "$FRONTEND_PORT" > "$ACTIVE_BACKEND_DIR/frontend.conf"
  fi
  # app-router.conf includes backend-pool.conf. A box that has never written it
  # gets it here, for the backend serving now, before that config is tested:
  # each release's switch rewrites it with backend.conf from then on.
  if [ ! -f "$ACTIVE_BACKEND_DIR/backend-pool.conf" ] && [ -f "$ACTIVE_BACKEND_DIR/backend.conf" ]; then
    write_pool_upstream "$ACTIVE_BACKEND_DIR/backend-pool.conf" "$(serving_backend_port)"
  fi
  if ! cmp -s "$HERE/llm-tunnel/app-router.conf" "$ACTIVE_BACKEND_DIR/app-router.conf"; then
    [ ! -f "$ACTIVE_BACKEND_DIR/app-router.conf" ] || router_changed=true
    cp "$HERE/llm-tunnel/app-router.conf" "$ACTIVE_BACKEND_DIR/app-router.conf"
  fi
  ACTIVE_BACKEND_DIR="$ACTIVE_BACKEND_DIR" docker compose \
    -p cheese-dataplane -f "$HERE/llm-tunnel/app-router-compose.yml" up -d app-router \
    || fail "cannot start application router; the existing ingress was not changed"
  docker exec cheese-app-router nginx -t || fail "application router config rejected"
  if [ "$router_changed" = true ]; then
    docker exec cheese-app-router nginx -s reload || fail "application router could not reload"
  fi
  if [ -n "$ACTIVE_FRONTEND_DIR" ]; then
    curl -fsS -m 3 http://127.0.0.1:18086/ >/dev/null \
      || fail "application router cannot reach the serving frontend"
  fi

  # What api-front runs. The record is trusted only while the files on disk
  # still match it: anything else that wrote them since (tls-renew.sh, a
  # release of an older commit, an operator) reloaded api-front without
  # updating it, or did not reload it at all, so what runs is unknown and the
  # deploy reloads. A box that never recorded it runs what is on disk, which
  # is what the deploy compared against before the record existed.
  loaded="$(cat "$ACTIVE_BACKEND_DIR/.api-front-loaded" 2>/dev/null || true)"
  if [ -z "$loaded" ]; then
    loaded="$(api_front_digest "$config")"
  elif [ "$loaded" != "$(api_front_digest "$config")" ]; then
    log "api-front's config on disk is not the one it last loaded here; reloading it"
    loaded=""
  fi
  # Everything api-front reads that this deploy may rewrite is backed up first,
  # so a rejected config restores all of it together.
  backup="${config}.pre-device-connection"
  cp "$config" "$backup"
  routing_backup="$ACTIVE_BACKEND_DIR/preview-routing.conf.pre-app-router"
  rm -f "$routing_backup"
  [ ! -f "$ACTIVE_BACKEND_DIR/preview-routing.conf" ] \
    || cp "$ACTIVE_BACKEND_DIR/preview-routing.conf" "$routing_backup"
  # The preview tunnel target for this deploy. owner points the routes at the
  # owner's loopback port. legacy normally writes the backend — but only once no
  # owner is serving: while an owner is still RUNNING its helpers are connected
  # to it, and re-pointing the routes first would send their redials to a backend
  # that has not rolled back to legacy yet (no tunnel route there, a 404, which
  # ends the helper's retry loop for good). So a legacy flip that still has a
  # running owner keeps the owner target through this pass, the backends roll
  # back, and retire_preview_connection_owner re-points them afterwards.
  if [ "$PREVIEW_CONNECTION_MODE" = owner ] \
    || { [ "${PREVIEW_ROUTE_FORCE_BACKEND:-0}" != 1 ] && preview_connection_running; }; then
    owner_port="$PREVIEW_CONNECTION_PORT"
  else
    owner_port=""
  fi
  # configure-preview.sh takes the mode, not the port, for what nginx.conf's
  # tunnel location points at; the content-host scripts below take the port.
  if [ -n "$owner_port" ]; then
    bash "$HERE/llm-tunnel/configure-preview.sh" owner "$ACTIVE_BACKEND_DIR" "$owner_port"
  else
    bash "$HERE/llm-tunnel/configure-preview.sh" legacy "$ACTIVE_BACKEND_DIR"
  fi || fail "could not write the preview routing file"
  cmp -s "$HERE/llm-tunnel/nginx.conf" "$config" || cp "$HERE/llm-tunnel/nginx.conf" "$config"
  # Preview content hosts are matched by the content domain's wildcard server,
  # and no server_name can beat it (a middle wildcard is invalid; a regex loses).
  # So the split lives inside active/sites.conf, and this re-renders that file
  # from the repo script for the effective mode. The domain is read from the
  # file already serving it, never guessed; a box with no content domain, or one
  # an operator disabled, is left alone.
  sites_backup=""
  if [ -s "$ACTIVE_BACKEND_DIR/sites.conf" ]; then
    site_domain="$(sed -n 's/^[[:space:]]*server_name[[:space:]]\+\([^[:space:];*][^[:space:];]*\).*/\1/p' \
      "$ACTIVE_BACKEND_DIR/sites.conf" | head -n 1)"
    if [ -n "$site_domain" ]; then
      sites_backup="$ACTIVE_BACKEND_DIR/sites.conf.pre-app-router"
      cp "$ACTIVE_BACKEND_DIR/sites.conf" "$sites_backup"
      bash "$HERE/llm-tunnel/configure-sites.sh" "$site_domain" "$ACTIVE_BACKEND_DIR" "$owner_port" \
        || fail "could not write the content-host routing"
    fi
  fi
  frontend_backup=""
  if [ -n "$ACTIVE_FRONTEND_DIR" ]; then
    frontend_backup="$ACTIVE_FRONTEND_DIR/sites-frontend.conf.pre-app-router"
    cp "$ACTIVE_FRONTEND_DIR/sites-frontend.conf" "$frontend_backup"
    bash "$HERE/llm-tunnel/configure-frontend.sh" "$ACTIVE_FRONTEND_DIR" 18086 "$FRONTEND_PROXY_PORT" "$owner_port"
  fi
  rendered="$(api_front_digest "$config")"
  if [ "$rendered" = "$loaded" ]; then
    printf '%s\n' "$rendered" > "$ACTIVE_BACKEND_DIR/.api-front-loaded"
    rm -f "$backup" ${frontend_backup:+"$frontend_backup"} ${sites_backup:+"$sites_backup"} "$routing_backup"
    return 0
  fi
  if ! docker exec "$API_FRONT_CONTAINER" nginx -t; then
    cp "$backup" "$config"
    [ -z "$frontend_backup" ] || cp "$frontend_backup" "$ACTIVE_FRONTEND_DIR/sites-frontend.conf"
    [ -z "$sites_backup" ] || cp "$sites_backup" "$ACTIVE_BACKEND_DIR/sites.conf"
    [ -s "$routing_backup" ] && cp "$routing_backup" "$ACTIVE_BACKEND_DIR/preview-routing.conf"
    rm -f "$backup"
    fail "api-front rejected the preview/owner route; restored its config"
  fi
  if ! docker exec "$API_FRONT_CONTAINER" nginx -s reload; then
    cp "$backup" "$config"
    [ -z "$frontend_backup" ] || cp "$frontend_backup" "$ACTIVE_FRONTEND_DIR/sites-frontend.conf"
    [ -z "$sites_backup" ] || cp "$sites_backup" "$ACTIVE_BACKEND_DIR/sites.conf"
    [ -s "$routing_backup" ] && cp "$routing_backup" "$ACTIVE_BACKEND_DIR/preview-routing.conf"
    docker exec "$API_FRONT_CONTAINER" nginx -s reload >/dev/null 2>&1 || true
    rm -f "$backup"
    fail "api-front could not reload the preview/owner route; restored its config"
  fi
  printf '%s\n' "$rendered" > "$ACTIVE_BACKEND_DIR/.api-front-loaded"
  rm -f "$backup" ${frontend_backup:+"$frontend_backup"} ${sites_backup:+"$sites_backup"} "$routing_backup"
  log "api-front routes application traffic through app-router; later business switches leave persistent connections untouched"
}

# The legacy half of the kill switch. While the routes still pointed at a running
# owner, helpers stayed connected to it, so flipping the file to legacy without
# this would leave those helpers on the owner and route NEW tunnel requests to a
# backend whose hub is empty — previews broken, and only until the helper redials.
# Called AFTER the backends have rolled back to legacy, so the order is: backends
# serve legacy -> routes re-point at the backend -> owner stops -> helpers redial
# and land on the backend. In owner mode it does nothing.
retire_preview_connection_owner() {
  [ "$PREVIEW_CONNECTION_MODE" = legacy ] || return 0
  preview_connection_running || return 0
  log "PREVIEW_CONNECTION_MODE=legacy: returning previews to the business backend"
  PREVIEW_ROUTE_FORCE_BACKEND=1 ensure_application_router
  if dc stop preview-connection >/dev/null; then
    # Remove it too: a stopped owner still holds the service name and its image,
    # and the next owner-mode deploy recreates it from scratch either way.
    dc rm -f preview-connection >/dev/null 2>&1 || true
    log "preview connection owner stopped; its helpers redial the business backend"
  else
    fail "previews are routed to the business backend but the preview connection owner would not stop"
  fi
}
log() { echo "[deploy-docker $(date '+%H:%M:%S')] $*"; }
fail() { echo "[deploy-docker $(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

# ---- Preview owner cutover (repo-level kill switch) ----
# owner moves the machine preview tunnels and the preview content hosts off the
# business backend onto the independently released preview-connection service;
# legacy keeps them in the backend a release replaces. The default lives in
# deploy/preview-connection.env (the kill switch: edit it to legacy and the next
# deploy routes everything back); an explicit PREVIEW_CONNECTION_MODE from the
# environment or ~/ops/deploy.env overrides it. Resolved here, before any
# compose call, so a box that cannot reach an owner never renders one.
if [ -z "${PREVIEW_CONNECTION_MODE:-}" ]; then
  # shellcheck source=/dev/null
  . "$HERE/preview-connection.env"
fi
if [ "${PREVIEW_CONNECTION_MODE:-legacy}" != owner ] && [ "${PREVIEW_CONNECTION_MODE:-legacy}" != legacy ]; then
  fail "PREVIEW_CONNECTION_MODE must be owner or legacy, not '${PREVIEW_CONNECTION_MODE}'"
fi
if [ "$PREVIEW_CONNECTION_MODE" = owner ] && [ -z "${ACTIVE_BACKEND_DIR:-}" ]; then
  log "WARNING: PREVIEW_CONNECTION_MODE=owner needs the standing api-front (ACTIVE_BACKEND_DIR); keeping previews legacy"
  PREVIEW_CONNECTION_MODE=legacy
fi
PREVIEW_CONNECTION_PORT="${PREVIEW_CONNECTION_PORT:-18087}"
export PREVIEW_CONNECTION_MODE PREVIEW_CONNECTION_PORT

# With a rolling frontend, app-router listens on the box's frontend ports itself
# (take_frontend_ports), on FRONTEND_PORT and on what FRONTEND_PORT_DIRECT names
# for the compose frontend. Compose takes a bare port, ip:port or [ipv6]:port
# there; nginx takes the last two, so a bare port becomes 0.0.0.0:port. Checked
# here, before anything on the box is touched: a value nginx cannot listen on
# would otherwise fail in the middle of a switch.
FRONTEND_DIRECT_LISTEN=""
if [ -n "${ACTIVE_BACKEND_DIR:-}" ] && [ -n "${ACTIVE_FRONTEND_DIR:-}" ]; then
  [[ "${FRONTEND_PORT:-8080}" =~ ^[0-9]+$ ]] \
    || fail "FRONTEND_PORT must be a port number, not '${FRONTEND_PORT}'; nothing was changed"
  FRONTEND_DIRECT_LISTEN="${FRONTEND_PORT_DIRECT:-0.0.0.0:80}"
  if [[ "$FRONTEND_DIRECT_LISTEN" =~ ^[0-9]+$ ]]; then
    FRONTEND_DIRECT_LISTEN="0.0.0.0:$FRONTEND_DIRECT_LISTEN"
  fi
  [[ "$FRONTEND_DIRECT_LISTEN" =~ ^([0-9]{1,3}(\.[0-9]{1,3}){3}|\[[0-9A-Fa-f:]+\]):[0-9]+$ ]] \
    || fail "FRONTEND_PORT_DIRECT='${FRONTEND_PORT_DIRECT}' is not a port, ip:port or [ipv6]:port app-router can listen on; nothing was changed"
fi

# A room's transcript view is mounted on the machine its tools run on, not
# here: this box is the session host, and the executor for an ordinary room is
# a pool host the platform provisions. That machine gets FUSE the way it gets
# everything else — cheesehost places the library the executor loads by path
# (`cli/internal/devenv`, the `fuse-runtime` pin in `agent/toolchain.py`) and
# the executor writes the one line `/etc/fuse.conf` needs, as root, only where
# the platform owns the machine (`bootstrap.fuse_is_mountable`). Nothing is
# installed here, and nothing is installed on a machine a person enrolled.
#
# This block used to `apt-get install fuse libfuse2` on the central session
# host. It never took effect: the view is not mounted on this machine. It is
# left as a comment rather than deleted silently, because the two lines it
# wrote — the packages and `user_allow_other` — moved, and the next person to
# look for them will look here.

[ -f "$COMPOSE" ] || fail "compose file not found: $COMPOSE"

event_environment="$(python3 "$HERE/bootstrap-forgejo.py" --configure-events \
  --backend-env "${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}" \
  ${FORGE_EVENTS_ENV_FILE:+--relay-env "$FORGE_EVENTS_ENV_FILE"})" \
  || fail "event relay configuration failed; running services were not touched"
eval "$event_environment"
if [ "$FORGE_EVENTS_LOCAL" = true ]; then
  COMPOSE_OVERLAYS="$COMPOSE_OVERLAYS $HERE/forge-events/compose.yml"
  export FORGE_EVENTS_IMAGE="${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$SHA}"
fi

# Every deployment needs a repository service for projects without GitHub.
# Preparation emits only shell-quoted public addresses, never credentials.
forge_environment="$(python3 "$HERE/bootstrap-forgejo.py" --prepare \
  --backend-env "${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}")" \
  || fail "repository configuration failed; running services were not touched"
eval "$forge_environment"

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

# ---- Two slots per application service (boxes with an app-router) ----
# A release moves traffic from the running backend and frontend to new ones
# exactly once. That needs the new containers to be the ones that stay, so each
# has two compose services — `backend` and `backend-b`, and with
# ACTIVE_FRONTEND_DIR `frontend` and `frontend-b` — on loopback ports, and every
# release starts the idle one, switches app-router to it and stops the other.
# The containers therefore alternate between cheese-backend-1 and
# cheese-backend-b-1 (and the frontend likewise); deploy/app-container.sh names
# the running one. The box's own :8080 and :80, which the frontend container
# used to publish, are app-router's (frontend.conf, see take_frontend_ports), so
# they answer whichever frontend slot serves.
#
# The `-b` services are written here, from compose's own merged model after
# every overlay, so they cannot drift from the services they copy: `extends`
# reads one file only and would miss what an overlay adds (the subscription
# overlay's workspace mirror, for one). Only the published port differs, plus the
# network name the other services dial (`backend` for device-connection and the
# office editor, `frontend` for the backend's docs index), which both slots of a
# service answer to. `frontend` itself is moved to a loopback port with
# `!override`, which compose accepts in this JSON-shaped file.
#
# The copy is interpolated — compose cannot print this model uninterpolated —
# so it carries the values this run exports and is written only once all of
# them are (the office editor and collab secrets below), and again whenever
# they change (the rollback). Compose prints the env file merged into
# `environment`, so the model is read with an empty one and the copy names the
# real file again: the backend's secrets stay in that file, and compose's own
# `environment` entries still win over it, as they do for the original. The two
# secrets compose interpolates itself are in the copy, hence a private file that
# the exit trap removes.
ACTIVE_BACKEND_DIR="${ACTIVE_BACKEND_DIR:-}"
BACKEND_PORT="${BACKEND_PORT:-8081}"
BACKEND_PORT_NEXT="${BACKEND_PORT_NEXT:-18082}"
# Each slot's api pool, published beside its backend (docker-compose.api-pool.yml).
BACKEND_POOL_PORT="${BACKEND_POOL_PORT:-18091}"
BACKEND_POOL_PORT_NEXT="${BACKEND_POOL_PORT_NEXT:-18092}"
export BACKEND_POOL_PORT
FRONTEND_PORT="${FRONTEND_PORT:-8080}"
FRONTEND_PORT_NEXT="${FRONTEND_PORT_NEXT:-18084}"
# The first frontend slot's loopback port: the box's :8080 and :80 are app-router's.
FRONTEND_SLOT_PORT="${FRONTEND_SLOT_PORT:-18088}"
SLOTS_OVERLAY=""
write_slots_overlay() {
  local no_env
  [ -n "$SLOTS_OVERLAY" ] || SLOTS_OVERLAY="$(mktemp)"
  chmod 600 "$SLOTS_OVERLAY"
  no_env="$(mktemp)"
  BACKEND_ENV_FILE="$no_env" docker compose -f "$COMPOSE" ${_overlay_args[@]+"${_overlay_args[@]}"} \
    -p "$PROJECT" config --format json \
    | ENV_FILE="${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}" \
      BACKEND_PORT_NEXT="$BACKEND_PORT_NEXT" FRONTEND_SLOT_PORT="$FRONTEND_SLOT_PORT" \
      POOL_PORT_NEXT="$( [ "$API_POOL_WORKERS" -gt 0 ] && printf %s "$BACKEND_POOL_PORT_NEXT" )" \
      FRONTEND_PORT_NEXT="$FRONTEND_PORT_NEXT" ROLL_FRONTEND="${ACTIVE_FRONTEND_DIR:+1}" python3 -c '
import json, os, sys
services = json.load(sys.stdin)["services"]
backend = dict(services["backend"])
backend["env_file"] = [os.environ.get("ENV_FILE")]
backend["ports"] = ["127.0.0.1:" + os.environ.get("BACKEND_PORT_NEXT") + ":8081"]
if os.environ.get("POOL_PORT_NEXT"):
    backend["ports"].append("127.0.0.1:" + os.environ["POOL_PORT_NEXT"] + ":8085")
backend["networks"] = {"default": {"aliases": ["backend"]}}
slots = {"backend-b": backend}
if os.environ.get("ROLL_FRONTEND"):
    frontend = dict(services["frontend"])
    frontend["ports"] = ["127.0.0.1:" + os.environ.get("FRONTEND_PORT_NEXT") + ":80"]
    frontend["networks"] = {"default": {"aliases": ["frontend"]}}
    # Started only with --no-deps: the original waits for the `backend` slot,
    # which is the stopped one half of the time.
    frontend.pop("depends_on", None)
    slots["frontend-b"] = frontend
    slots["frontend"] = {"ports": "@FRONTEND_PORTS@"}
text = json.dumps({"services": slots}, indent=1)
first = json.dumps(["127.0.0.1:" + os.environ.get("FRONTEND_SLOT_PORT") + ":80"])
print(text.replace("\"@FRONTEND_PORTS@\"", "!override " + first))
' > "$SLOTS_OVERLAY" \
    || { rm -f "$no_env"; fail "could not write the second application slots; running services were not touched"; }
  rm -f "$no_env"
}
_slot_args=()

# The office editor and the backend share one signing secret: it is what makes
# a save callback the editor's. Provisioned once and kept beside deploy.env, so a
# redeploy never rotates it out from under an open document; a box that pins its
# own in deploy.env keeps that one.
if [ -z "${OFFICE_EDITOR_JWT_SECRET:-}" ]; then
  _office_secret="$HOME/ops/office-editor.secret"
  if [ ! -s "$_office_secret" ]; then
    mkdir -p "$HOME/ops"
    ( umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$_office_secret" )
    log "provisioned the office editor's signing secret in $_office_secret"
  fi
  OFFICE_EDITOR_JWT_SECRET="$(cat "$_office_secret")"
  export OFFICE_EDITOR_JWT_SECRET
fi

# The same once-provisioned shape for the collaboration service, which the
# backend signs document tickets for and the two services authenticate each
# other with. Rotating it would close every open document.
if [ -z "${COLLAB_SECRET:-}" ]; then
  _collab_secret="$HOME/ops/collab.secret"
  if [ ! -s "$_collab_secret" ]; then
    mkdir -p "$HOME/ops"
    ( umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$_collab_secret" )
    log "provisioned the collaboration service's secret in $_collab_secret"
  fi
  COLLAB_SECRET="$(cat "$_collab_secret")"
  export COLLAB_SECRET
fi

# Only now: the second backend slot carries the values exported above.
if [ -n "$ACTIVE_BACKEND_DIR" ]; then
  write_slots_overlay
  trap 'rm -f "$SLOTS_OVERLAY"' EXIT
  _slot_args=(-f "$SLOTS_OVERLAY")
fi

# The pull budget, its backoff and retry_pull, shared with ghcr-login.sh.
. "$HERE/registry-retry.sh"

# What retry_pull does before retrying a pull that failed on a full disk.
# Idempotent and safe on a live box: prune only removes what nothing
# references. Deliberately NOT touching containerd's ingest area, stopping
# dockerd, or killing containers — a failed retry is recoverable, a wedged
# box is not.
reclaim_before_pull_retry() {
  if [ "$APP_IMAGE_SOURCE" = registry ]; then
    reclaim_docker_disk "before pull retry" true
  else
    reclaim_docker_disk "before pull retry" false
  fi
}

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
# trick build.yml's cheese-buildkit-image-retainer and this script's
# private-executor retainer below already use for the same reason. Opportunistic
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
# The backend traffic has left, until retire_old_backend stops it (rollout_app).
RETIRING_BACKEND="" RETIRING_BACKEND_SERVICE="" RETIRING_BACKEND_PORT="" RETIRING_DEADLINE=0

on_exit() {
  local rc=$?
  [ "$#" -eq 0 ] || rc="$1"
  trap - EXIT INT TERM
  # A release never leaves two backends behind it, nor waits on the way out.
  # (Set only by rollout_app, so retire_old_backend is defined by then.)
  [ -z "$RETIRING_BACKEND_SERVICE" ] \
    || retire_old_backend now "the deploy is exiting with status $rc"
  resume_owners_held_for_schema
  [ -z "$SLOTS_OVERLAY" ] || rm -f "$SLOTS_OVERLAY"
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

# Capture the exact currently-running images so rollback also works when an
# environment overrides BACKEND_IMAGE / FRONTEND_IMAGE instead of using IMAGE_TAG.
service_container() {
  dc ps -q "$1" 2>/dev/null | head -n 1 || true
}
# The services the backend runs as: both slots on a box with an app-router
# (see "Two backend slots" above), the one service elsewhere.
backend_services() {
  if [ -n "$ACTIVE_BACKEND_DIR" ]; then
    printf 'backend backend-b\n'
  else
    printf 'backend\n'
  fi
}
# The running backend container, whichever slot it is in.
running_backend() {
  local service container
  for service in $(backend_services); do
    container="$(service_container "$service")"
    if [ -n "$container" ]; then
      printf '%s\n' "$container"
      return 0
    fi
  done
}
# The running frontend container: `frontend-b` serves every other release on a
# box that rolls the frontend too (ACTIVE_FRONTEND_DIR).
running_frontend() {
  local container
  container="$(service_container frontend)"
  if [ -z "$container" ] && [ -n "${ACTIVE_FRONTEND_DIR:-}" ]; then
    container="$(service_container frontend-b)"
  fi
  printf '%s' "$container"
}
service_image() {
  local container="$1"
  [ -n "$container" ] || return 0
  docker inspect --format '{{.Config.Image}}' "$container" 2>/dev/null || true
}

# ---- Unchanged services are not recreated ----
# Compose recreates a container whenever the image reference it was created
# from changes, and every commit's images carry that commit's tag, including
# the ones build.yml only promoted because nothing in them changed. So collab,
# browser-render and office-render were replaced on every release, closing
# every open document's socket and every page a renderer was holding. They are
# named by digest instead (repo@sha256:…), and when the image this release
# pulled has the same content as the one running, by the running one's digest:
# the reference compose sees does not change, and neither does the container.
#
# Content, not digest: a rebuild of unchanged inputs (every image is rebuilt
# on a dispatched build) gets a new digest, because the image records when and
# from which commit it was built, while its layers and runtime config are
# identical. image_content is exactly those, without the build's own labels.
image_content() {
  docker image inspect --format '{{json .RootFS.Layers}} {{json .Config.Entrypoint}} {{json .Config.Cmd}} {{json .Config.Env}} {{json .Config.WorkingDir}} {{json .Config.User}} {{json .Config.ExposedPorts}} {{json .Config.Volumes}} {{json .Config.Healthcheck}} {{json .Config.StopSignal}} {{range $k, $v := .Config.Labels}}{{$k}}={{$v}};{{end}}' \
    "$1" 2>/dev/null | sed 's/org\.opencontainers\.image\.[^=;]*=[^;]*;//g' || true
}

# repo_digest <image> <repository>: the image as <repository>@sha256:…, if the
# registry gave it one.
repo_digest() {
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" 2>/dev/null \
    | grep -F -m 1 "$2@" || true
}

# pin_by_content <variable> <service> <pulled image>: point the service's
# image variable at the running image when the pulled one has the same
# content, and at the pulled one by digest when it does not. An image the
# operator named (the variable already set) or one without a registry digest
# (a local build) is left as it is.
pin_by_content() {
  local variable="$1" service="$2" pulled="$3" repository="${3%:*}" pinned container running content
  [ -z "${!variable:-}" ] || return 0
  pinned="$(repo_digest "$pulled" "$repository")"
  [ -n "$pinned" ] || return 0
  container="$(service_container "$service")"
  running="$([ -z "$container" ] || docker inspect --format '{{.Image}}' "$container" 2>/dev/null || true)"
  content="$(image_content "$pulled")"
  if [ -n "$running" ] && [ -n "$content" ] && [ "$(image_content "$running")" = "$content" ] \
    && [ -n "$(repo_digest "$running" "$repository")" ]; then
    pinned="$(repo_digest "$running" "$repository")"
    log "$service: $pulled has the content already running; keeping $pinned"
  else
    log "$service: $pulled is new content; it runs as $pinned"
  fi
  export "$variable=$pinned"
  PINNED_IMAGES="$PINNED_IMAGES $variable"
}
PINNED_IMAGES=""

# The collab image a rollback returns to: the one that ran before this release;
# with none running, the previous release's by its tag, as compose names it
# when nothing pins it.
rollback_collab_image() {
  if [ -n "$PREV_COLLAB_IMAGE" ]; then
    printf '%s' "$PREV_COLLAB_IMAGE"
  else
    case " $PINNED_IMAGES " in
      *" COLLAB_IMAGE "*) ;;
      *) printf '%s' "${COLLAB_IMAGE:-}" ;;
    esac
  fi
}

# The forge event relay runs the backend image, which changes with nearly
# every backend commit while the relay's own code rarely does, and recreating
# the relay drops every deployment's event socket. So it keeps the image it
# runs, by digest, until the code it runs differs (deploy/code-digest.py in the
# running relay and in this release's image).
pin_relay_by_code() {
  local container repository release running pinned
  repository="${FORGE_EVENTS_IMAGE%@*}"
  repository="${repository%:*}"
  container="$(service_container forge-events)"
  release="$(dc run --rm --no-deps -T backend python - app.forge_events_app < "$HERE/code-digest.py" \
    | sed -n 's/^code-digest //p' || true)"
  running="$([ -z "$container" ] || docker exec -i "$container" python - app.forge_events_app < "$HERE/code-digest.py" 2>/dev/null \
    | sed -n 's/^code-digest //p' || true)"
  if [ -n "$release" ] && [ "$running" = "$release" ]; then
    pinned="$(repo_digest "$(docker inspect --format '{{.Image}}' "$container")" "$repository")"
    FORGE_EVENTS_IMAGE="${pinned:-$(service_image "$container")}"
    log "forge event relay: its code is unchanged (${release:0:12}); keeping $FORGE_EVENTS_IMAGE"
  else
    pinned="$(repo_digest "$FORGE_EVENTS_IMAGE" "$repository")"
    FORGE_EVENTS_IMAGE="${pinned:-$FORGE_EVENTS_IMAGE}"
    log "forge event relay: its code differs from the running relay's; it runs $FORGE_EVENTS_IMAGE"
  fi
  export FORGE_EVENTS_IMAGE
}

NEXT_BACKEND="${PROJECT}-backend-next"
NEXT_FRONTEND="${PROJECT}-frontend-next"
# The one-off successors releases before the two slots started, on the ports
# the second slots now use. One left behind by an interrupted release blocks
# that port. If app-router does not send traffic to it, it is only in the way
# and is removed; if it does, it is what serves, and the release stops here.
clear_interrupted_release() {
  local leftover port file
  for leftover in "$NEXT_BACKEND" "$NEXT_FRONTEND"; do
    docker container inspect "$leftover" >/dev/null 2>&1 || continue
    if [ "$leftover" = "$NEXT_BACKEND" ]; then
      port="$BACKEND_PORT_NEXT" file="$ACTIVE_BACKEND_DIR/backend.conf"
    else
      port="$FRONTEND_PORT_NEXT" file="$ACTIVE_BACKEND_DIR/frontend.conf"
    fi
    if grep -Fq "server 127.0.0.1:$port;" "$file" 2>/dev/null; then
      fail "$leftover, left by an interrupted release, is what app-router serves ($file names :$port); nothing was changed. Point $file back at the compose service on its own port, reload app-router, remove $leftover, and release again"
    fi
    log "removing $leftover, left by an interrupted release; app-router does not send traffic to it"
    docker stop --time 30 "$leftover" >/dev/null 2>&1 || true
    docker rm -f "$leftover" >/dev/null 2>&1 || fail "could not remove $leftover"
  done
}

# Before anything is pulled, migrated or switched.
[ -z "$ACTIVE_BACKEND_DIR" ] || clear_interrupted_release

PREV_BACKEND_CONTAINER="$(running_backend)"
PREV_FRONTEND_CONTAINER="$(running_frontend)"
PREV_BACKEND_IMAGE="$(service_image "$PREV_BACKEND_CONTAINER")"
PREV_FRONTEND_IMAGE="$(service_image "$PREV_FRONTEND_CONTAINER")"
PREV_COLLAB_IMAGE="$(service_image "$(service_container collab)")"
# The release running now is the tag its backend was started from, in whichever
# slot it serves. Reading only the `backend` service left every release that
# started while `backend-b` served with no previous release, so a failed health
# check there had nothing to roll back to.
PREV_SHA=""
case "${PREV_BACKEND_IMAGE##*/}" in
  *@*) ;;
  *:*) PREV_SHA="${PREV_BACKEND_IMAGE##*:}" ;;
esac
if [ -z "$PREV_SHA" ]; then
  # Fall back to reading the image tag actually in use.
  PREV_SHA="$(dc images backend 2>/dev/null | awk 'NR==2{print $3}' || true)"
fi
# The release the running backend reports, so a rollback reports it again.
PREV_RELEASE=""
if [ -n "$PREV_BACKEND_CONTAINER" ]; then
  PREV_RELEASE="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
    "$PREV_BACKEND_CONTAINER" 2>/dev/null | sed -n 's/^APP_RELEASE=//p' | head -n 1 || true)"
fi
log "deploying sha=$SHA (previous=${PREV_SHA:-none}) via $COMPOSE"

# From here on the script may leave pulled layers on disk, so every exit path
# owes the box a reclaim.
RECLAIM_PENDING=1
trap on_exit EXIT
trap 'on_exit 130' INT
trap 'on_exit 143' TERM

log_disk "before pull"

COLLAB_EXPECTED=false
case "$APP_IMAGE_SOURCE" in
  registry)
    log "pulling app images…"
    retry_pull "image pull" dc pull backend frontend
    # The collaboration service, which every living document is edited
    # through. build.yml builds or promotes it for every commit, so a release
    # without it predates it — and its backend still saves documents itself,
    # with no use for the service. Only a registry that says the tag does not
    # exist means that; any other failure is a failed pull like the two above.
    collab_ref="${COLLAB_IMAGE:-ghcr.io/sageseekersociety/cheese/collab:$IMAGE_TAG}"
    if collab_err="$(docker manifest inspect "$collab_ref" 2>&1 >/dev/null)" \
      || [[ ! "${collab_err,,}" =~ manifest\ unknown|no\ such\ manifest|not\ found ]]; then
      retry_pull "collab pull" dc pull collab
      COLLAB_EXPECTED=true
      pin_by_content COLLAB_IMAGE collab "$collab_ref"
    else
      log "$collab_ref does not exist: this release predates the collaboration service"
    fi
    # The browser is an ENHANCEMENT to fetching, not a component of the app, so
    # its pull is deliberately outside the retry-and-fail path above: without it
    # fetching falls back a rung (measured: 19 of 20 real sites becomes 17) and
    # everything else is unaffected, whereas failing the deploy over it would
    # trade the whole platform for one rung.
    if dc pull browser-render >/dev/null 2>&1; then
      pin_by_content BROWSER_RENDER_IMAGE browser-render \
        "ghcr.io/sageseekersociety/cheese/browser-render:$IMAGE_TAG"
    else
      log "WARNING: browser-render image unavailable; fetching will fall back a rung"
    fi
    # Same shape, same reason: without the renderer a Word or PowerPoint
    # deliverable falls back to a download, which the preview panel reports on
    # screen. Nothing else in the platform is affected.
    if dc pull office-render >/dev/null 2>&1; then
      pin_by_content OFFICE_RENDER_IMAGE office-render \
        "ghcr.io/sageseekersociety/cheese/office-render:$IMAGE_TAG"
    else
      log "WARNING: office-render image unavailable; documents will offer download only"
    fi
    dc pull office-editor >/dev/null 2>&1 \
      || log "WARNING: office-editor image unavailable; room files stay read-only"
    # Same shape again: without the executor a private chat fails with an
    # explicit setup error, and nothing else is affected. The session host
    # starts it with `docker run` under a local name, which the image carries as
    # a label; a stopped container keeps it through the image prune, handed over
    # at the end so a rollback still finds the old one.
    # The retainer is created from the local name, not the registry one: the
    # containerd image store keeps each name as an image of its own, and
    # `image prune -a` spares only the names a container was created from.
    private_executor="ghcr.io/sageseekersociety/cheese/private-executor:$IMAGE_TAG"
    private_executor_retainer="${PROJECT}-private-executor-image-retainer-next"
    # A retainer a failed deploy left behind must not be promoted for this one.
    docker rm -f "$private_executor_retainer" >/dev/null 2>&1 || true
    if docker pull "$private_executor" >/dev/null 2>&1 \
      && private_executor_name="$(docker image inspect \
        --format '{{ index .Config.Labels "com.cheese.local-image" }}' \
        "$private_executor" 2>/dev/null)" \
      && [ -n "$private_executor_name" ] \
      && docker tag "$private_executor" "$private_executor_name" \
      && docker create --name "$private_executor_retainer" \
        --label "com.cheese.image-retainer=private-executor" \
        --entrypoint /bin/true "$private_executor_name" >/dev/null 2>&1; then
      log "private-chat executor available as $private_executor_name"
    else
      log "WARNING: private-executor image unavailable; private chats cannot start"
    fi
    ;;
  local)
    [ -n "${BACKEND_IMAGE:-}" ] || \
      fail "BACKEND_IMAGE is required when DEPLOY_APP_IMAGE_SOURCE=local"
    [ -n "${FRONTEND_IMAGE:-}" ] || \
      fail "FRONTEND_IMAGE is required when DEPLOY_APP_IMAGE_SOURCE=local"
    [ -n "${COLLAB_IMAGE:-}" ] || \
      fail "COLLAB_IMAGE is required when DEPLOY_APP_IMAGE_SOURCE=local"
    log "verifying locally built app images…"
    docker image inspect "$BACKEND_IMAGE" >/dev/null 2>&1 || \
      fail "local backend image not found: $BACKEND_IMAGE"
    docker image inspect "$FRONTEND_IMAGE" >/dev/null 2>&1 || \
      fail "local frontend image not found: $FRONTEND_IMAGE"
    docker image inspect "$COLLAB_IMAGE" >/dev/null 2>&1 || \
      fail "local collab image not found: $COLLAB_IMAGE"
    COLLAB_EXPECTED=true
    ;;
  *)
    fail "DEPLOY_APP_IMAGE_SOURCE must be registry or local (got: $APP_IMAGE_SOURCE)"
    ;;
esac

log_disk "after pull"

ensure_forgejo
ensure_forge_events
hold_owners_behind_schema
log "running DB migrations (alembic upgrade head)…"
# Production image ships no pyproject, so call alembic directly from the venv.
# Each migration commits on its own (alembic/env.py), so a failure leaves the
# database at the last one that succeeded; the next deploy resumes from there.
dc run --rm backend sh -c "alembic upgrade head" || fail "migration failed — aborting before swap; the database stays at the last migration that committed (alembic current)"
release_owners_held_for_schema

# Persistent files must be readable by the backend's uid (1000), including
# legacy repository files that the migration archives. Ownership repair uses
# a marker in each path so later releases leave existing ownership alone.
# APPHOME contains the backend's persistent configuration and migration receipts.
#
# Schema and credential checks precede ownership repair. Repository migration
# follows it because the new backend uid must read the old workspace files;
# a failed repository migration leaves writers stopped until a release retries.
# Same story for the claude binaries the backend serves to the machines it
# enrols: a cache the container has to be able to write, and that has to
# outlive the container (see the compose file).
CLAUDE_CACHE_PATH="${CLAUDE_CACHE_HOST_PATH:-/home/nictheboy/cheese-claude-cache}"
mkdir -p "$CLAUDE_CACHE_PATH" || fail "cannot create $CLAUDE_CACHE_PATH"
# And the pi builds, which the backend serves to the same machines.
PI_CACHE_PATH="${PI_CACHE_HOST_PATH:-/home/nictheboy/cheese-pi-cache}"
mkdir -p "$PI_CACHE_PATH" || fail "cannot create $PI_CACHE_PATH"
# And the tools a connector places on a machine it enrols — the largest of the
# three, because every platform's copy of them is served from here.
TOOLCHAIN_CACHE_PATH="${TOOLCHAIN_CACHE_HOST_PATH:-/home/nictheboy/cheese-toolchain-cache}"
mkdir -p "$TOOLCHAIN_CACHE_PATH" || fail "cannot create $TOOLCHAIN_CACHE_PATH"

OWNERSHIP_REPORT="$(mktemp)"
OWNERSHIP_PATHS=(
  "${WORKSPACES_HOST_PATH:-/home/nictheboy/cheese-workspaces}"
  "${UPLOADS_HOST_PATH:-/home/nictheboy/shared/uploads}"
  "${APPHOME_HOST_PATH:-/home/nictheboy/cheese-app-home}"
  "$CLAUDE_CACHE_PATH"
  "$PI_CACHE_PATH"
  "$TOOLCHAIN_CACHE_PATH"
)
OWNERSHIP_IMAGE="${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$SHA}"
log "checking workspace/uploads ownership…"
OWNERSHIP_REPORT_FILE="$OWNERSHIP_REPORT" \
"$HERE/fix-workspace-ownership.sh" \
  "$OWNERSHIP_IMAGE" \
  "${OWNERSHIP_PATHS[@]}" \
  || fail "workspace ownership migration failed — aborting before swap"
OWNERSHIP_MIGRATED="$(cat "$OWNERSHIP_REPORT" 2>/dev/null || echo no)"
rm -f "$OWNERSHIP_REPORT"

migrate_project_repositories

# ---- Application rollout without downtime (boxes with an app-router) ----
# ACTIVE_BACKEND_DIR names the directory app-router reads its upstreams from;
# the persistent api-front routes business traffic to that separate nginx.
# When it is set, the idle backend slot (and frontend slot, with
# ACTIVE_FRONTEND_DIR) comes up on the new image beside the running one and
# answers its health check, app-router is pointed at both in ONE reload, the old
# backend hands its running work over, and after a drain the old slots are
# stopped. That reload is the release's only cutover:
# every reload retires a generation of app-router workers and, with them, the
# WebSockets they carry, and every backend switch is a handover of the running
# work, so a second switch would be a second disconnect and a second handover.
#
# Unset — prod (RUC), etrip, the test harness — the in-place recreate below
# runs, gap included.
API_FRONT_CONTAINER="${API_FRONT_CONTAINER:-cheese-api-front}"
BACKEND_START_TIMEOUT="${DEPLOY_BACKEND_START_TIMEOUT:-180}"
# How long the slots traffic left keep answering what they already have before
# they are stopped. App-router's old workers do not cut those requests off at
# this point: its worker_shutdown_timeout is set above this drain plus the wait
# for model streams and the backend's stop grace (deploy/llm-tunnel/app-router.conf),
# so what ends a connection on the old slots is the old slot stopping, once.
DRAIN_SECONDS="${DEPLOY_DRAIN_SECONDS:-31}"
# Matches the backend's stop_grace_period in the compose files.
BACKEND_STOP_GRACE_SECONDS="${DEPLOY_BACKEND_STOP_GRACE_SECONDS:-60}"
# How long the old frontend's nginx may take to finish its requests after the
# drain: they then had 5 + 31 + 120 s, inside app-router's 1080 s worker deadline.
FRONTEND_STOP_GRACE_SECONDS="${DEPLOY_FRONTEND_STOP_GRACE_SECONDS:-120}"
# Opt in only after the public ingress uses the standing frontend proxy.
ACTIVE_FRONTEND_DIR="${ACTIVE_FRONTEND_DIR:-}"
FRONTEND_PROXY_PORT="${FRONTEND_PROXY_PORT:-18080}"

# The address every live room's agent dials for its own tools, its chat
# publication and its hooks. It must NOT be a port this deploy takes down: on
# 2026-09-15 it named the backend container's published port, so an ordinary
# release left every working room with `[Errno 111] Connection refused` on every
# tool for as long as the recreate took (four minutes, observed) — the very
# thing the standing api-front and the separately released owner exist to
# prevent, defeated by an address that bypasses both.
#
# Warned, not failed, and only where a central session exists: refusing to
# deploy over a configuration preference is the worse outage. The value names no
# secret, so it is printed.
check_session_base_survives_release() {
  local envf base port
  envf="${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}"
  [ -r "$envf" ] || return 0
  base="$(awk -F= '$1 == "AGENT_SESSION_API_BASE" { sub(/^[^=]*=/, ""); gsub(/["'"'"']/, ""); print; exit }' "$envf")"
  [ -n "$base" ] || return 0
  port="${base##*:}"; port="${port%%/*}"
  case "$port" in
    "$BACKEND_PORT"|"$BACKEND_PORT_NEXT")
      log "WARNING: AGENT_SESSION_API_BASE=$base names :$port, which this deploy"
      log "         replaces and which listens on loopback only — every live"
      log "         room will see its tools, its chat and its hooks refused. Point"
      log "         it at the standing api-front instead (:8081 by default), which"
      log "         routes execution to the owner and swaps backends underneath."
      ;;
  esac
}

# Which slot of a service app-router sends its traffic to, read from the file it
# reads that from: $1 file, $2 service, $3 and $4 the two slots' ports, $5 a
# port that also means the first slot (the frontend's published port, before
# its first switch). Sets SLOT_FROM (serving now) and SLOT_TO/SLOT_TO_PORT (the
# idle one this release starts).
read_slots() {
  local file="$1" service="$2" port_a="$3" port_b="$4" legacy="${5:-}"
  if grep -Fq "server 127.0.0.1:$port_a;" "$file" \
    || { [ -n "$legacy" ] && grep -Fq "server 127.0.0.1:$legacy;" "$file"; }; then
    SLOT_FROM="$service" SLOT_TO="$service-b" SLOT_TO_PORT="$port_b"
  elif grep -Fq "server 127.0.0.1:$port_b;" "$file"; then
    SLOT_FROM="$service-b" SLOT_TO="$service" SLOT_TO_PORT="$port_a"
  else
    fail "$file names neither :$port_a nor :$port_b ($(cat "$file")); point it at the $service that is serving before redeploying"
  fi
}

write_upstream() {
  local file="$1" name="$2" port="$3" tmp
  tmp="$(mktemp "$file.XXXXXX")" || fail "cannot write into $(dirname "$file")"
  printf 'upstream %s { server 127.0.0.1:%s; }\n' "$name" "$port" > "$tmp"
  # Rename, never rewrite in place: nginx re-reads the file on reload and a
  # half-written one would take the whole server config down with it.
  mv -f "$tmp" "$file"
}

# backend-pool.conf names where app-router sends a plain read
# (deploy/llm-tunnel/app-router.conf): the api pool of the slot on $2, with that
# slot's realtime process as the backup that takes a read whenever the pool does
# not answer — or, on a box that runs no pool, that process alone.
pool_port_of() {
  if [ "$1" = "$BACKEND_PORT_NEXT" ]; then
    printf '%s' "$BACKEND_POOL_PORT_NEXT"
  else
    printf '%s' "$BACKEND_POOL_PORT"
  fi
}

write_pool_upstream() {
  local file="$1" port="$2" tmp
  tmp="$(mktemp "$file.XXXXXX")" || fail "cannot write into $(dirname "$file")"
  if [ "$API_POOL_WORKERS" -gt 0 ]; then
    printf 'upstream backend_pool_active { server 127.0.0.1:%s; server 127.0.0.1:%s backup; }\n' \
      "$(pool_port_of "$port")" "$port" > "$tmp"
  else
    printf 'upstream backend_pool_active { server 127.0.0.1:%s; }\n' "$port" > "$tmp"
  fi
  mv -f "$tmp" "$file"
}

# Whether container $1 runs the api pool: its image starts through
# backend/app/serve.py, which runs the pool when API_POOL_WORKERS asks.
runs_api_pool() {
  docker inspect --format '{{json .Config.Cmd}}' "$1" 2>/dev/null | grep -q 'app\.serve'
}

# The backend port backend.conf names now.
serving_backend_port() {
  sed -n 's/.*server 127\.0\.0\.1:\([0-9]*\);.*/\1/p' "$ACTIVE_BACKEND_DIR/backend.conf" | head -n 1
}

# frontend.conf names the frontend app-router serves and, once app-router owns
# them, carries the box's :8080 and :80 (deploy/llm-tunnel/configure-frontend-upstream.sh).
frontend_conf_has_ports() {
  grep -Fq "listen 0.0.0.0:$FRONTEND_PORT;" "$ACTIVE_BACKEND_DIR/frontend.conf" 2>/dev/null
}

write_frontend_conf() {
  local port="$1" with_ports="$2"
  if [ "$with_ports" = true ]; then
    bash "$HERE/llm-tunnel/configure-frontend-upstream.sh" "$ACTIVE_BACKEND_DIR" "$port" \
      "$FRONTEND_PORT" "$FRONTEND_DIRECT_LISTEN"
  else
    bash "$HERE/llm-tunnel/configure-frontend-upstream.sh" "$ACTIVE_BACKEND_DIR" "$port"
  fi || fail "could not write $ACTIVE_BACKEND_DIR/frontend.conf"
}

# Both upstreams in one reload, which retires one generation of app-router
# workers: the release's only switch. $2 is empty when the frontend is not
# rolled (no ACTIVE_FRONTEND_DIR); $3 says whether frontend.conf carries the
# box's ports, which it cannot while the old frontend still publishes them.
switch_app_router() {
  local backend_port="$1" frontend_port="$2" with_ports="${3:-false}" backend_before frontend_before pool_before
  backend_before="$(cat "$ACTIVE_BACKEND_DIR/backend.conf")"
  pool_before="$(cat "$ACTIVE_BACKEND_DIR/backend-pool.conf" 2>/dev/null || true)"
  frontend_before="$(cat "$ACTIVE_BACKEND_DIR/frontend.conf" 2>/dev/null || true)"
  write_upstream "$ACTIVE_BACKEND_DIR/backend.conf" backend_active "$backend_port"
  write_pool_upstream "$ACTIVE_BACKEND_DIR/backend-pool.conf" "$backend_port"
  [ -z "$frontend_port" ] || write_frontend_conf "$frontend_port" "$with_ports"
  if ! docker exec cheese-app-router nginx -t || ! docker exec cheese-app-router nginx -s reload; then
    printf '%s\n' "$backend_before" > "$ACTIVE_BACKEND_DIR/backend.conf"
    printf '%s\n' "$pool_before" > "$ACTIVE_BACKEND_DIR/backend-pool.conf"
    [ -z "$frontend_port" ] || printf '%s\n' "$frontend_before" > "$ACTIVE_BACKEND_DIR/frontend.conf"
    return 1
  fi
  log "app-router now sends backend traffic to :$backend_port${frontend_port:+ and frontend traffic to :$frontend_port}, in one reload"
}

# The release whose old frontend was the compose one still publishing the box's
# :8080 and :80 hands them to app-router once that frontend has stopped: nginx
# cannot bind them before. That is a second reload, on that release only, and
# the ports are closed from the old frontend's stop to it. If app-router cannot
# take them, frontend.conf goes back to the upstream alone and the stopped
# frontend is started again, so the ports are served as before; $1 is its
# container, $2 the port of the frontend that now serves.
take_frontend_ports() {
  local old_frontend="$1" port="$2"
  write_frontend_conf "$port" true
  if docker exec cheese-app-router nginx -t && docker exec cheese-app-router nginx -s reload; then
    log "app-router now serves the box's frontend ports :$FRONTEND_PORT and $FRONTEND_DIRECT_LISTEN"
    return 0
  fi
  write_frontend_conf "$port" false
  docker exec cheese-app-router nginx -s reload >/dev/null 2>&1 || true
  docker start "$old_frontend" >/dev/null 2>&1 \
    || log "WARNING: could not start $old_frontend again; nothing serves :$FRONTEND_PORT now"
  return 1
}

# $1 = host port, $2 = what is expected there. Polls the published port from
# the host, which is what api-front will use, rather than docker's own health
# state — a one-off container may not carry the service healthcheck.
#
# /readyz, not /healthz: both ports answer with a backend, and /healthz only
# says its process is up. /readyz is 503 while the database or Redis is out of
# reach or a route module failed to import (production skips such a module and
# serves 404 for its whole group), so a build like that never takes traffic.
wait_for_ready() {
  local port="$1" what="$2" waited=0 step="$HEALTH_INTERVAL_SECONDS"
  [ "$step" -gt 0 ] 2>/dev/null || step=1
  while [ "$waited" -lt "$BACKEND_START_TIMEOUT" ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${port}/readyz" >/dev/null 2>&1; then
      log "$what answers /readyz on :$port after ${waited}s"
      return 0
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
    waited=$((waited + step))
  done
  # The 503 body names what is not ready (and which route modules did not
  # mount); without it the log would only say that the wait ran out.
  log "$what is not ready on :$port: $(curl -sS -m 3 "http://127.0.0.1:${port}/readyz" 2>&1 | head -c 600)"
  return 1
}

# The running work — sessions, turns, sweeps — belongs to one backend at a time
# (backend/app/core/ownership.py), and used to move only when the old backend
# stopped, after the drain below. Every new turn on the backend traffic had just
# moved to waited for it: on dev on 2026-10-04 a message sent in that window
# waited 40 s (median) for its turn to start, against 0.2 s outside it. So the
# backend traffic has left is told to hand the work over now, with SIGUSR1, and
# goes on answering what it still has through the drain. The pause first lets a
# request already on its way there land before the work moves; on dev nearly
# all of them completed within 5 s of a switch.
HANDOVER_AFTER_SECONDS="${DEPLOY_HANDOVER_AFTER_SECONDS:-5}"

# Does the backend in container $1 catch SIGUSR1? One built before the handler
# existed would be killed by it outright — that is the signal's default action —
# so the first release carrying the handler moves the work the old way, at stop.
catches_handover_signal() {
  docker exec "$1" python -c '
import os, pathlib, sys
for status in pathlib.Path("/proc").glob("[0-9]*/status"):
    if status.parent.name == str(os.getpid()):
        continue
    try:
        command = (status.parent / "cmdline").read_bytes()
        fields = dict(line.split(":", 1) for line in status.read_text().splitlines() if ":" in line)
    except OSError:
        continue
    if b"bin/uvicorn" in command and int(fields["SigCgt"], 16) & (1 << 9):
        sys.exit(0)
sys.exit(1)
' >/dev/null 2>&1
}

hand_over_running_work() {
  local container="$1"
  [ -n "$container" ] || return 0
  if ! catches_handover_signal "$container"; then
    log "$container does not take the handover signal; its running work moves when it stops"
    return 0
  fi
  sleep "$HANDOVER_AFTER_SECONDS"
  if docker kill --signal USR1 "$container" >/dev/null; then
    log "told $container to hand its running work over; it keeps serving through the drain"
  else
    log "could not signal $container; its running work moves when it stops"
  fi
}

# A model stream relayed through /llm/v1 runs for minutes on a reasoning model
# and cannot move to the next backend: stopping the one that relays it ends it,
# and the harness that asked loses the whole answer. So the old backend is
# stopped only once the streams it relays have ended, or this bound, counted
# from the switch, has run out (deploy/wait-model-streams.sh). app-router's
# worker_shutdown_timeout (deploy/llm-tunnel/app-router.conf) stays above the
# handover pause, the drain, this bound and the stop grace together, so the
# worker carrying such a stream does not end it first.
MODEL_STREAM_GRACE_SECONDS="${DEPLOY_MODEL_STREAM_GRACE_SECONDS:-900}"
MODEL_STREAM_POLL_SECONDS="${DEPLOY_MODEL_STREAM_POLL_SECONDS:-5}"
# A rollback is already the second switch of a failed release; the streams on
# the release it leaves get only this long.
ROLLBACK_MODEL_STREAM_GRACE_SECONDS="${DEPLOY_ROLLBACK_MODEL_STREAM_GRACE_SECONDS:-30}"

# $1 = the old backend's host port, $2 = its container, $3 = how long to wait.
wait_for_model_streams() {
  local line
  bash "$HERE/wait-model-streams.sh" "$1" "$2" "$3" "$MODEL_STREAM_POLL_SECONDS" 2>&1 \
    | while IFS= read -r line; do log "$line"; done || true
}

# The old backend is the last thing a release stops, after its health check:
# waiting for its model streams any earlier would hold a release that then
# fails its health check on the broken build for that long before it rolls
# back. rollout_app names it here; retire_old_backend stops and removes it.
# $1 = streams: wait for its model streams up to the bound's deadline.
# $1 = now: stop it at once, saying why ($2) — a failed release, an exit.
retire_old_backend() {
  local left
  [ -n "$RETIRING_BACKEND_SERVICE" ] || return 0
  if [ -n "$RETIRING_BACKEND" ]; then
    if [ "$1" = streams ]; then
      left=$((RETIRING_DEADLINE - SECONDS))
      [ "$left" -gt 0 ] || left=0
      wait_for_model_streams "$RETIRING_BACKEND_PORT" "$RETIRING_BACKEND" "$left"
    else
      log "stopping $RETIRING_BACKEND without waiting for its model streams: $2"
    fi
    docker stop --time "$BACKEND_STOP_GRACE_SECONDS" "$RETIRING_BACKEND" >/dev/null 2>&1 || true
  fi
  dc rm -f "$RETIRING_BACKEND_SERVICE" >/dev/null 2>&1 || true
  log "$RETIRING_BACKEND_SERVICE stopped"
  RETIRING_BACKEND="" RETIRING_BACKEND_SERVICE="" RETIRING_BACKEND_PORT="" RETIRING_DEADLINE=0
}

wait_for_frontend() {
  local port="$1" container="$2" waited=0 step="$HEALTH_INTERVAL_SECONDS"
  [ "$step" -gt 0 ] 2>/dev/null || step=1
  while [ "$waited" -lt "$BACKEND_START_TIMEOUT" ]; do
    if docker exec "$container" /usr/local/bin/check-static-assets /usr/share/nginx/html >/dev/null 2>&1 \
      && curl -fsS -m 3 "http://127.0.0.1:$port/" >/dev/null 2>&1; then
      log "$container serves complete frontend assets on :$port after ${waited}s"
      return 0
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
    waited=$((waited + step))
  done
  return 1
}

# ---- Functional smoke test of the new slots, before the switch ----
# /readyz says the new backend can serve, not that anyone can sign in, open a
# task or hear from the server. So the new slots are used the way a person
# uses them (deploy/release-smoke.py: the sign-in route, /users/me, the smoke
# account's own task, the inbox WebSocket, sign-out, the frontend's index and
# one hashed script), on their loopback ports, before app-router sends them
# anything. A failure leaves app-router, and the slots serving, as they were.
#
# Before the switch and not after it: everything checked is served by the new
# slots themselves, and the only thing the switch changes is the upstream line
# app-router reads, which the routed /readyz after rollout_app already probes.
# A check after the switch could only fail into a rollback, a second switch.
#
# The account is the deploy's own and has no password: `provision` finds or
# makes it in this release's image and opens a session for it, which reaches
# `check` through the environment and never the log. A password sign-in would
# let anyone who sends that handle wrong passwords hold every release back
# (deploy/release-smoke.py says why). Only boxes with app-router slots get
# here; the others (prod, etrip) never create the account.
#
# A rollback returns to the release that served before this one, and is not
# held back by a smoke test of it.
SMOKE_RELEASE=true
# $1 the new backend's port, $2 the new frontend's ('' when it is not rolled).
smoke_release() {
  local backend_port="$1" frontend_port="$2" image out session line rc=0
  local DEPLOY_SMOKE_USER_ID="" DEPLOY_SMOKE_TOKEN="" DEPLOY_SMOKE_REFRESH=""
  if [ "$SMOKE_RELEASE" != true ]; then
    log "smoke: not run for a rollback; that release served before this one"
    return 0
  fi
  image="${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$IMAGE_TAG}"
  log "smoke: checking the new slots before the switch (backend :$backend_port${frontend_port:+, frontend :$frontend_port})"
  out="$(dc run --rm --no-deps -T backend python - provision < "$HERE/release-smoke.py" 2>&1)" || rc=$?
  # The session line carries its tokens: it is kept out of the log.
  printf '%s\n' "$out" | { grep -v '^smoke-session ' || true; } | while IFS= read -r line; do log "  $line"; done
  [ "$rc" = 0 ] || return "$rc"
  session="$(printf '%s\n' "$out" | sed -n 's/^smoke-session //p' | tail -n 1)"
  read -r DEPLOY_SMOKE_USER_ID DEPLOY_SMOKE_TOKEN DEPLOY_SMOKE_REFRESH <<< "$session" || true
  if [ -z "${DEPLOY_SMOKE_REFRESH:-}" ]; then
    log "  smoke provision FAILED it opened no session"
    return 1
  fi
  # The tokens travel in the environment, never on a command line.
  DEPLOY_SMOKE_USER_ID="$DEPLOY_SMOKE_USER_ID" DEPLOY_SMOKE_TOKEN="$DEPLOY_SMOKE_TOKEN" \
    DEPLOY_SMOKE_REFRESH="$DEPLOY_SMOKE_REFRESH" docker run --rm -i --network host \
    -e DEPLOY_SMOKE_USER_ID -e DEPLOY_SMOKE_TOKEN -e DEPLOY_SMOKE_REFRESH "$image" python - check \
    --backend "http://127.0.0.1:$backend_port" \
    ${frontend_port:+--frontend "http://127.0.0.1:$frontend_port"} < "$HERE/release-smoke.py" 2>&1 \
    | while IFS= read -r line; do log "  $line"; done || rc=$?
  return "$rc"
}

rollout_app() {
  local backend_from backend_to backend_port old_backend old_backend_port handover switched_at
  local frontend_from="" frontend_to="" frontend_port="" old_frontend="" collab_failed=false
  local frontend_holds_ports=false frontend_stop_grace="$FRONTEND_STOP_GRACE_SECONDS" ports_failed=false
  read_slots "$ACTIVE_BACKEND_DIR/backend.conf" backend "$BACKEND_PORT" "$BACKEND_PORT_NEXT"
  backend_from="$SLOT_FROM" backend_to="$SLOT_TO" backend_port="$SLOT_TO_PORT"
  old_backend="$(service_container "$backend_from")"
  old_backend_port="$BACKEND_PORT"
  [ "$backend_from" = backend ] || old_backend_port="$BACKEND_PORT_NEXT"
  if [ -n "$ACTIVE_FRONTEND_DIR" ]; then
    [ -f "$ACTIVE_FRONTEND_DIR/sites-frontend.conf" ] || fail "frontend proxy must be configured before enabling rollout"
    read_slots "$ACTIVE_BACKEND_DIR/frontend.conf" frontend "$FRONTEND_SLOT_PORT" "$FRONTEND_PORT_NEXT" "$FRONTEND_PORT"
    frontend_from="$SLOT_FROM" frontend_to="$SLOT_TO" frontend_port="$SLOT_TO_PORT"
    old_frontend="$(service_container "$frontend_from")"
    # The compose frontend app-router has served since before the two slots
    # still publishes the box's ports itself.
    if [ "$frontend_from" = frontend ] && ! frontend_conf_has_ports \
      && grep -Fq "server 127.0.0.1:$FRONTEND_PORT;" "$ACTIVE_BACKEND_DIR/frontend.conf"; then
      frontend_holds_ports=true
      # Its traffic has moved and its drain is over by the time it stops; the
      # ports are closed while it stops, so it does not wait on open sockets.
      frontend_stop_grace=5
    fi
  fi

  log "starting $backend_to on :$backend_port beside the serving ${backend_from}…"
  if ! dc up -d --no-deps --force-recreate "$backend_to"; then
    dc rm -f -s "$backend_to" >/dev/null 2>&1 || true
    fail "could not start $backend_to; the running backend was not touched"
  fi
  if ! wait_for_ready "$backend_port" "$backend_to"; then
    docker logs --tail 40 "$(service_container "$backend_to")" 2>&1 | sed 's/^/  next| /' || true
    dc rm -f -s "$backend_to" >/dev/null 2>&1 || true
    fail "$backend_to was not ready within ${BACKEND_START_TIMEOUT}s; the running backend was not touched"
  fi
  # Only an image that runs app/serve.py has a pool to wait for. A rollback to a
  # release from before it starts a backend with no pool at all; app-router
  # then sends its reads to the backup, that backend itself.
  if [ "$API_POOL_WORKERS" -gt 0 ] && ! runs_api_pool "$(service_container "$backend_to")"; then
    log "$backend_to's image runs no api pool; plain reads go to its realtime process"
  elif [ "$API_POOL_WORKERS" -gt 0 ] && ! wait_for_ready "$(pool_port_of "$backend_port")" "$backend_to api pool"; then
    docker logs --tail 40 "$(service_container "$backend_to")" 2>&1 | sed 's/^/  next| /' || true
    dc rm -f -s "$backend_to" >/dev/null 2>&1 || true
    fail "$backend_to's api pool was not ready within ${BACKEND_START_TIMEOUT}s; the running backend was not touched"
  fi
  if [ -n "$frontend_to" ]; then
    if ! dc up -d --no-deps --force-recreate "$frontend_to" \
      || ! wait_for_frontend "$frontend_port" "$(service_container "$frontend_to")"; then
      dc rm -f -s "$backend_to" "$frontend_to" >/dev/null 2>&1 || true
      fail "next frontend ($frontend_to) is unhealthy; the running backend and frontend were not touched"
    fi
  fi
  if ! smoke_release "$backend_port" "$frontend_port"; then
    dc rm -f -s "$backend_to" ${frontend_to:+"$frontend_to"} >/dev/null 2>&1 || true
    # The first forge migration stops the backends a release replaces
    # (migrate_project_repositories): then nothing serves until one passes.
    if [ -z "$old_backend" ] || [ "$(docker inspect --format '{{.State.Running}}' "$old_backend" 2>/dev/null)" != true ]; then
      fail "the new slots failed the smoke test (the FAILED line above names the check) and were removed; $backend_from is not running (the repository migration stops it), so no backend serves until a release passes"
    fi
    fail "the new slots failed the smoke test (the FAILED line above names the check) and were removed; app-router was not switched and still points at $backend_from${frontend_from:+ and $frontend_from}"
  fi

  local ports_now=false
  [ -z "$frontend_to" ] || [ "$frontend_holds_ports" = true ] || ports_now=true
  if ! switch_app_router "$backend_port" "$frontend_port" "$ports_now"; then
    dc rm -f -s "$backend_to" ${frontend_to:+"$frontend_to"} >/dev/null 2>&1 || true
    fail "app-router did not take the switch; its upstream files are restored and traffic stays on $backend_from${frontend_from:+ and $frontend_from}"
  fi
  switched_at=$SECONDS
  # From here nothing exits early: the old frontend is always stopped and the
  # old backend handed to retire_old_backend, which the exit trap runs too, so
  # a failure leaves one backend and one frontend, and the next release starts
  # from a clean state.
  #
  # The handover keeps its own clock from the switch.
  hand_over_running_work "$old_backend" &
  handover=$!
  # Replaced at once, while the old frontend still runs: an editor's socket
  # closes once, and reconnects through the new frontend to the new collab. It
  # loads documents from and stores them to the backend, which the new slot
  # already is; every change is stored before it stops (stop_grace_period).
  if [ "$COLLAB_EXPECTED" = true ]; then
    log "bringing up the collaboration service…"
    dc up -d --no-deps collab || collab_failed=true
  fi
  wait "$handover" || true
  # New requests reach only the new slots now. The old ones finish what they
  # have: through the drain, then a graceful stop — SIGQUIT for the frontend's
  # nginx, which closes its sockets as its requests end, and SIGTERM for the
  # backend, which still hands over whatever it holds (`rm -f` alone is a
  # SIGKILL that cuts that off). The backend is stopped by retire_old_backend,
  # after the release's health check, once the model streams it still relays
  # have ended or their bound has run out.
  sleep "$DRAIN_SECONDS"
  if [ -n "$old_frontend" ]; then
    docker stop --time "$frontend_stop_grace" "$old_frontend" >/dev/null 2>&1 &
  fi
  RETIRING_BACKEND="$old_backend" RETIRING_BACKEND_SERVICE="$backend_from"
  RETIRING_BACKEND_PORT="$old_backend_port"
  RETIRING_DEADLINE=$((switched_at + HANDOVER_AFTER_SECONDS + DRAIN_SECONDS + MODEL_STREAM_GRACE_SECONDS))
  [ -n "$old_backend" ] || retire_old_backend now
  wait || true
  if [ "$frontend_holds_ports" = true ] && ! take_frontend_ports "$old_frontend" "$frontend_port"; then
    ports_failed=true
  fi
  # A frontend started again to keep the ports is left for the next release,
  # which replaces it in its slot and takes the ports over at that switch.
  if [ -n "$frontend_from" ] && [ "$ports_failed" = false ]; then
    dc rm -f "$frontend_from" >/dev/null 2>&1 || true
  fi
  [ "$ports_failed" = false ] \
    || fail "app-router could not take the box's ports :$FRONTEND_PORT and $FRONTEND_DIRECT_LISTEN (see the nginx error above); $old_frontend serves them again, and the next release takes them over"
  [ "$collab_failed" = false ] || fail "compose up collab failed"
  log "${frontend_from:+$frontend_from stopped; }$backend_to${frontend_to:+ and $frontend_to} serve this release; $backend_from stops after the health check"
  if [ "$backend_to" = backend-b ]; then
    # The script before the two slots starts its one-off successors on these
    # ports and refuses while app-router names them.
    log "note: a release of a commit older than the two slots (a revert included) reloads app-router, pulls, migrates and then refuses to switch while backend-b serves; dispatch a release of a commit that has the slots once to move back to the first slots, then that one"
  fi
}

# First installation must use the backend image this deploy just pulled or
# verified. Once running, ensure_device_connection_owner deliberately leaves it
# untouched: only hold_owners_behind_schema above, or the separate owner
# release operation, replaces it.
export DEVICE_CONNECTION_IMAGE="${DEVICE_CONNECTION_IMAGE:-${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$SHA}}"
export PREVIEW_CONNECTION_IMAGE="${PREVIEW_CONNECTION_IMAGE:-${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$SHA}}"
ensure_journal_retention
ensure_device_connection_owner
# Before ensure_application_router: the routes must point at a healthy owner
# before they are installed, and before any backend restarts into owner mode.
ensure_preview_connection_owner
ensure_application_router
check_session_base_survives_release

if [ -n "$ACTIVE_BACKEND_DIR" ]; then
  [ -d "$ACTIVE_BACKEND_DIR" ] \
    || fail "ACTIVE_BACKEND_DIR=$ACTIVE_BACKEND_DIR does not exist — run deploy/llm-tunnel/up.sh first"
  rollout_app
  # Forge migration stops the old backend. Probe the routed backend only after
  # its replacement is serving, including retries from a persisted cutover.
  wait_for_ready 18085 "application router" || fail "the backend behind the application router is not ready"
  if [ -z "$ACTIVE_FRONTEND_DIR" ]; then
    log "bringing up frontend…"
    dc up -d --no-deps frontend || fail "compose up frontend failed"
  fi
else
  log "smoke: skipped, this box has no app-router slots (ACTIVE_BACKEND_DIR unset) to check a release on before it serves"
  log "bringing up backend + frontend…"
  # The frontend waits on the backend's healthcheck, which is /readyz, so a
  # build that never becomes ready makes this `up` fail. Leave that verdict to
  # the health wait below: it is the one that rolls back to $PREV_SHA, and
  # failing here would leave the broken build in place.
  # Nothing below that assumes a serving backend runs either: the preview
  # routes stay where they are and collab is not replaced.
  if ! dc up -d backend frontend; then
    log "WARNING: compose up did not complete; the health check below decides"
    app_up=failed
  fi
fi
if [ "${app_up:-}" != failed ]; then
  # The legacy half of the preview kill switch. The backends above are serving
  # legacy now, so it is safe to move the routes off a still-running owner and
  # stop it — see retire_preview_connection_owner. It is a no-op in owner mode
  # and when no owner is running.
  retire_preview_connection_owner
  # After the backend it loads documents from and stores them to. Replacing it
  # closes open documents for a moment; every change is stored before it stops
  # (stop_grace_period) and the editors reconnect on their own. A box with an
  # app-router replaces it inside rollout_app, before the old frontend stops.
  if [ "$COLLAB_EXPECTED" = true ] && [ -z "$ACTIVE_BACKEND_DIR" ]; then
    log "bringing up the collaboration service…"
    dc up -d --no-deps collab || fail "compose up collab failed"
  fi
fi
export COLLAB_EXPECTED

# Same reasoning as the pull: never `fail` on these. A browser that will not
# start must not hold back a backend that would have served.
#
# But say WHY, and say it when it works too. These two lines used to send both
# streams to /dev/null, which cost real time: `cheese-browser-render` failed
# every deploy for weeks with nothing but "did not start", and the reason —
# a container of that name left behind by a manual `docker compose up`, so
# compose could never create its own — was printed by docker on every attempt
# and discarded by this script on every attempt.
#
# The eviction is what actually unblocks it: these services declare a fixed
# `container_name`, and while anything else holds that name compose cannot
# create its own. See deploy/evict-foreign-container.sh.
start_optional_service() {
  service="$1"
  container="$2"
  consequence="$3"
  evicted="$("$HERE/evict-foreign-container.sh" "$container" "$PROJECT" 2>&1)" \
    || log "WARNING: could not free $container; $service cannot start while it is held"
  # An `[ -n … ] && log` here would be the last command of this branch under
  # `set -e`, so the common case — nothing to evict, empty string — would end
  # the deploy.
  if [ -n "${evicted:-}" ]; then
    log "$evicted"
  fi
  if out="$(dc up -d "$service" 2>&1)"; then
    log "$service is up"
    return 0
  fi
  log "WARNING: $service did not start; $consequence"
  printf '%s\n' "$out" | tail -n 5 | while IFS= read -r line; do
    [ -n "$line" ] && log "  $service: $line"
  done
  return 0
}

start_optional_service browser-render cheese-browser-render \
  "fetching will fall back a rung"
start_optional_service office-render cheese-office-render \
  "documents will offer download only"
start_optional_service office-editor cheese-office-editor \
  "room files stay read-only"

log "waiting for health…"
code=""
app_tier=""
for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
  sleep "$HEALTH_INTERVAL_SECONDS"
  app_tier="$(docker ps -a --no-trunc \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.Label "com.docker.compose.service"}}\t{{.Image}}\t{{.State}}\t{{.Status}}' \
    2>/dev/null || true)"
  # The old backend still finishing its model streams is not part of this
  # release; retire_old_backend removes it after this check.
  if [ -n "$RETIRING_BACKEND_SERVICE" ]; then
    app_tier="$(printf '%s\n' "$app_tier" | awk -F '\t' -v old="$RETIRING_BACKEND_SERVICE" '$1 != old')"
  fi
  if printf '%s\n' "$app_tier" | "$HERE/check-app-tier.sh" "$SHA" \
    >/dev/null 2>&1; then
    code=ok
    break
  fi
done

if [ "$code" != ok ]; then
  log "HEALTH CHECK FAILED"
  printf '%s\n' "$app_tier" | "$HERE/check-app-tier.sh" "$SHA" || true
  if [ -f "$FORGE_CUTOVER_PENDING" ]; then
    fail "repository migration completed; refusing to restart the legacy repository writer; retry this release from migration receipts"
  fi
  # The previous release goes back into the slot the old backend holds.
  retire_old_backend now "this release failed its health check"
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
      log "handing the bind mounts back to ${PREVIOUS_AGENT_UID:-1001} before starting ${PREV_SHA}…"
      AGENT_UID="${PREVIOUS_AGENT_UID:-1001}" \
      AGENT_GID="${PREVIOUS_AGENT_GID:-1001}" \
      FORCE_OWNERSHIP_FIX=1 \
      "$HERE/fix-workspace-ownership.sh" \
        "$OWNERSHIP_IMAGE" \
        "${OWNERSHIP_PATHS[@]}" \
        || log "WARNING: could not hand the mounts back to ${PREVIOUS_AGENT_UID:-1001} — $PREV_SHA will come up on a tree it cannot read; re-run the deploy or chown by hand"
    fi
    if [ -n "$ACTIVE_BACKEND_DIR" ]; then
      # This release serves from the slots it started, so the previous one goes
      # back into the idle slots the same way: up beside it, one switch, a
      # handover, the failed slots stopped. The second slot is rewritten first,
      # because it carries the images it was written with.
      (
        if [ -n "$PREV_BACKEND_IMAGE" ] && [ -n "$PREV_FRONTEND_IMAGE" ]; then
          export BACKEND_IMAGE="$PREV_BACKEND_IMAGE" FRONTEND_IMAGE="$PREV_FRONTEND_IMAGE"
        fi
        export IMAGE_TAG="$PREV_SHA" APP_RELEASE="$PREV_RELEASE"
        # shellcheck disable=SC2030 # the rollback's own choice, for this subshell only
        COLLAB_IMAGE="$(rollback_collab_image)"
        export COLLAB_IMAGE
        write_slots_overlay
        MODEL_STREAM_GRACE_SECONDS="$ROLLBACK_MODEL_STREAM_GRACE_SECONDS"
        SMOKE_RELEASE=false
        # A subshell does not run the script's exit trap.
        trap 'retire_old_backend now "the rollback did not complete"' EXIT
        rollout_app
        retire_old_backend streams
      ) || log "WARNING: the rollback to $PREV_SHA did not complete; see the lines above"
    elif [ -n "$PREV_BACKEND_IMAGE" ] && [ -n "$PREV_FRONTEND_IMAGE" ]; then
      BACKEND_IMAGE="$PREV_BACKEND_IMAGE" \
        FRONTEND_IMAGE="$PREV_FRONTEND_IMAGE" COLLAB_IMAGE="$(rollback_collab_image)" \
        IMAGE_TAG="$PREV_SHA" APP_RELEASE="$PREV_RELEASE" \
        dc up -d backend frontend collab || true
    else
      COLLAB_IMAGE="$(rollback_collab_image)" IMAGE_TAG="$PREV_SHA" APP_RELEASE="$PREV_RELEASE" \
        dc up -d backend frontend collab || true
    fi
  fi
  fail "deploy failed health check${PREV_SHA:+, rolled back to $PREV_SHA}"
fi

# Healthy: now the backend traffic left may finish its model streams and stop.
retire_old_backend streams

# Keep the cutover guard across failed releases until the new app is healthy.
if [ -f "$FORGE_EXECUTOR_RESTART" ]; then
  sudo -n systemctl start cheese.service || fail "could not restore the host executor"
  systemctl is-active --quiet cheese.service || fail "host executor did not become active"
  rm -f "$FORGE_EXECUTOR_RESTART"
fi
rm -f "$FORGE_CUTOVER_PENDING"

# Host clock survives API rollouts. Non-systemd installations must arrange an
# external minute trigger; startup/reconnect recovery alone is not a timer.
if [ -d /run/systemd/system ]; then
  bash "$HERE/install-room-cleanup-timer.sh" \
    || fail "backend is healthy, but archived-room cleanup timer installation failed"
else
  log "no systemd host: schedule deploy/trigger-room-cleanup.sh externally every minute"
fi

# The runner running this deploy can hang "active" with no way for systemd to
# notice (deploy/runner-watchdog.sh). Install its watchdog while we are on it.
# A failure here only warns: the release itself is already healthy, and the
# next deploy retries the install.
runner_root="${RUNNER_TEMP:-}"; runner_root="${runner_root%/_work/*}"
if [ -d /run/systemd/system ] && [ -n "${RUNNER_TEMP:-}" ] && [ -f "$runner_root/.service" ]; then
  bash "$HERE/install-runner-watchdog.sh" "$runner_root" \
    || log "warning: runner watchdog installation failed; the release is unaffected"
fi

# Best-effort like its pull: only a retainer that pull prepared is handed over.
# The new one already protects the new image, so removing the old retainer
# never leaves either deployment's image unreferenced during the handoff.
if docker container inspect "${PROJECT}-private-executor-image-retainer-next" >/dev/null 2>&1; then
  docker rm -f "${PROJECT}-private-executor-image-retainer" >/dev/null 2>&1 || true
  docker rename "${PROJECT}-private-executor-image-retainer-next" \
    "${PROJECT}-private-executor-image-retainer" \
    || log "WARNING: could not retain the private-executor image; the prune below may remove it"
fi

# Reclaim disk from superseded per-commit images: every deploy pulls a fresh
# 6-7GB image set and nothing ever pruned them — the dev box filled its disk to
# 100% (2026-07-18) and CD wedged for a day. Best-effort, never fails a deploy.
# NOT time-filtered: under a busy merge day every image is "too new" to prune
# and the disk fills anyway (happened twice on 2026-07-18/19 — 8 image sets in
# an afternoon). Keep what running containers and the explicit image retainers
# use; rollback re-pulls superseded images from ghcr.
#
# Done inline rather than left to the EXIT trap so the reclaim and its disk
# watermark still print before "DEPLOY OK"; clearing RECLAIM_PENDING is what
# stops the trap from repeating it.
reclaim_docker_disk "successful deploy" true
RECLAIM_PENDING=0
# The other half of what fills this box. Docker images are one; the package
# caches rooms kept before they shared a project store are the other, and they
# are the half nothing was reclaiming: the launcher drops a room's copies when
# that room next starts, but a room only starts when someone uses it, and the
# rooms holding the most disk are the ones nobody has opened in a month.
#
# Here rather than on a timer because a deploy is when someone is watching: the
# line it prints names the directory it swept, so a box where rooms belong to a
# different user than the deploy says "0 rooms" next to that path instead of
# quietly reclaiming nothing forever. Best-effort, exactly like the reclaim
# above — a box with no rooms on it is the normal case, not a failure.
bash "$HERE/reclaim-room-caches.sh" --apply 2>&1 | sed 's/^/  /' || true
# And the checkouts the old layout left behind. Until #936 a room's working
# directory was `~/.cheese/work/<project>/<room>`; nothing has written there
# since, and on dev that was still 107GB. Gated on publication, from the same
# module archival uses — a checkout holding work that never left the box is the
# user's only copy of it, and is kept and reported instead.
python3 "$HERE/reclaim-legacy-room-checkouts.py" --apply 2>&1 | sed 's/^/  /' || true
echo "$(date -Iseconds) $SHA" >> "$HERE/deploy-docker.log"
# What this release chose for the services named by digest, for the checks
# that run outside a deploy (deploy-drift.yml, scripts/whats-live.sh).
mkdir -p "$HOME/ops"
{
  # shellcheck disable=SC2031 # this release's choice; only a rollback changed it, in its subshell
  [ -z "${COLLAB_IMAGE:-}" ] || printf 'COLLAB_IMAGE=%s\n' "$COLLAB_IMAGE"
  [ -z "${FORGE_EVENTS_IMAGE:-}" ] || [ "${FORGE_EVENTS_LOCAL:-false}" != true ] || printf 'FORGE_EVENTS_IMAGE=%s\n' "$FORGE_EVENTS_IMAGE"
} > "$HOME/ops/app-tier-pins"
log "DEPLOY OK: sha=$SHA healthy"
