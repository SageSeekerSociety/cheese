#!/usr/bin/env bash
# Decide which Docker contexts changed since the nearest commit whose images
# are all in the registry. Output is compatible with GITHUB_OUTPUT. An
# explicit BASE_SHA skips the registry lookup, including an empty value for
# the first build.
set -euo pipefail

output_file="${GITHUB_OUTPUT:-/dev/stdout}"
base_sha="${BASE_SHA:-}"
current_sha="${CURRENT_SHA:-HEAD}"
event_name="${EVENT_NAME:-push}"
ref_type="${REF_TYPE:-branch}"
search_limit="${BASELINE_SEARCH_LIMIT:-200}"
retry_wait="${REGISTRY_RETRY_WAIT_SECONDS:-5}"

# Every image this workflow publishes, by the name of its registry path. Its
# build job is `build-<name>`, and its output flag is the name with `_`.
images=(backend sandbox frontend office-render browser-render gateway
  metering-proxy private-executor collab)

# The tag the image built from <sha> is published under: build.yml tags
# metering-proxy with the full sha and every other image with the first seven
# characters (deploy/image-tag.sh).
image_tag() {
  if [[ "$1" == metering-proxy ]]; then
    printf '%s\n' "$2"
  else
    printf '%s\n' "${2:0:7}"
  fi
}

# Whether the build workflow at a commit has a job for an image. A baseline
# whose workflow never built an image has no manifest of it to promote. No
# `grep -q`: it exits at the first match, and under pipefail the SIGPIPE that
# gives `git show` would read as "not built".
builds_image() {
  git show "$2:.github/workflows/build.yml" 2>/dev/null \
    | grep "^  build-$1:" >/dev/null
}

# 0 when the registry holds the image's tag for a commit, 1 when it answers
# that the tag does not exist, 2 when it does not answer either way.
registry_has() {
  local ref err attempt
  ref="$IMAGE_ROOT/$1:$(image_tag "$1" "$2")"
  for attempt in 1 2 3; do
    if err="$(docker buildx imagetools inspect "$ref" 2>&1 >/dev/null)"; then
      return 0
    fi
    [[ "$err" != *": not found"* ]] || return 1
    sleep $((attempt * retry_wait))
  done
  echo "::error::Could not read $ref from the registry: $err" >&2
  return 2
}

# The baseline is the nearest earlier commit on this branch whose images are
# all in the registry, since promotion copies exactly those tags forward. The
# registry is asked rather than the list of workflow runs: that list has
# answered from snapshots weeks old, and every image then rebuilt against a
# baseline from weeks before. Any build that published a full set counts, a
# manual one included, and a commit whose build is still running or failed
# part way is passed over.
if [[ -z "${BASE_SHA+x}" && "$event_name" == push && "$ref_type" == branch ]]; then
  : "${IMAGE_ROOT:?IMAGE_ROOT must name the registry path the images are published under}"
  candidates=""
  if git rev-parse -q --verify "${current_sha}^" >/dev/null; then
    candidates="$(git rev-list --first-parent --max-count="$search_limit" "${current_sha}^")"
  fi
  examined=0
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    examined=$((examined + 1))
    complete=true
    for image in "${images[@]}"; do
      builds_image "$image" "$candidate" || continue
      status=0
      registry_has "$image" "$candidate" || status=$?
      if [[ "$status" -eq 1 ]]; then
        complete=false
        break
      elif [[ "$status" -ne 0 ]]; then
        exit 1
      fi
    done
    if [[ "$complete" == true ]]; then
      base_sha="$candidate"
      break
    fi
  done <<< "$candidates"
  # Only reaching the first commit establishes that nothing was ever built.
  # Running out of the search leaves the baseline unknown.
  if [[ -z "$base_sha" && "$examined" -ge "$search_limit" ]]; then
    echo "::error::None of the last $search_limit commits has a complete image set; run this workflow manually to build one" >&2
    exit 1
  fi
fi

backend=false
sandbox=false
frontend=false
office_render=false
browser_render=false
gateway=false
metering_proxy=false
private_executor=false
collab=false

# Tags and manual runs are explicit release/rebuild requests. A repository with
# no earlier complete image set also needs a complete bootstrap.
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
    why="no earlier commit has a complete image set"
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
  metering_proxy=true
  private_executor=true
  collab=true
else
  while IFS= read -r -d '' changed_path; do
    case "$changed_path" in
      deploy/metering-proxy/*)
        metering_proxy=true
        ;;
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

    # The docs site ships inside the frontend image, built in CI from these
    # sources (build.yml). Its CLI and settings references are generated from
    # these two files, so they are docs sources too. (The CI reference, from
    # the workflows, catches up with the next frontend build rather than make
    # every workflow edit rebuild an image.)
    case "$changed_path" in
      docs/manual/* | docs/site/* | backend/sandbox/cheese \
        | backend/app/core/config.py)
        frontend=true
        ;;
    esac

    # The backend image carries the sentence catalogs it renders push and
    # desktop notices from (backend/Dockerfile, the `i18n` context).
    case "$changed_path" in
      frontend/src/i18n/messages/*/roomNotice.json \
        | frontend/src/i18n/messages/*/apiError.json \
        | frontend/src/i18n/messages/*/global.json)
        backend=true
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

    # The collaboration service is built from the frontend package: its own
    # sources, the document schema it shares with the editors, and the
    # dependencies both are installed from.
    case "$changed_path" in
      frontend/collab/* | frontend/src/lib/docSchema/* \
        | frontend/package.json | frontend/pnpm-lock.yaml)
        collab=true
        ;;
    esac

    # The private-chat executor is built from the repository root and copies
    # these files in, so any of them changing is a change to that image.
    case "$changed_path" in
      backend/sandbox/Dockerfile.private \
        | backend/sandbox/cheese \
        | backend/app/domain/agent/harness/claude_code/remote_execution/runtime.py \
        | backend/app/domain/agent/harness/claude_code/remote_execution/mcp_process.py \
        | backend/app/domain/agent/harness/claude_code/remote_execution/private.py \
        | backend/app/domain/agent/harness/claude_code/remote_execution/private_egress.py \
        | backend/app/domain/fetch/addresses.py)
        private_executor=true
        ;;
    esac
  done < <(git diff --name-only -z "$base_sha" "$current_sha" --)
  for image in "${images[@]}"; do
    builds_image "$image" "$base_sha" || printf -v "${image//-/_}" true
  done
fi

# The decision, where a person can read it. It has only ever gone to
# `$GITHUB_OUTPUT`, which the workflow consumes and no log shows — so an
# image that rebuilt when nothing it owns had changed left nothing behind
# to explain itself.
echo "planned: backend=$backend sandbox=$sandbox frontend=$frontend" \
  "office_render=$office_render browser_render=$browser_render" \
  "gateway=$gateway metering_proxy=$metering_proxy" \
  "private_executor=$private_executor" "collab=$collab" \
  "base=${base_sha:-none}" >&2

{
  echo "backend=$backend"
  echo "sandbox=$sandbox"
  echo "frontend=$frontend"
  echo "office_render=$office_render"
  echo "browser_render=$browser_render"
  echo "gateway=$gateway"
  echo "metering_proxy=$metering_proxy"
  echo "private_executor=$private_executor"
  echo "collab=$collab"
  echo "base_sha=$base_sha"
  echo "base_tag=${base_sha:0:7}"
  echo "current_tag=$("$(dirname "$0")/../../deploy/image-tag.sh" "$current_sha")"
} >> "$output_file"
