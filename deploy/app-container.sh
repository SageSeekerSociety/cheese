#!/usr/bin/env bash
# Prints the name of the running container of one part of the app tier:
# `backend` or `frontend`.
#
# On a box that releases without downtime (ACTIVE_BACKEND_DIR), each runs in one
# of two compose services, `backend` and `backend-b`, and every release moves it
# to the other (deploy/deploy-docker.sh). So the container is cheese-backend-1
# after one release and cheese-backend-b-1 after the next; ask this instead of
# naming either. While a release runs both are up, and the newer one is printed.
# Exits 1 when neither is running.
set -euo pipefail

part="${1:?usage: app-container.sh backend|frontend}"
docker ps \
  --filter "label=com.docker.compose.project=${PROJECT:-cheese}" \
  --filter label=com.docker.compose.oneoff=False \
  --format '{{.Label "com.docker.compose.service"}} {{.Names}}' \
  | awk -v part="$part" '
      !found && ($1 == part || $1 == part "-b") { print $2; found = 1 }
      END { exit !found }'
