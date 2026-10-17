#!/usr/bin/env bash
# check-remote-branch.sh — does this branch exist on the remote, and is the
# base this worktree compares against still current?
#
# WHY: a task worktree may be cloned with a single-branch refspec
# (`+refs/heads/main:refs/remotes/origin/main`), so `refs/remotes/origin/<x>`
# exists only for the branches that checkout happened to fetch. Answering
# "is this branch on the remote?" from `git rev-parse --verify origin/<x>`
# reports a branch that does exist — including one a colleague pushed after
# the clone — as missing, and the wrong answer is indistinguishable from the
# right one. Only the remote can answer this, so ask it directly:
# `git ls-remote --heads`. The local remote-tracking ref is a cache, and this
# script never reads it to decide existence.
#
# The second question is about that cache: a branch can exist while the
# `origin/main` this worktree diffs and merges against is not the remote's.
# The script names that and prints the one command that fixes it; `--fetch`
# runs that command and re-reads the result. The command carries an explicit
# refspec: a bare `git fetch origin main` updates FETCH_HEAD and, in a
# checkout whose refspec does not map main — or across a tag that happens to
# be named main — exits 0 without moving `refs/remotes/origin/main`, which
# would leave the report stale forever.
#
# Usage: check-remote-branch.sh [--remote NAME] [--fetch] <branch>
#        check-remote-branch.sh --self-test
#
# <branch> takes a bare name, `refs/heads/<name>`, or `<remote>/<name>`. The
# answer is about the remote branch of that name; nothing else is inferred.
#
# Output is one `KEY: value` line per fact, on stdout, so a caller can read a
# field without parsing the sentence above it:
#
#   BRANCH            the branch asked about, after normalisation
#   REMOTE            the remote that was queried
#   EXISTS            yes | no | unknown
#   REMOTE_SHA        the branch's commit on the remote, or -
#   ORIGIN_MAIN       <remote>/main against the remote: fresh | stale | absent
#                     (absent: the remote has no main; the cache cannot be
#                     compared, so the answer is not "fresh")
#   LOCAL_ORIGIN_MAIN the cached commit in this checkout, or -
#   REMOTE_MAIN       main's commit on the remote, or -
#   NEXT              the shortest command that changes the verdict, or none
#   VERDICT           one sentence saying what was found
#
# Exit 0 branch exists and `origin/main` is current (or was refreshed).
# Exit 1 the branch does not exist on the remote. This wins over exit 3: a
#        caller that reads only the code must never see "the branch exists"
#        for one that does not.
# Exit 2 cannot judge: no such remote, or `ls-remote` failed (network, auth).
#        This is deliberately not exit 1 — an unreadable remote is not an
#        absent branch, and the fix is different.
# Exit 3 the branch exists but the base is not current — `origin/main` is
#        not the remote's, or the remote has no main to compare against.
#        NEXT says what to run.
set -euo pipefail

SELF_TEST=0
FETCH=0
REMOTE=origin
BRANCH=""

usage() {
  sed -n 's/^# \{0,1\}//p' <<'EOF'
# Usage: check-remote-branch.sh [--remote NAME] [--fetch] <branch>
#        check-remote-branch.sh --self-test
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --self-test) SELF_TEST=1; shift ;;
    --fetch) FETCH=1; shift ;;
    --remote)
      [ "$#" -ge 2 ] || { echo "FAIL: --remote needs a name" >&2; exit 2; }
      REMOTE="$2"; shift 2 ;;
    --remote=*) REMOTE="${1#--remote=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    --)
      shift
      if [ "$#" -gt 0 ]; then
        [ -z "$BRANCH" ] || { echo "FAIL: one branch at a time (got '$BRANCH' and '$1')" >&2; exit 2; }
        BRANCH="$1"; shift
      fi
      break ;;
    -*) echo "FAIL: unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      [ -z "$BRANCH" ] || { echo "FAIL: one branch at a time (got '$BRANCH' and '$1')" >&2; exit 2; }
      BRANCH="$1"; shift ;;
  esac
done

if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  me="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  ident=(-c user.name=self-test -c user.email=self-test@example.invalid)

  # A bare remote and a checkout that fetched main only: the shape the answer
  # has to survive. `git clone --single-branch` writes exactly the refspec that
  # makes `refs/remotes/origin/<other>` absent.
  git init -q --bare "$tmp/remote.git"
  git init -q -b main "$tmp/seed"
  git -C "$tmp/seed" "${ident[@]}" commit -q --allow-empty -m one
  git -C "$tmp/seed" branch feature/only-on-remote
  git -C "$tmp/seed" remote add origin "$tmp/remote.git"
  git -C "$tmp/seed" push -q origin main feature/only-on-remote
  git clone -q --single-branch --branch main "$tmp/remote.git" "$tmp/clone"

  # If the clone ever stopped being single-branch, every case below would pass
  # for the wrong reason: `rev-parse origin/<x>` would find the ref the case
  # exists to make absent.
  if git -C "$tmp/clone" rev-parse --verify --quiet refs/remotes/origin/feature/only-on-remote >/dev/null; then
    self_fail "the fixture is not single-branch: origin/feature/only-on-remote is local"
  fi

  CODE=0
  OUT=""
  verdict() {
    local dir="$1"; shift
    OUT="$(cd "$dir" && bash "$me" "$@" 2>&1)" && CODE=0 || CODE=$?
  }
  says() {
    case "$OUT" in
      *"$1"*) ;;
      *) self_fail "$2 (exit $CODE, got: $OUT)" ;;
    esac
  }
  says_not() {
    case "$OUT" in
      *"$1"*) self_fail "$2 (exit $CODE, got: $OUT)" ;;
    esac
  }

  # The case the guard exists for: the ref is absent locally and present
  # remotely. A local-refs implementation answers "no" here.
  verdict "$tmp/clone" feature/only-on-remote
  [ "$CODE" -eq 0 ] || self_fail "a branch only on the remote must read as existing (exit $CODE)"
  says "EXISTS: yes" "the report must say the branch exists"
  says "REMOTE_SHA: $(git -C "$tmp/seed" rev-parse refs/heads/feature/only-on-remote)" "the report must carry the remote commit"
  says "ORIGIN_MAIN: fresh" "a fresh clone's origin/main must read as fresh"

  # The other direction: a name nobody pushed is absent, not unknown.
  verdict "$tmp/clone" feature/never-pushed
  [ "$CODE" -eq 1 ] || self_fail "a branch absent from the remote must exit 1 (exit $CODE)"
  says "EXISTS: no" "the report must say the branch is absent"
  says "NEXT: git push -u origin HEAD:feature/never-pushed" "the report must name the push that creates it"

  # A remote that cannot be read is not an absent branch.
  verdict "$tmp/clone" --remote no-such-remote main
  [ "$CODE" -eq 2 ] || self_fail "an unreadable remote must exit 2, not 1 (exit $CODE)"
  says "EXISTS: unknown" "an unreadable remote must read as unknown"
  says_not "EXISTS: no" "an unreadable remote must not be reported as an absent branch"

  # `origin/<name>` and `refs/heads/<name>` name the same branch.
  verdict "$tmp/clone" origin/feature/only-on-remote
  [ "$CODE" -eq 0 ] || self_fail "an origin/-prefixed name must resolve (exit $CODE)"
  says "BRANCH: feature/only-on-remote" "the report must print the bare branch name"

  # The remote advances; the checkout's cache does not. That is the stale base
  # the second half of this script is about.
  git -C "$tmp/seed" "${ident[@]}" commit -q --allow-empty -m two
  git -C "$tmp/seed" push -q origin main
  verdict "$tmp/clone" feature/only-on-remote
  [ "$CODE" -eq 3 ] || self_fail "a stale origin/main must exit 3 (exit $CODE)"
  says "ORIGIN_MAIN: stale" "the report must say origin/main is stale"
  says "NEXT: git fetch origin +refs/heads/main:refs/remotes/origin/main" "the report must name the command that fixes it"

  # --fetch runs it, and the second reading is the one that counts. The cache
  # itself has to move: a report that says "fresh" over an unchanged ref is
  # the failure this case exists for.
  verdict "$tmp/clone" --fetch feature/only-on-remote
  [ "$CODE" -eq 0 ] || self_fail "--fetch must clear the stale base (exit $CODE, got: $OUT)"
  says "ORIGIN_MAIN: fresh" "after --fetch, origin/main must read as fresh"
  says "NEXT: none" "with nothing left to fix, NEXT must be none"
  [ "$(git -C "$tmp/clone" rev-parse refs/remotes/origin/main)" = "$(git -C "$tmp/seed" rev-parse refs/heads/main)" ] \
    || self_fail "--fetch must move refs/remotes/origin/main to the remote's commit"

  # A tag named main must not capture the fetch: `git fetch origin main`
  # resolves the bare name to the tag and exits 0 without moving the cache.
  git init -q --bare "$tmp/tagremote.git"
  git init -q -b main "$tmp/tagseed"
  git -C "$tmp/tagseed" "${ident[@]}" commit -q --allow-empty -m one
  git -C "$tmp/tagseed" tag main
  git -C "$tmp/tagseed" remote add origin "$tmp/tagremote.git"
  git -C "$tmp/tagseed" push -q origin refs/heads/main:refs/heads/main refs/tags/main:refs/tags/main
  git clone -q --single-branch --branch main "$tmp/tagremote.git" "$tmp/tagclone"
  git -C "$tmp/tagseed" "${ident[@]}" commit -q --allow-empty -m two
  git -C "$tmp/tagseed" push -q origin refs/heads/main:refs/heads/main
  verdict "$tmp/tagclone" --fetch main
  [ "$CODE" -eq 0 ] || self_fail "a tag named main must not keep the base stale (exit $CODE, got: $OUT)"
  [ "$(git -C "$tmp/tagclone" rev-parse refs/remotes/origin/main)" = "$(git -C "$tmp/tagseed" rev-parse refs/heads/main)" ] \
    || self_fail "--fetch must move the cache even with a tag named main"

  # A checkout cloned from another branch does not track main at all, so a
  # bare-name fetch would exit 0 and leave the cache missing forever.
  git init -q --bare "$tmp/narrow.git"
  git init -q -b main "$tmp/narrowseed"
  git -C "$tmp/narrowseed" "${ident[@]}" commit -q --allow-empty -m one
  git -C "$tmp/narrowseed" branch topic
  git -C "$tmp/narrowseed" remote add origin "$tmp/narrow.git"
  git -C "$tmp/narrowseed" push -q origin main topic
  git clone -q --single-branch --branch topic "$tmp/narrow.git" "$tmp/narrowclone"
  if git -C "$tmp/narrowclone" rev-parse --verify --quiet refs/remotes/origin/main >/dev/null; then
    self_fail "the narrow fixture unexpectedly tracks main"
  fi
  verdict "$tmp/narrowclone" --fetch topic
  [ "$CODE" -eq 0 ] || self_fail "a single-branch clone of another branch must converge (exit $CODE, got: $OUT)"
  [ "$(git -C "$tmp/narrowclone" rev-parse refs/remotes/origin/main)" = "$(git -C "$tmp/narrowseed" rev-parse refs/heads/main)" ] \
    || self_fail "--fetch must create the main cache in a checkout that did not track it"

  # A remote with no main cannot be called fresh.
  git init -q --bare "$tmp/nomain.git"
  git init -q -b trunk "$tmp/nomainseed"
  git -C "$tmp/nomainseed" "${ident[@]}" commit -q --allow-empty -m one
  git -C "$tmp/nomainseed" branch feature/trunk
  git -C "$tmp/nomainseed" remote add origin "$tmp/nomain.git"
  git -C "$tmp/nomainseed" push -q origin trunk feature/trunk
  git clone -q --single-branch --branch trunk "$tmp/nomain.git" "$tmp/nomainclone"
  verdict "$tmp/nomainclone" feature/trunk
  [ "$CODE" -eq 3 ] || self_fail "a remote without main must exit 3, not 0 (exit $CODE, got: $OUT)"
  says "ORIGIN_MAIN: absent" "a remote without main must read as absent"
  says "NEXT: git ls-remote --symref origin HEAD" "the report must name the default-branch probe"

  # A cache that was never written is as stale as a disagreeing one.
  git -C "$tmp/clone" update-ref -d refs/remotes/origin/main
  verdict "$tmp/clone" feature/only-on-remote
  [ "$CODE" -eq 3 ] || self_fail "a missing origin/main must exit 3 (exit $CODE, got: $OUT)"
  says "ORIGIN_MAIN: stale" "a missing origin/main must read as stale"
  says "LOCAL_ORIGIN_MAIN: -" "a missing origin/main must read as -"

  # A failed fetch must not turn an absent branch into exit 3: the exit code
  # says "the branch exists", and a caller that reads only it would be misled.
  git -C "$tmp/seed" "${ident[@]}" commit -q --allow-empty -m three
  git -C "$tmp/seed" push -q origin main
  mkdir -p "$tmp/clone/.git/refs/remotes/origin"
  : > "$tmp/clone/.git/refs/remotes/origin/main.lock"
  verdict "$tmp/clone" --fetch feature/never-pushed
  rm -f "$tmp/clone/.git/refs/remotes/origin/main.lock"
  [ "$CODE" -eq 1 ] || self_fail "an absent branch must exit 1 even when the fetch fails (exit $CODE, got: $OUT)"
  says "EXISTS: no" "the failed fetch must not change the branch answer"

  # A remote that is configured but cannot be read is "cannot judge" too.
  git -C "$tmp/clone" remote set-url origin "$tmp/not-a-repo"
  verdict "$tmp/clone" main
  [ "$CODE" -eq 2 ] || self_fail "an unreachable remote must exit 2, not 1 (exit $CODE, got: $OUT)"
  says "EXISTS: unknown" "an unreachable remote must read as unknown"
  says_not "EXISTS: no" "an unreachable remote must not be reported as an absent branch"

  echo "PASS: check-remote-branch self-test (remote-only branch, absent branch,"
  echo "      unreadable remote, name forms, stale base, --fetch that moves the"
  echo "      cache, a tag named main, a checkout that did not track main, a"
  echo "      remote without main, a missing cache, a failed fetch)"
  exit 0
fi

[ -n "$BRANCH" ] || { usage >&2; exit 2; }

# `origin/feature/x` is how a person names a remote branch after a fetch; the
# query itself is about `refs/heads/feature/x`.
case "$BRANCH" in
  refs/heads/*) BRANCH="${BRANCH#refs/heads/}" ;;
esac
case "$BRANCH" in
  "$REMOTE"/*) BRANCH="${BRANCH#"$REMOTE"/}" ;;
esac
[ -n "$BRANCH" ] || { echo "FAIL: the branch name is empty" >&2; exit 2; }

# Git's own message, flattened onto one line so it fits a KEY: value report.
one_line() {
  tr '\n' ' ' < "$1" | tr -s '[:space:]' ' ' | sed -e 's/^ *//' -e 's/ *$//'
}

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  printf 'BRANCH: %s\nREMOTE: %s\nEXISTS: unknown\nREMOTE_SHA: -\nORIGIN_MAIN: unknown\nLOCAL_ORIGIN_MAIN: -\nREMOTE_MAIN: -\nNEXT: git remote add %s <url>\nVERDICT: cannot judge — there is no remote named %s here\n' \
    "$BRANCH" "$REMOTE" "$REMOTE" "$REMOTE"
  exit 2
fi

# One round trip answers both questions. The ref arguments are full ref names,
# so a wildcard in a branch name cannot widen the match.
ref="refs/heads/$BRANCH"
main_ref="refs/heads/main"
# The fetch is spelled with its refspec. `git fetch origin main` resolves the
# bare name (a tag named main wins) and, in a checkout whose refspec does not
# map main, writes only FETCH_HEAD — both exit 0 without moving the cache, so
# NEXT would name a command that cannot change the verdict.
main_fetch="+refs/heads/main:refs/remotes/$REMOTE/main"
fetch_cmd="git fetch $REMOTE $main_fetch"
tmp_err="$(mktemp)"
trap 'rm -f "$tmp_err"' EXIT

if ! remote_out="$(git ls-remote --heads -- "$REMOTE" "$ref" "$main_ref" 2>"$tmp_err")"; then
  printf 'BRANCH: %s\nREMOTE: %s\nEXISTS: unknown\nREMOTE_SHA: -\nORIGIN_MAIN: unknown\nLOCAL_ORIGIN_MAIN: -\nREMOTE_MAIN: -\nNEXT: check the network and credentials, then run this again\nVERDICT: cannot judge — %s did not answer (%s)\n' \
    "$BRANCH" "$REMOTE" "$REMOTE" "$(one_line "$tmp_err")"
  exit 2
fi

sha_for() {
  printf '%s\n' "$remote_out" | awk -v ref="$1" '$2 == ref { print $1; found = 1 } END { exit(found ? 0 : 1) }'
}

exists=no
branch_sha=""
if branch_sha="$(sha_for "$ref")"; then exists=yes; fi

remote_main=""
if ! remote_main="$(sha_for "$main_ref")"; then remote_main=""; fi

local_main="$(git rev-parse --verify --quiet "refs/remotes/$REMOTE/main" || true)"

# A missing local ref is as stale as a disagreeing one: this checkout has
# nothing current to compare against, and the same one command fixes both.
origin_main=stale
if [ -z "$remote_main" ]; then
  origin_main=absent
elif [ -n "$local_main" ] && [ "$local_main" = "$remote_main" ]; then
  origin_main=fresh
fi

fetch_ran=0
if [ "$FETCH" = 1 ] && [ "$origin_main" = stale ]; then
  if ! git fetch --quiet "$REMOTE" "$main_fetch" 2>"$tmp_err"; then
    printf 'BRANCH: %s\nREMOTE: %s\nEXISTS: %s\nREMOTE_SHA: %s\nORIGIN_MAIN: stale\nLOCAL_ORIGIN_MAIN: %s\nREMOTE_MAIN: %s\nNEXT: %s\nVERDICT: the branch answer stands, but the fetch failed (%s)\n' \
      "$BRANCH" "$REMOTE" "$exists" "${branch_sha:--}" "${local_main:--}" "$remote_main" \
      "$fetch_cmd" "$(one_line "$tmp_err")"
    # The branch question is the primary answer, so its code wins: a caller
    # must never read exit 3 ("the branch exists") for an absent branch.
    if [ "$exists" = no ]; then exit 1; fi
    exit 3
  fi
  fetch_ran=1
  local_main="$(git rev-parse --verify --quiet "refs/remotes/$REMOTE/main" || true)"
  if [ -n "$local_main" ] && [ "$local_main" = "$remote_main" ]; then
    origin_main=fresh
  fi
fi

next="none"
if [ "$exists" = no ]; then
  next="git push -u $REMOTE HEAD:$BRANCH   # to create it"
elif [ "$origin_main" = stale ]; then
  next="$fetch_cmd"
elif [ "$origin_main" = absent ]; then
  next="git ls-remote --symref $REMOTE HEAD   # which branch is the default?"
fi

verdict=""
if [ "$exists" = no ]; then
  verdict="the branch does not exist on $REMOTE"
  [ "$origin_main" = fresh ] || verdict="$verdict; $REMOTE/main is not the current base here either"
elif [ "$origin_main" = fresh ]; then
  verdict="the branch exists on $REMOTE and origin/main is current"
elif [ "$origin_main" = absent ]; then
  verdict="the branch exists on $REMOTE, but $REMOTE has no main to compare against"
elif [ "$fetch_ran" = 1 ]; then
  verdict="the branch exists on $REMOTE, but the fetch succeeded and refs/remotes/$REMOTE/main still did not move"
else
  verdict="the branch exists on $REMOTE, but this checkout's origin/main is not the remote's"
fi

printf 'BRANCH: %s\nREMOTE: %s\nEXISTS: %s\nREMOTE_SHA: %s\nORIGIN_MAIN: %s\nLOCAL_ORIGIN_MAIN: %s\nREMOTE_MAIN: %s\nNEXT: %s\nVERDICT: %s\n' \
  "$BRANCH" "$REMOTE" "$exists" "${branch_sha:--}" "$origin_main" "${local_main:--}" "${remote_main:--}" "$next" "$verdict"

if [ "$exists" = no ]; then exit 1; fi
[ "$origin_main" = fresh ] || exit 3
exit 0
