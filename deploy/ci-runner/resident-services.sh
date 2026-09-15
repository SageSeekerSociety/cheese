#!/usr/bin/env bash
# Bring up this machine's resident Postgres and Valkey for CI jobs.
#
# One set per MACHINE, not one per job. A job's own service containers bind host
# ports 5432/6379, so two jobs on one machine collide on the port — and each
# brings a 3 GB tmpfs for Postgres, which two of do not fit in 8 GB of RAM. Both
# are why a pool machine could only ever run one job at a time. Resident, the
# port is bound once and the tmpfs exists once, and the two runner slots tell
# their data apart by name instead (see backend/tests/isolation.py).
#
# Idempotent: it is the provisioning step for a new machine and the repair step
# for one whose containers were pruned away.
#
# Not on 5432/6379: a job that still brings its own service containers binds
# those, and it has to keep working while branches cut before this change are
# still in flight. The pair here answer on 5442/6389 instead.
set -euo pipefail

PG_IMAGE="mirror.gcr.io/paradedb/paradedb:v0.18.8-pg16@sha256:8a14fee5257f554a60d70afc89490a6460a9833c3f7f99f7d88dbbf12e4042a2"
VALKEY_IMAGE="mirror.gcr.io/valkey/valkey:8.0.2@sha256:57bcc49c6ade1813ef25206c571b65b66bb0094235ff7fb767941622892297d9"
# Data in RAM: these VMs share one Proxmox disk with every other guest there, and
# a Postgres at its defaults fsyncs every commit onto it — measured 2026-09-02, a
# 4 KB sync write cost 3.5 ms on a busy host and the DB-bound part of the suite
# ran 4-5x slower. Nothing in a CI database outlives the job.
PG_TMPFS_SIZE="${CHEESE_CI_PG_TMPFS:-3g}"
# Postgres defaults to 100 connections. A job's own container had all of them;
# here a machine's slots share one server, and each run opens a connection per
# xdist worker per test database — so the default is a ceiling two runs can
# reach together, and reaching it is `FATAL: sorry, too many clients already` in
# whichever run got there second.
PG_MAX_CONNECTIONS="${CHEESE_CI_PG_MAX_CONNECTIONS:-300}"
# Redis ships 16 databases; each runner slot takes a block of that many, so the
# server has to offer more than one block's worth.
VALKEY_DATABASES="${CHEESE_CI_VALKEY_DATABASES:-64}"

ensure() {
  local name="$1"; shift
  if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = true ]; then
    echo "$name already running"
    return 0
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --restart unless-stopped "$@" >/dev/null
  echo "$name started"
}

ensure cheese-ci-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cheese_test \
  -p 127.0.0.1:5442:5432 \
  --tmpfs "/var/lib/postgresql/data:rw,size=$PG_TMPFS_SIZE" \
  --health-cmd="pg_isready -U postgres" --health-interval=10s \
  --health-timeout=5s --health-retries=5 \
  "$PG_IMAGE" -c max_connections="$PG_MAX_CONNECTIONS"

ensure cheese-ci-valkey \
  -p 127.0.0.1:6389:6379 \
  --health-cmd="valkey-cli ping" --health-interval=10s \
  --health-timeout=5s --health-retries=5 \
  "$VALKEY_IMAGE" valkey-server --databases "$VALKEY_DATABASES"

for name in cheese-ci-postgres cheese-ci-valkey; do
  for _ in $(seq 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null)" = healthy ] && break
    sleep 1
  done
  echo "$name: $(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null)"
done
