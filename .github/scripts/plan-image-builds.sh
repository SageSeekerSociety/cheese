#!/usr/bin/env bash
# Decide which Docker contexts changed since the last fully successful image
# build. Output is compatible with GITHUB_OUTPUT. An explicit BASE_SHA skips
# the GitHub lookup, including an empty value for the first successful build.
set -euo pipefail

output_file="${GITHUB_OUTPUT:-/dev/stdout}"
base_sha="${BASE_SHA:-}"
current_sha="${CURRENT_SHA:-HEAD}"
event_name="${EVENT_NAME:-push}"
ref_type="${REF_TYPE:-branch}"

# A failed lookup leaves the baseline unknown. Stop before scheduling builds;
# only a successful empty response establishes that a bootstrap is needed.
if [[ -z "${BASE_SHA+x}" && "$event_name" == push && "$ref_type" == branch ]]; then
  if ! base_sha="$(gh api \
      "repos/${GITHUB_REPOSITORY}/actions/workflows/build.yml/runs?branch=${GITHUB_REF_NAME}&status=success&event=push&per_page=1" \
      --jq '.workflow_runs[0].head_sha // empty')"; then
    echo "::error::Could not query the previous successful image build; rerun this job when the API is available" >&2
    exit 1
  fi
fi

backend=false
sandbox=false
frontend=false
office_render=false
browser_render=false
gateway=false

# Tags and manual runs are explicit release/rebuild requests. A repository with
# no earlier successful build also needs a complete bootstrap.
if [[ "$event_name" != "push" || "$ref_type" == "tag" || -z "$base_sha" ]] \
  || ! git cat-file -e "${base_sha}^{commit}" 2>/dev/null; then
  # Which of the four it was. A full rebuild of every image is ~20 minutes on
  # a box with one runner slot, so it is worth a line saying why it happened —
  # a base commit the checkout cannot resolve needs to be distinguished from
  # a deliberate bootstrap.
  if [[ "$event_name" != "push" ]]; then
    why="event is $event_name, not a push"
  elif [[ "$ref_type" == "tag" ]]; then
    why="a tag is an explicit release build"
  elif [[ -z "$base_sha" ]]; then
    why="no previous successful build to compare against"
  else
    why="base commit $base_sha is not in this checkout"
  fi
  echo "rebuilding every image: $why" >&2
  backend=true
  sandbox=true
  frontend=true
  browser_render=true
  office_render=true
  gateway=true
else
  while IFS= read -r -d '' changed_path; do
    case "$changed_path" in
      deploy/gateway/*)
        gateway=true
        ;;
      backend/*)
        backend=true
        ;;
      cli/*)
        backend=true
        ;;
      frontend/*)
        frontend=true
        ;;
      deploy/office-render/*)
        office_render=true
        ;;
      deploy/browser-render/*)
        browser_render=true
        ;;
    esac

    # The production backend bakes backend/sandbox into /app/sandbox, while the
    # same directory is also the context for both runtime images.
    case "$changed_path" in
      backend/sandbox/skills/*)
        # Skills ship in the backend, which seeds them into agent workspaces.
        # The sandbox Dockerfile copies only `cheese`, not this directory.
        ;;
      backend/sandbox/*)
        sandbox=true
        ;;
    esac
  done < <(git diff --name-only -z "$base_sha" "$current_sha" --)
fi

# The decision, where a person can read it. It has only ever gone to
# `$GITHUB_OUTPUT`, which the workflow consumes and no log shows — so an
# image that rebuilt when nothing it owns had changed left nothing behind
# to explain itself.
echo "planned: backend=$backend sandbox=$sandbox frontend=$frontend" \
  "office_render=$office_render browser_render=$browser_render" \
  "gateway=$gateway" \
  "base=${base_sha:-none}" >&2

{
  echo "backend=$backend"
  echo "sandbox=$sandbox"
  echo "frontend=$frontend"
  echo "office_render=$office_render"
  echo "browser_render=$browser_render"
  echo "gateway=$gateway"
  echo "base_sha=$base_sha"
  echo "base_tag=${base_sha:0:7}"
  echo "current_tag=$(git rev-parse --short=7 "$current_sha")"
} >> "$output_file"
