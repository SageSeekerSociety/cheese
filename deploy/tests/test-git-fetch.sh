#!/usr/bin/env bash
# deploy/git-fetch.sh against a real repository served over file://, with a
# git in front of the real one that plays GitHub's part: it resets the first
# GIT_FAKE_RESETS fetches the way the dev box's link does, and, when
# GIT_FAKE_REQUIRE_TOKEN is set, refuses a fetch whose credentials are not that
# token, as GitHub does for a private repository.
#
# Usage: test-git-fetch.sh [case|all]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${1:-all}"
REAL_GIT="$(command -v git)"
mkdir -p "$ROOT/.tmp"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# A served repository with three commits on main and a branch, a fake git on
# PATH, and an empty workspace. Prints the run directory.
new_run() {
  local run_dir
  run_dir="$(mktemp -d "$ROOT/.tmp/git-fetch.XXXXXX")"
  mkdir -p "$run_dir/bin" "$run_dir/srv/owner" "$run_dir/src" "$run_dir/ws"
  (
    cd "$run_dir/src"
    "$REAL_GIT" init -q -b main .
    "$REAL_GIT" config user.email t@example.com
    "$REAL_GIT" config user.name t
    for n in 1 2 3; do
      echo "$n" > release.txt
      "$REAL_GIT" add release.txt
      "$REAL_GIT" commit -q -m "commit $n"
    done
    "$REAL_GIT" branch rollback HEAD~1
    "$REAL_GIT" clone -q --bare . "$run_dir/srv/owner/repo"
  )
  cat > "$run_dir/bin/git" <<'EOF'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$GIT_FAKE_DIR/argv.log"
# The -c settings before the subcommand, to ask git for credentials with.
config=()
args=("$@")
i=0
while [ "$i" -lt "${#args[@]}" ] && [ "${args[$i]}" = -c ]; do
  config+=(-c "${args[$((i + 1))]}")
  i=$((i + 2))
done
if [ "${args[$i]:-}" = fetch ]; then
  n=$(( $(cat "$GIT_FAKE_DIR/fetches" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$GIT_FAKE_DIR/fetches"
  if [ "$n" -le "${GIT_FAKE_RESETS:-0}" ]; then
    echo "fatal: unable to access 'https://github.com/owner/repo/': Recv failure: Connection reset by peer" >&2
    exit 128
  fi
  if [ -n "${GIT_FAKE_REQUIRE_TOKEN:-}" ]; then
    password="$(printf 'protocol=https\nhost=github.com\n\n' \
      | "$GIT_FAKE_REAL" "${config[@]}" credential fill 2>/dev/null | sed -n 's/^password=//p')"
    if [ "$password" != "$GIT_FAKE_REQUIRE_TOKEN" ]; then
      echo "remote: Invalid username or token. Password authentication is not supported for Git operations." >&2
      echo "fatal: Authentication failed for 'https://github.com/owner/repo/'" >&2
      exit 128
    fi
  fi
fi
exec "$GIT_FAKE_REAL" "$@"
EOF
  chmod +x "$run_dir/bin/git"
  printf '%s\n' "$run_dir"
}

# run_fetch <run_dir> <ref> <depth> [env…]: git-fetch.sh in the workspace,
# output to <run_dir>/out; its exit status is returned.
run_fetch() {
  local run_dir="$1" ref="$2" depth="$3"
  shift 3
  (
    cd "$run_dir/ws"
    env PATH="$run_dir/bin:$PATH" GIT_FAKE_DIR="$run_dir" GIT_FAKE_REAL="$REAL_GIT" \
      GITHUB_SERVER_URL="file://$run_dir/srv" GITHUB_REPOSITORY=owner/repo \
      GIT_FETCH_TOKEN="${GIT_FETCH_TOKEN:-ghs_fake_fetch_token_7c2e}" \
      DEPLOY_PULL_BACKOFF_CAP_SECONDS=1 "$@" \
      bash "$ROOT/deploy/git-fetch.sh" "$ref" "$depth"
  ) > "$run_dir/out" 2>&1
}

fetches() { cat "$1/fetches" 2>/dev/null || echo 0; }
served() { "$REAL_GIT" -C "$1/srv/owner/repo" rev-parse "$2"; }

test_burst_of_resets_is_waited_out() {
  local run_dir want
  run_dir="$(new_run)"
  want="$(served "$run_dir" main)"
  # Five resets in a row is more than actions/checkout's three attempts.
  run_fetch "$run_dir" "$want" 2 GIT_FAKE_RESETS=5 \
    || { cat "$run_dir/out" >&2; fail "gave up on a fetch that would have succeeded within the budget"; }
  [ "$(fetches "$run_dir")" = 6 ] || fail "expected 6 fetch attempts, saw $(fetches "$run_dir")"
  [ "$("$REAL_GIT" -C "$run_dir/ws" rev-parse HEAD)" = "$want" ] || fail "the workspace is not at the requested commit"
  [ "$(cat "$run_dir/ws/release.txt")" = 3 ] || fail "the workspace's files are not the requested commit's"
  grep -q 'git fetch of .*: succeeded on attempt 6' "$run_dir/out" || fail "the attempt that worked was not reported"
  rm -rf "$run_dir"
  echo "PASS: a burst of reset connections is retried within the budget"
}

test_rejected_token_is_not_retried() {
  local run_dir
  run_dir="$(new_run)"
  # Short budget: were the refusal retried, it would still be retried several
  # times inside it.
  if run_fetch "$run_dir" main 1 GIT_FAKE_REQUIRE_TOKEN=ghs_the_right_one \
      DEPLOY_PULL_BUDGET_SECONDS=10; then
    fail "the fetch succeeded although GitHub refused its token"
  fi
  [ "$(fetches "$run_dir")" = 1 ] || fail "a refused token was tried $(fetches "$run_dir") times, expected once"
  grep -q 'refused by GitHub' "$run_dir/out" || fail "the failure did not say GitHub refused it"
  rm -rf "$run_dir"
  echo "PASS: a token GitHub rejects fails at once"
}

test_missing_commit_is_not_retried() {
  local run_dir
  run_dir="$(new_run)"
  if run_fetch "$run_dir" 0123456789abcdef0123456789abcdef01234567 2 DEPLOY_PULL_BUDGET_SECONDS=10; then
    fail "the fetch of a commit the repository does not have succeeded"
  fi
  [ "$(fetches "$run_dir")" = 1 ] || fail "a missing commit was fetched $(fetches "$run_dir") times, expected once"
  grep -q 'refused by GitHub' "$run_dir/out" || fail "the failure did not say GitHub refused it"
  if run_fetch "$run_dir" no-such-branch 2 DEPLOY_PULL_BUDGET_SECONDS=10; then
    fail "the fetch of a branch the repository does not have succeeded"
  fi
  [ "$(fetches "$run_dir")" = 2 ] || fail "a missing branch was fetched $(( $(fetches "$run_dir") - 1 )) times, expected once"
  rm -rf "$run_dir"
  echo "PASS: a commit or branch GitHub does not have fails at once"
}

test_token_is_used_but_never_shown() {
  local run_dir token=ghs_fake_private_token_91ab encoded
  run_dir="$(new_run)"
  encoded="$(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')"
  GIT_FETCH_TOKEN="$token" run_fetch "$run_dir" main 1 GIT_FAKE_REQUIRE_TOKEN="$token" \
    || { cat "$run_dir/out" >&2; fail "a fetch that GitHub asked to authenticate did not offer the token"; }
  for where in "$run_dir/out" "$run_dir/argv.log" "$run_dir/ws/.git/config"; do
    if grep -qF -e "$token" -e "$encoded" "$where"; then
      fail "the token appeared in $(basename "$where")"
    fi
  done
  rm -rf "$run_dir"
  echo "PASS: the token reaches GitHub through a credential helper, never a command line, the log or the config"
}

test_used_workspace_ends_at_the_requested_commit() {
  local run_dir want
  run_dir="$(new_run)"
  want="$(served "$run_dir" rollback)"
  # The workspace a previous deploy left: main checked out, then files the
  # deploy wrote, a file git ignores, and an index lock from a killed job.
  run_fetch "$run_dir" main 2 || { cat "$run_dir/out" >&2; fail "the first fetch failed"; }
  echo junk > "$run_dir/ws/left-behind.txt"
  echo 'ignored.txt' > "$run_dir/ws/.git/info/exclude"
  echo junk > "$run_dir/ws/ignored.txt"
  echo 4 > "$run_dir/ws/release.txt"
  : > "$run_dir/ws/.git/index.lock"
  run_fetch "$run_dir" rollback 2 || { cat "$run_dir/out" >&2; fail "the fetch into a used workspace failed"; }
  [ "$("$REAL_GIT" -C "$run_dir/ws" rev-parse HEAD)" = "$want" ] || fail "the workspace is not at the requested branch's commit"
  [ "$(cat "$run_dir/ws/release.txt")" = 2 ] || fail "a modified tracked file survived the checkout"
  [ ! -e "$run_dir/ws/left-behind.txt" ] && [ ! -e "$run_dir/ws/ignored.txt" ] \
    || fail "files the previous run left are still there"
  "$REAL_GIT" -C "$run_dir/ws" rev-parse -q --verify 'HEAD^' >/dev/null || fail "depth 2 did not bring the parent commit"
  rm -rf "$run_dir"
  echo "PASS: a used workspace ends at exactly the requested commit, with its parent"
}

case "$CASE" in
  burst) test_burst_of_resets_is_waited_out ;;
  rejected-token) test_rejected_token_is_not_retried ;;
  missing-commit) test_missing_commit_is_not_retried ;;
  token) test_token_is_used_but_never_shown ;;
  used-workspace) test_used_workspace_ends_at_the_requested_commit ;;
  all)
    test_burst_of_resets_is_waited_out
    test_rejected_token_is_not_retried
    test_missing_commit_is_not_retried
    test_token_is_used_but_never_shown
    test_used_workspace_ends_at_the_requested_commit
    ;;
  *) fail "unknown case: $CASE" ;;
esac
