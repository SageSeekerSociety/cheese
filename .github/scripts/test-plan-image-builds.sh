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
  "$test_repo/deploy/office-render"
touch "$test_repo/backend/app/main.py" "$test_repo/backend/sandbox/cheese" \
  "$test_repo/backend/sandbox/skills/cheese/SKILL.md" \
  "$test_repo/frontend/src/main.ts" "$test_repo/cli/main.go" "$test_repo/docs/readme.md" \
  "$test_repo/deploy/office-render/server.py"
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
    BASE_SHA="$base" CURRENT_SHA=HEAD EVENT_NAME="$event_name" REF_TYPE="$ref_type" \
      GITHUB_OUTPUT=/dev/stdout bash "$planner"
  )"
  local actual
  actual="$(printf '%s\n' "$output" | grep -E '^(backend|sandbox|frontend|office_render)=' | paste -sd, -)"
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

assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true' ''

commit_path frontend/src/main.ts
assert_plan 'backend=false,sandbox=false,frontend=true,office_render=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/cheese
assert_plan 'backend=true,sandbox=true,frontend=false,office_render=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/sandbox/skills/cheese/SKILL.md
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path cli/main.go
assert_plan 'backend=true,sandbox=false,frontend=false,office_render=false' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path deploy/office-render/server.py
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=true' "$base_sha"

git -C "$test_repo" switch -q --detach "$base_sha"
commit_path docs/readme.md
assert_plan 'backend=false,sandbox=false,frontend=false,office_render=false' "$base_sha"
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true' "$base_sha" workflow_dispatch branch
assert_plan 'backend=true,sandbox=true,frontend=true,office_render=true' "$base_sha" push tag

# Diff from the last successful build, not merely HEAD^, catches component
# changes whose preceding build failed.
git -C "$test_repo" switch -q --detach "$base_sha"
commit_path backend/app/main.py
commit_path frontend/src/main.ts
assert_plan 'backend=true,sandbox=false,frontend=true,office_render=false' "$base_sha"

echo 'PASS: image build planning contracts'
