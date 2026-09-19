#!/usr/bin/env bash
# Point this machine's docker daemon at a pull-through mirror for Docker Hub.
#
# The pool cannot count on reaching Docker Hub. From here auth.docker.io closes
# the connection mid-handshake ("failed to fetch anonymous token: … EOF"), not
# always and not predictably — it took the canary's mock API out on two nights
# and the sandbox test's base image out before that. A credential does not
# address it: the token endpoint is the thing that will not answer, so an
# authenticated pull fails in the same place an anonymous one does.
#
# Every image reference we write is already `mirror.gcr.io/…`, but the ones we
# do not write are not: a `FROM debian:12-slim` inside an image we build, a
# compose file's `paradedb/paradedb`, a test that runs `docker run
# python:3.12-slim`. Each of those has been patched at its own call site, after
# it broke something. At the daemon they are all covered at once, including the
# next one nobody has written yet.
#
# Only docker.io is mirrored. ghcr.io, where our own images live, is untouched.
# mirror.gcr.io is a pull-through cache, so an image it has not seen it fetches
# from Docker Hub itself — over Google's network rather than ours — and a
# digest-pinned reference keeps its digest.
#
# `registry-mirrors` is one of the options dockerd re-reads on SIGHUP, which is
# why this reloads rather than restarts: a job running on this machine's other
# slot must not lose its containers to a change it has nothing to do with.
# Idempotent — the provisioning step for a new machine and the repair step for
# one whose daemon.json was replaced.
set -euo pipefail

MIRROR="${CHEESE_CI_REGISTRY_MIRROR:-https://mirror.gcr.io}"
CONFIG=/etc/docker/daemon.json

# Merged, never overwritten: whatever else a machine's daemon.json says is
# somebody's decision, and this script knows about exactly one key.
merged="$(sudo python3 - "$CONFIG" "$MIRROR" <<'PY'
import json, sys, pathlib
path, mirror = pathlib.Path(sys.argv[1]), sys.argv[2]
config = {}
if path.exists() and path.read_text().strip():
    config = json.loads(path.read_text())
mirrors = config.get("registry-mirrors", [])
if mirror not in mirrors:
    mirrors.insert(0, mirror)
config["registry-mirrors"] = mirrors
print(json.dumps(config, indent=2))
PY
)"

if [ "$(sudo cat "$CONFIG" 2>/dev/null || true)" = "$merged" ]; then
  echo "registry mirror already configured: $MIRROR"
else
  sudo mkdir -p "$(dirname "$CONFIG")"
  printf '%s\n' "$merged" | sudo tee "$CONFIG" >/dev/null
  sudo systemctl reload docker
  echo "registry mirror configured: $MIRROR"
fi

# The daemon is what has to believe it, not the file: a malformed merge or a
# daemon that refused the reload would otherwise be found by the next red job.
configured="$(docker info --format '{{.RegistryConfig.Mirrors}}')"
case "$configured" in
  *"$MIRROR"*) echo "docker info reports: $configured" ;;
  *) echo "daemon did not pick up $MIRROR (reports: $configured)" >&2; exit 1 ;;
esac
