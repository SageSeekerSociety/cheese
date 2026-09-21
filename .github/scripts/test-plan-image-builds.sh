#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
planner="$script_dir/plan-image-builds.sh"
test_repo="$(mktemp -d "${TMPDIR:-/tmp}/image-build-plan.XXXXXX")"
trap 'rm -rf "$test_repo"' EXIT

git -C "$test_repo" init -q
git -C "$test_repo" config user.email test@example.com
git -C "$test_repo" config user.name test
mkdir -p "$test_repo/backend/app" "$test_repo/backend/sandbox/skills/cheese" \
  "$test_repo/frontend/src" "$test_repo/cli" "$test_repo/docs" \
  "$test_repo/deploy/office-render" \
  "$test_repo/deploy/browser-render" "$test_repo/deploy/gateway"
touch "$test_repo/backend/app/main.py" "$test_repo/backend/sandbox/cheese" \
  "$test_repo/backend/sandbox/skills/cheese/SKILL.md" \
  "$test_repo/frontend/src/main.ts" "$test_repo/cli/main.go" "$test_repo/docs/readme.md" \
  "$test_repo/deploy/office-render/server.py" \
  "$test_repo/deploy/browser-render/server.py" "$test_repo/deploy/gateway/Dockerfile"
git -C "$test_repo" add .
git -C "$test_repo" commit -qm base
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
  actual="$(printf '%s\n' "$output" | grep -E '^(backend|sandbox|frontend|office_render|browser_render|gateway)=' | paste -sd, -)"
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

assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true' ''

commit_path frontend/src/main.ts
assert_plan 'backend=false,sandbox=false,frontend=true,office_render=false,browser_render=false,gateway=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/cheese
assert_plan 'backend=true,sandbox=true,frontend=false,office_render=false,browser_render=false,gateway=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/skills/cheese/SKILL.md
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path cli/main.go
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/browser-render/server.py
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=true,gateway=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/office-render/server.py
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=true,browser_render=false,gateway=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path docs/readme.md
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=false' "$base_sha"
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true' "$base_sha" workflow_dispatch branch
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true' "$base_sha" push tag

# Diff from the last successful build, not merely HEAD^, catches component
# changes whose preceding build failed.
git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
commit_path frontend/src/main.ts
assert_plan 'backend=true,sandbox=false,frontend=true,office_render=false,browser_render=false,gateway=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/gateway/Dockerfile
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=true' "$base_sha"

# Exercise the same GitHub lookup used by the workflow without network calls.
gh() {
  local endpoint="$2" filter="$4" page="${2##*&page=}"
  if [[ "$1" != api || "$endpoint" != *'status=completed&event=push&per_page=100&page='* ]]; then
    echo 'FAIL: query completed runs without a server-side conclusion filter' >&2
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
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=true' lookup

export GH_TEST_MODE=pages
: > "$GH_TEST_CALLS"
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false,browser_render=false,gateway=true' lookup
[[ "$(paste -sd, "$GH_TEST_CALLS")" == 1,2 ]] || { echo 'FAIL: did not search the second page'; exit 1; }

export GH_TEST_MODE=empty
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true' lookup
export GH_TEST_MODE=failures
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true' lookup

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
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true' lookup workflow_dispatch branch
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true,browser_render=true,gateway=true' lookup push tag

echo 'PASS: image build planning contracts'
