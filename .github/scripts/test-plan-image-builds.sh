#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
planner="$script_dir/plan-image-builds.sh"
test_repo="$(mktemp -d "${TMPDIR:-/tmp}/image-build-plan.XXXXXX")"
trap 'rm -rf "$test_repo"' EXIT

git -C "$test_repo" init -q
git -C "$test_repo" config user.email test@example.com
git -C "$test_repo" config user.name test
executor_dir=backend/app/domain/agent/harness/claude_code/remote_execution
mkdir -p "$test_repo/backend/app" "$test_repo/backend/sandbox/skills/cheese" \
  "$test_repo/$executor_dir" "$test_repo/backend/app/domain/fetch" \
  "$test_repo/frontend/src" "$test_repo/cli" "$test_repo/docs" \
  "$test_repo/deploy/office-render" \
  "$test_repo/deploy/browser-render" "$test_repo/deploy/gateway"
touch "$test_repo/backend/app/main.py" "$test_repo/backend/sandbox/cheese" \
  "$test_repo/backend/sandbox/skills/cheese/SKILL.md" \
  "$test_repo/frontend/src/main.ts" "$test_repo/cli/main.go" "$test_repo/docs/readme.md" \
  "$test_repo/deploy/office-render/server.py" \
  "$test_repo/deploy/browser-render/server.py" "$test_repo/deploy/gateway/Dockerfile" \
  "$test_repo/backend/sandbox/Dockerfile.private" \
  "$test_repo/$executor_dir/runtime.py" "$test_repo/$executor_dir/private.py" \
  "$test_repo/$executor_dir/mcp_process.py" "$test_repo/$executor_dir/private_egress.py" \
  "$test_repo/backend/app/domain/fetch/addresses.py"
# The legacy workflow builds five images; the two added later have no job yet.
mkdir -p "$test_repo/.github/workflows"
add_build_jobs() {
  local image
  for image in "$@"; do
    printf '  build-%s:\n    runs-on: ubuntu-24.04\n' "$image" \
      >> "$test_repo/.github/workflows/build.yml"
  done
}
printf 'jobs:\n' > "$test_repo/.github/workflows/build.yml"
add_build_jobs backend frontend office-render browser-render gateway
git -C "$test_repo" add .
git -C "$test_repo" commit -qm base
legacy_sha="$(git -C "$test_repo" rev-parse HEAD)"
mkdir -p "$test_repo/deploy/metering-proxy"
touch "$test_repo/deploy/metering-proxy/Dockerfile"
add_build_jobs metering-proxy private-executor
git -C "$test_repo" add .
git -C "$test_repo" commit -qm 'add metering image'
base_sha="$(git -C "$test_repo" rev-parse HEAD)"

assert_plan() {
  local expected="$1"
  local base="$2"
  local event_name="${3:-push}"
  local ref_type="${4:-branch}"
  local output
  output="$(
    cd "$test_repo"
    export BASE_SHA="$base"
    if [[ "$base" == lookup ]]; then
      unset BASE_SHA
    fi
    CURRENT_SHA=HEAD EVENT_NAME="$event_name" REF_TYPE="$ref_type" \
      GITHUB_OUTPUT=/dev/stdout bash "$planner"
  )"
  local actual
  actual="$(printf '%s\n' "$output" | grep -E '^(backend|frontend|office_render|browser_render|gateway|metering_proxy|private_executor)=' | paste -sd, -)"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: expected $expected, got $actual" >&2
    exit 1
  fi
}

commit_path() {
  local path="$1"
  printf 'change\n' >> "$test_repo/$path"
  git -C "$test_repo" add "$path"
  git -C "$test_repo" commit -qm "change $path"
}

assert_plan 'backend=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' ''

# Even a no-diff baseline without the image cannot supply a promoted manifest.
git -C "$test_repo" switch -q --detach "$legacy_sha"
assert_plan 'backend=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=true,private_executor=true' "$legacy_sha"
git -C "$test_repo" switch -q --detach "$base_sha"

# The first image must build even when a previously successful baseline has no image.
assert_plan 'backend=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=true,private_executor=true' "$legacy_sha"
commit_path deploy/metering-proxy/Dockerfile
assert_plan 'backend=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=true,private_executor=false' "$base_sha"
git -C "$test_repo" switch -q --detach "$base_sha"

commit_path frontend/src/main.ts
assert_plan 'backend=false,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
assert_plan 'backend=true,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

# The backend renders notices from the frontend's sentence catalogs, and only
# those: another namespace is the frontend's alone.
for catalog in roomNotice apiError global; do
  git -C "$test_repo" switch -q --detach "$base_sha"
  mkdir -p "$test_repo/frontend/src/i18n/messages/en"
  commit_path "frontend/src/i18n/messages/en/$catalog.json"
  assert_plan 'backend=true,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"
done
git -C "$test_repo" switch -q --detach "$base_sha"
mkdir -p "$test_repo/frontend/src/i18n/messages/en"
commit_path frontend/src/i18n/messages/en/topic.json
assert_plan 'backend=false,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/cheese
assert_plan 'backend=true,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=true' "$base_sha"

# The private executor rebuilds from its recipe and every file it copies in.
git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/Dockerfile.private
assert_plan 'backend=true,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=true' "$base_sha"

for executor_file in "$executor_dir/runtime.py" "$executor_dir/private.py" \
  "$executor_dir/mcp_process.py" "$executor_dir/private_egress.py" \
  backend/app/domain/fetch/addresses.py; do
  git -C "$test_repo" switch -q --detach "$base_sha"
  commit_path "$executor_file"
  assert_plan 'backend=true,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=true' "$base_sha"
done

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/skills/cheese/SKILL.md
assert_plan 'backend=true,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path cli/main.go
assert_plan 'backend=true,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/browser-render/server.py
assert_plan 'backend=false,frontend=false,office_render=false,browser_render=true,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/office-render/server.py
assert_plan 'backend=false,frontend=false,office_render=true,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path docs/readme.md
assert_plan 'backend=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

# The docs site is built into the frontend image, so its sources rebuild it.
git -C "$test_repo" switch -q --detach "$base_sha"
mkdir -p "$test_repo/docs/manual"
commit_path docs/manual/quickstart.md
assert_plan 'backend=false,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"
assert_plan 'backend=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' "$base_sha" workflow_dispatch branch
assert_plan 'backend=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' "$base_sha" push tag

# Diff from the last successful build, not merely HEAD^, catches component
# changes whose preceding build failed.
git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
commit_path frontend/src/main.ts
assert_plan 'backend=true,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/gateway/Dockerfile
assert_plan 'backend=false,frontend=false,office_render=false,browser_render=false,gateway=true,metering_proxy=false,private_executor=false' "$base_sha"

# The collaboration service is built from the frontend package, from its own
# sources, the document schema it shares with the editors, and the lockfile.
collab_plan() {
  (
    cd "$test_repo"
    BASE_SHA="$1" CURRENT_SHA=HEAD EVENT_NAME=push REF_TYPE=branch \
      GITHUB_OUTPUT=/dev/stdout bash "$planner" 2>/dev/null
  ) | sed -n 's/^collab=//p'
}
git -C "$test_repo" switch -q --detach "$base_sha"
mkdir -p "$test_repo/frontend/collab" "$test_repo/frontend/src/lib/docSchema"
touch "$test_repo/frontend/collab/service.ts" "$test_repo/frontend/src/lib/docSchema/index.ts" \
  "$test_repo/frontend/pnpm-lock.yaml"
add_build_jobs collab
git -C "$test_repo" add .
git -C "$test_repo" commit -qm 'add collab image'
collab_base="$(git -C "$test_repo" rev-parse HEAD)"
for collab_file in frontend/collab/service.ts frontend/src/lib/docSchema/index.ts \
  frontend/pnpm-lock.yaml; do
  git -C "$test_repo" switch -q --detach "$collab_base"
  commit_path "$collab_file"
  [[ "$(collab_plan "$collab_base")" == true ]] \
    || { echo "FAIL: $collab_file must rebuild collab" >&2; exit 1; }
done
git -C "$test_repo" switch -q --detach "$collab_base"
commit_path frontend/src/main.ts
[[ "$(collab_plan "$collab_base")" == false ]] \
  || { echo "FAIL: an app-only change must not rebuild collab" >&2; exit 1; }

# Every image is published under the first seven characters of its commit
# (docker/metadata-action `type=sha`), and promotion and deploys must name it the
# same way. The commit below is fixed to 7a5fcd85…, and the blob
# "ambiguous 245468762\n" hashes to 7a5fcd80…, so git itself abbreviates the
# commit to eight characters here.
tag_repo="$test_repo/ambiguous-prefix"
git init -q "$tag_repo"
git -C "$tag_repo" config user.email test@example.com
git -C "$tag_repo" config user.name test
git -C "$tag_repo" config commit.gpgsign false
mkdir -p "$tag_repo/backend"
printf 'base\n' > "$tag_repo/backend/main.py"
git -C "$tag_repo" add .
GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z \
  git -C "$tag_repo" commit -qm base
tag_base="$(git -C "$tag_repo" rev-parse HEAD)"
printf 'ambiguous 245468762\n' | git -C "$tag_repo" hash-object -w --stdin >/dev/null
if [[ "$(git -C "$tag_repo" rev-parse --short=7 HEAD)" == "${tag_base:0:7}" ]]; then
  echo "FAIL: fixture no longer has an ambiguous prefix" >&2
  exit 1
fi

plan_value() {
  local key="$1" base="$2"
  (
    cd "$tag_repo"
    BASE_SHA="$base" CURRENT_SHA=HEAD EVENT_NAME=push REF_TYPE=branch \
      GITHUB_OUTPUT=/dev/stdout bash "$planner" 2>/dev/null
  ) | sed -n "s/^$key=//p"
}

published="${tag_base:0:7}"
[[ "$(plan_value current_tag '')" == "$published" ]] \
  || { echo "FAIL: current tag is not the published $published" >&2; exit 1; }
[[ "$(cd "$tag_repo" && bash "$script_dir/../../deploy/image-tag.sh" HEAD)" == "$published" ]] \
  || { echo "FAIL: deploy tag is not the published $published" >&2; exit 1; }
printf 'change\n' >> "$tag_repo/backend/main.py"
git -C "$tag_repo" commit -qam change
[[ "$(plan_value base_tag "$tag_base")" == "$published" ]] \
  || { echo "FAIL: promotion does not start from the published $published" >&2; exit 1; }

# The baseline lookup asks the registry. This stands in for it: a reference
# exists when it is listed in $REGISTRY_TEST_TAGS, and REGISTRY_TEST_DOWN
# makes every request fail the way an unauthorised or unreachable registry
# does.
docker() {
  if [[ "$*" != "buildx imagetools inspect "* ]]; then
    echo "FAIL: unexpected docker call: $*" >&2
    return 1
  fi
  local ref="$4"
  if [[ -n "${REGISTRY_TEST_DOWN:-}" ]]; then
    echo "ERROR: unexpected status from HEAD request to https://$ref: 403 Forbidden" >&2
    return 1
  fi
  if grep -qxF "$ref" "$REGISTRY_TEST_TAGS"; then
    echo "Name: $ref"
    return 0
  fi
  echo "ERROR: $ref: not found" >&2
  return 1
}
export -f docker
export IMAGE_ROOT=registry.test/project REGISTRY_RETRY_WAIT_SECONDS=0
export REGISTRY_TEST_TAGS="$test_repo/registry-tags"

# Publish a commit's images the way build.yml tags them.
publish() {
  local sha="$1" image
  shift
  for image in "$@"; do
    if [[ "$image" == metering-proxy ]]; then
      echo "$IMAGE_ROOT/$image:$sha"
    else
      echo "$IMAGE_ROOT/$image:${sha:0:7}"
    fi >> "$REGISTRY_TEST_TAGS"
  done
}
all_images=(backend frontend office-render browser-render gateway
  metering-proxy private-executor)

lookup_plan() {
  (
    cd "$test_repo"
    unset BASE_SHA
    CURRENT_SHA=HEAD EVENT_NAME=push REF_TYPE=branch \
      GITHUB_OUTPUT=/dev/stdout bash "$planner" 2>/dev/null
  )
}

assert_lookup() {
  local expected_plan="$1" expected_base="$2" output actual
  output="$(lookup_plan)"
  actual="$(printf '%s\n' "$output" | grep -E '^(backend|frontend|office_render|browser_render|gateway|metering_proxy|private_executor)=' | paste -sd, -)"
  [[ "$actual" == "$expected_plan" ]] \
    || { echo "FAIL: expected $expected_plan, got $actual" >&2; exit 1; }
  [[ "$(printf '%s\n' "$output" | sed -n 's/^base_sha=//p')" == "$expected_base" ]] \
    || { echo "FAIL: expected baseline ${expected_base:-none}" >&2; exit 1; }
}

assert_lookup_fails() {
  if (
    cd "$test_repo"
    unset BASE_SHA
    CURRENT_SHA=HEAD EVENT_NAME=push REF_TYPE=branch \
      GITHUB_OUTPUT="$test_repo/failed-output" bash "$planner" 2>/dev/null
  ); then
    echo "FAIL: $1 must fail planning" >&2
    exit 1
  fi
  [[ ! -s "$test_repo/failed-output" ]] \
    || { echo "FAIL: $1 published a build plan" >&2; exit 1; }
}

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
backend_sha="$(git -C "$test_repo" rev-parse HEAD)"
commit_path frontend/src/main.ts
frontend_sha="$(git -C "$test_repo" rev-parse HEAD)"
commit_path docs/readme.md

# The nearest commit with every image is the baseline. A later one whose build
# published only part of its images, as a running or failed build leaves it,
# is passed over.
: > "$REGISTRY_TEST_TAGS"
publish "$backend_sha" "${all_images[@]}"
publish "$frontend_sha" backend office-render browser-render gateway \
  metering-proxy private-executor
assert_lookup 'backend=false,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$backend_sha"

# Once that build finishes, it is the baseline.
publish "$frontend_sha" frontend
assert_lookup 'backend=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$frontend_sha"

# A baseline whose workflow never built an image is complete without it, and
# that image is built because there is nothing to promote.
: > "$REGISTRY_TEST_TAGS"
publish "$legacy_sha" backend frontend office-render browser-render gateway
assert_lookup 'backend=true,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=true,private_executor=true' "$legacy_sha"

# Reaching the first commit without a complete set is a bootstrap.
: > "$REGISTRY_TEST_TAGS"
assert_lookup 'backend=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' ''

# A search that stops short of a complete set leaves the baseline unknown.
publish "$base_sha" "${all_images[@]}"
BASELINE_SEARCH_LIMIT=2 assert_lookup_fails 'an exhausted search'

# The registry not answering is not the tag being absent.
REGISTRY_TEST_DOWN=1 assert_lookup_fails 'an unreachable registry'

# Explicit release requests remain usable while the registry is unavailable.
export REGISTRY_TEST_DOWN=1
assert_plan 'backend=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' lookup workflow_dispatch branch
assert_plan 'backend=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' lookup push tag
unset REGISTRY_TEST_DOWN

echo 'PASS: image build planning contracts'
