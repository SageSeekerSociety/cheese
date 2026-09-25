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
  "$test_repo/$executor_dir" \
  "$test_repo/frontend/src" "$test_repo/cli" "$test_repo/docs" \
  "$test_repo/deploy/office-render" \
  "$test_repo/deploy/browser-render" "$test_repo/deploy/gateway"
touch "$test_repo/backend/app/main.py" "$test_repo/backend/sandbox/cheese" \
  "$test_repo/backend/sandbox/skills/cheese/SKILL.md" \
  "$test_repo/frontend/src/main.ts" "$test_repo/cli/main.go" "$test_repo/docs/readme.md" \
  "$test_repo/deploy/office-render/server.py" \
  "$test_repo/deploy/browser-render/server.py" "$test_repo/deploy/gateway/Dockerfile" \
  "$test_repo/backend/sandbox/Dockerfile.private" \
  "$test_repo/$executor_dir/runtime.py" "$test_repo/$executor_dir/private.py"
git -C "$test_repo" add .
git -C "$test_repo" commit -qm base
legacy_sha="$(git -C "$test_repo" rev-parse HEAD)"
mkdir -p "$test_repo/deploy/metering-proxy"
touch "$test_repo/deploy/metering-proxy/Dockerfile"
mkdir -p "$test_repo/.github/workflows"
printf 'jobs:\n  build-private-executor:\n    runs-on: ubuntu-24.04\n' \
  > "$test_repo/.github/workflows/build.yml"
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
  actual="$(printf '%s\n' "$output" | grep -E '^(backend|sandbox|frontend|office_render|browser_render|gateway|metering_proxy|private_executor)=' | paste -sd, -)"
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

assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' ''

# Even a no-diff baseline without the image cannot supply a promoted manifest.
git -C "$test_repo" switch -q --detach "$legacy_sha"
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=true,private_executor=true' "$legacy_sha"
git -C "$test_repo" switch -q --detach "$base_sha"

# The first image must build even when a previously successful baseline has no image.
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=true,private_executor=true' "$legacy_sha"
commit_path deploy/metering-proxy/Dockerfile
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=true,private_executor=false' "$base_sha"
git -C "$test_repo" switch -q --detach "$base_sha"

commit_path frontend/src/main.ts
assert_plan 'backend=false,sandbox=false,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/cheese
assert_plan 'backend=true,sandbox=true,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=true' "$base_sha"

# The private executor rebuilds from its recipe and every file it copies in.
git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/Dockerfile.private
assert_plan 'backend=true,sandbox=true,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=true' "$base_sha"

for executor_file in runtime.py private.py; do
  git -C "$test_repo" switch -q --detach "$base_sha"
  commit_path "$executor_dir/$executor_file"
  assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=true' "$base_sha"
done

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/skills/cheese/SKILL.md
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path cli/main.go
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/browser-render/server.py
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=true,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/office-render/server.py
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=true,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path docs/readme.md
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' "$base_sha" workflow_dispatch branch
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' "$base_sha" push tag

# Diff from the last successful build, not merely HEAD^, catches component
# changes whose preceding build failed.
git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
commit_path frontend/src/main.ts
assert_plan 'backend=true,sandbox=false,frontend=true,office_render=false,browser_render=false,gateway=false,metering_proxy=false,private_executor=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/gateway/Dockerfile
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=true,metering_proxy=false,private_executor=false' "$base_sha"

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

# Exercise the same GitHub lookup used by the workflow without network calls.
gh() {
  local endpoint="$2" filter="$4" page="${2##*&page=}"
  if [[ "$1" != api || "$endpoint" != *'status=completed&per_page=100&page='* ]]; then
    echo 'FAIL: query completed runs without a server-side conclusion filter' >&2
    return 1
  fi
  if [[ "$endpoint" == *'event='* ]]; then
    echo 'FAIL: a manual rebuild must count as a baseline, so the lookup cannot filter by event' >&2
    return 1
  fi
  printf '%s\n' "$page" >> "$GH_TEST_CALLS"
  if [[ "$GH_TEST_STATUS" != 0 ]]; then
    printf '%s' "$GH_TEST_BASE"
    return "$GH_TEST_STATUS"
  fi
  case "$GH_TEST_MODE" in
    mixed)
      jq -n --arg sha "$GH_TEST_BASE" '{workflow_runs: [
        {conclusion: "cancelled", head_sha: $sha},
        {conclusion: "failure", head_sha: $sha},
        {conclusion: "success", head_sha: $sha},
        {conclusion: "success", head_sha: "0000000000000000000000000000000000000000"}]}' ;;
    empty) printf '{"workflow_runs":[]}' ;;
    failures) jq -n --arg sha "$GH_TEST_BASE" '{workflow_runs: [{conclusion:"failure",head_sha:$sha}]}' ;;
    pages|page_failure|limit)
      if [[ "$page" == 1 || "$GH_TEST_MODE" == limit ]]; then
        jq -n --arg sha "$GH_TEST_BASE" '{workflow_runs: [range(100) | {conclusion:"failure",head_sha:$sha}]}'
      elif [[ "$GH_TEST_MODE" == page_failure ]]; then
        return 1
      else
        jq -n --arg sha "$GH_TEST_BASE" '{workflow_runs: [{conclusion:"success",head_sha:$sha}]}'
      fi ;;
    malformed) printf '{bad json' ;;
    missing_runs) printf '{}' ;;
    missing_record) printf '{"workflow_runs":[{}]}' ;;
  esac | jq -r "$filter"
}
export -f gh
export GITHUB_REPOSITORY=example/project GITHUB_REF_NAME=main
export GH_TEST_BASE="$base_sha" GH_TEST_STATUS=0 GH_TEST_MODE=mixed
export GH_TEST_CALLS="$test_repo/query-pages"
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=true,metering_proxy=false,private_executor=false' lookup

export GH_TEST_MODE=pages
: > "$GH_TEST_CALLS"
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=true,metering_proxy=false,private_executor=false' lookup
[[ "$(paste -sd, "$GH_TEST_CALLS")" == 1,2 ]] || { echo 'FAIL: did not search the second page'; exit 1; }

export GH_TEST_MODE=empty
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' lookup
export GH_TEST_MODE=failures
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' lookup

# An API failure must never publish a plan, even if stdout contains a SHA.
export GH_TEST_STATUS=1
for GH_TEST_BASE in '' "$base_sha"; do
  export GH_TEST_BASE
  if (
    cd "$test_repo"
    unset BASE_SHA
    CURRENT_SHA=HEAD EVENT_NAME=push REF_TYPE=branch \
      GITHUB_OUTPUT="$test_repo/failed-output" bash "$planner"
  ); then
    echo 'FAIL: a failed GitHub lookup must fail planning' >&2
    exit 1
  fi
  if [[ -s "$test_repo/failed-output" ]]; then
    echo 'FAIL: a failed GitHub lookup published a build plan' >&2
    exit 1
  fi
done

# A later page failure, malformed response, and the search limit all leave
# the baseline unknown. None may publish a bootstrap plan.
export GH_TEST_STATUS=0 GH_TEST_BASE="$base_sha"
for GH_TEST_MODE in page_failure malformed missing_runs missing_record limit; do
  export GH_TEST_MODE
  if (
    cd "$test_repo"
    unset BASE_SHA
    CURRENT_SHA=HEAD EVENT_NAME=push REF_TYPE=branch \
      GITHUB_OUTPUT="$test_repo/failed-output" bash "$planner"
  ); then
    echo "FAIL: $GH_TEST_MODE must fail planning" >&2
    exit 1
  fi
  [[ ! -s "$test_repo/failed-output" ]] || { echo 'FAIL: unknown baseline published a plan'; exit 1; }
done

# Explicit release requests remain usable while the API is unavailable.
export GH_TEST_STATUS=1
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' lookup workflow_dispatch branch
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true,metering_proxy=true,private_executor=true' lookup push tag

echo 'PASS: image build planning contracts'
