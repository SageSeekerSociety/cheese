#!/usr/bin/env bash
set -euo pipefail

expected_tag="${1:?usage: check-app-tier.sh <expected-image-tag>}"
backend_count=0
frontend_count=0
# The repo server. Checked here rather than left to the deploy bringing it up,
# because the failure it guards against is silent: with git down the front
# nginx still starts and still answers every machine fetch and push - with a
# 502, or by hanging until the machine gives up. A deploy that went green
# while that was true is what put this here.
git_count=0
backend_image=""
frontend_image=""
git_image=""
backend_state=""
frontend_state=""
git_state=""
backend_status=""
frontend_status=""
git_status=""

while IFS=$'\t' read -r service image state status; do
  case "$service" in
    backend)
      backend_count=$((backend_count + 1))
      backend_image="$image"
      backend_state="$state"
      backend_status="$status"
      ;;
    frontend)
      frontend_count=$((frontend_count + 1))
      frontend_image="$image"
      frontend_state="$state"
      frontend_status="$status"
      ;;
    git)
      git_count=$((git_count + 1))
      git_image="$image"
      git_state="$state"
      git_status="$status"
      ;;
  esac
done

failures=0
check_service() {
  service="$1"
  count="$2"
  image="$3"
  state="$4"
  status="$5"

  if [ "$count" -ne 1 ]; then
    echo "APP-TIER ERROR: expected exactly one $service container, found $count" >&2
    failures=$((failures + 1))
    return
  fi
  case "$image" in
    *:"$expected_tag") ;;
    *)
      echo "APP-TIER ERROR: $service image '$image' is not tag '$expected_tag'" >&2
      failures=$((failures + 1))
      ;;
  esac
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
check_service git "$git_count" "$git_image" "$git_state" "$git_status"

if [ "$failures" -ne 0 ]; then
  exit 1
fi

echo "APP-TIER OK: backend, frontend and git are healthy at tag $expected_tag"
