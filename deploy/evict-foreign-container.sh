#!/usr/bin/env bash
# Remove a container that is squatting on a name this deployment needs.
#
#     evict-foreign-container.sh <container> <our-compose-project>
#
# Services that declare a fixed `container_name` cannot be started at all while
# something else holds that name: compose fails with "Conflict. The container
# name ... is already in use", on every deploy, forever. Nothing retries past it
# and no timeout expires, so it does not look like an outage — the service is
# simply never there. `cheese-browser-render` sat like that from the day its
# image began building, still serving a hand-built image somebody started once
# from a compose file of its own.
#
# Ownership comes from `com.docker.compose.project`, so this removes only what
# another project — or no project at all — put there, and never a container this
# deployment started. Exits 0 when there is nothing to do, so a caller can run it
# unconditionally.
set -euo pipefail

container="${1:?usage: evict-foreign-container.sh <container> <project>}"
project="${2:?usage: evict-foreign-container.sh <container> <project>}"

# A missing container makes `docker inspect` fail; a container with no compose
# labels makes it succeed with an empty string. Those are different situations
# and only the first one means "nothing to do" — a plain `docker run --name …`
# holds the name just as firmly as another compose project does.
if ! owner="$(docker inspect "$container" \
  --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)"; then
  exit 0
fi

if [ "$owner" = "$project" ]; then
  exit 0
fi

echo "removing $container: held by ${owner:-no compose project}, not $project"
docker rm -f "$container" >/dev/null
