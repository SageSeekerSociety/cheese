#!/usr/bin/env bash
set -euo pipefail

expected_tag="${1:?usage: check-app-tier.sh <expected-image-tag>}"
backend_count=0
frontend_count=0
backend_image=""
frontend_image=""
backend_state=""
frontend_state=""
backend_status=""
frontend_status=""
forgejo_count=0
forgejo_state=""
forgejo_status=""
collab_count=0
collab_image=""
collab_state=""
collab_status=""
events_count=0
events_image=""
events_state=""
events_status=""

# A box with an app-router runs the backend and the frontend each in one of two
# slots (`backend` or `backend-b`, see deploy-docker.sh); either is that service.
while IFS=$'\t' read -r service image state status; do
  case "$service" in
    backend|backend-b)
      backend_count=$((backend_count + 1))
      backend_image="$image"
      backend_state="$state"
      backend_status="$status"
      ;;
    frontend|frontend-b)
      frontend_count=$((frontend_count + 1))
      frontend_image="$image"
      frontend_state="$state"
      frontend_status="$status"
      ;;
    collab)
      collab_count=$((collab_count + 1))
      collab_image="$image"
      collab_state="$state"
      collab_status="$status"
      ;;
    forgejo)
      forgejo_count=$((forgejo_count + 1))
      forgejo_state="$state"
      forgejo_status="$status"
      ;;
    forge-events)
      events_count=$((events_count + 1))
      events_image="$image"
      events_state="$state"
      events_status="$status"
      ;;
  esac
done

failures=0
# collab and the forge event relay are named by digest when nothing in them
# changed since the running image (deploy-docker.sh, pin_by_content and
# pin_relay_by_code): their containers then keep the reference they were
# created from, which carries no release's tag. The deploy passes the
# reference it chose ($6, from COLLAB_IMAGE or FORGE_EVENTS_IMAGE); a check run
# outside a deploy (the drift check, scripts/whats-live.sh) reads the choice
# the last healthy deploy recorded in APP_TIER_PINS (deploy-docker.sh writes
# ~/ops/app-tier-pins). A digest reference must be exactly the one chosen; a
# tag must be this release's. Feed it `docker ps --no-trunc`: without it,
# Docker prints a digest reference as the image's short ID.
pins="${APP_TIER_PINS:-${HOME:-}/ops/app-tier-pins}"
pinned() {
  [ -r "$pins" ] || return 0
  sed -n "s/^$1=//p" "$pins" | tail -n 1
}
check_service() {
  service="$1"
  count="$2"
  image="$3"
  state="$4"
  status="$5"
  chosen="${6:-}"

  if [ "$count" -ne 1 ]; then
    echo "APP-TIER ERROR: expected exactly one $service container, found $count" >&2
    failures=$((failures + 1))
    return
  fi
  if [[ "$image" == *@sha256:* ]] && [ -z "$chosen" ]; then
    echo "APP-TIER ERROR: $service image '$image' is pinned by digest, but no deploy recorded choosing it" >&2
    failures=$((failures + 1))
  elif [ -n "$chosen" ] && [ "$image" != "$chosen" ] && [[ "$chosen" == *@sha256:* ]]; then
    echo "APP-TIER ERROR: $service image '$image' is not '$chosen', which this release chose" >&2
    failures=$((failures + 1))
  else
    case "$image" in
      "$chosen" | *:"$expected_tag") ;;
      *@sha256:*)
        if [ -n "$chosen" ]; then
          echo "APP-TIER ERROR: $service image '$image' is not '$chosen', which this release chose" >&2
          failures=$((failures + 1))
        fi
        ;;
      *)
        echo "APP-TIER ERROR: $service image '$image' is not tag '$expected_tag'" >&2
        failures=$((failures + 1))
        ;;
    esac
  fi
  if [ "$state" != "running" ]; then
    echo "APP-TIER ERROR: $service state is '$state', not running" >&2
    failures=$((failures + 1))
  fi
  case "$status" in
    *"(healthy)"*) ;;
    *)
      echo "APP-TIER ERROR: $service is not healthy ('$status')" >&2
      failures=$((failures + 1))
      ;;
  esac
}

check_service backend "$backend_count" "$backend_image" "$backend_state" "$backend_status"
check_service frontend "$frontend_count" "$frontend_image" "$frontend_state" "$frontend_status"
# The living documents are edited through it; without it no document opens.
# A release that predates it has none (deploy-docker.sh decides which).
if [ "${COLLAB_EXPECTED:-true}" = true ] || [ "$collab_count" -gt 0 ]; then
  check_service collab "$collab_count" "$collab_image" "$collab_state" "$collab_status" "${COLLAB_IMAGE:-$(pinned COLLAB_IMAGE)}"
fi
if [ "${FORGE_EVENTS_LOCAL:-false}" = true ] || [ "$events_count" -gt 0 ]; then
  check_service forge-events "$events_count" "$events_image" "$events_state" "$events_status" "${FORGE_EVENTS_IMAGE:-$(pinned FORGE_EVENTS_IMAGE)}"
fi
if [ "$forgejo_count" -ne 1 ] || [ "$forgejo_state" != running ] || [[ "$forgejo_status" != *"(healthy)"* ]]; then
  echo "APP-TIER ERROR: expected one running healthy forgejo container, found $forgejo_count ($forgejo_state, $forgejo_status)" >&2
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  exit 1
fi

echo "APP-TIER OK: backend and frontend (and collab when released) are healthy at tag $expected_tag; forgejo is healthy"
