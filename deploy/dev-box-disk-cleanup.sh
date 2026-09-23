#!/usr/bin/env bash
# Routine reclaim of REGENERABLE caches on a dev/agent box.
#
# Why this exists next to cheesex-disk-pressure-guard.sh rather than inside it:
# the guard is an EMERGENCY brake — it waits for 85%, checks that no turn is
# active, then removes sandbox containers. It never touches a build cache, so on
# a box whose space is in caches it brakes against something it cannot reclaim.
# This script reclaims the caches, and `cheese-disk-cleanup.timer`
# (deploy/install-disk-cleanup-timer.sh) runs it nightly above 75%, which is
# under the guard's 85% on purpose.
#
# Measured on cheese-dev-env6-app, 2026-08-11, at 92% full (2.6G free):
#   docker build cache   3.0G   71 entries, 0 in use
#   /var/cache/apt       1.7G   downloaded .deb archives
#   ~/.cache/go-build    1.3G
#   ~/.vscode-server     2.0G   superseded server versions + VSIX cache
#   ~/.cache/puccinialin 665M   rust toolchain downloads for python builds
# Reclaiming exactly these took it to 70% (9.1G free). Nothing else was touched.
#
# The rule this script encodes: only delete what a command can rebuild. A slower
# next build is an acceptable price; someone else's data is not. Concretely it
# never touches container images or volumes that a running container uses, never
# touches ~/.cache/ms-playwright (browser binaries the e2e suite needs and pip
# will not refetch on demand), and never touches anything under a project dir.
#
# It watches more than one filesystem, because on these boxes the one that fills
# first is not the one `df /` reports. `/tmp` is a separate tmpfs — a RAM disk —
# and every pytest run leaves its temp tree there: one suite that builds a venv
# leaves gigabytes, nothing prunes them across runs, and they accumulate until
# the RAM disk is full. On 2026-09-22 that is exactly what happened on the dev
# box: `/` sat at 51% while `/tmp` hit 100%, every write under `/tmp` began
# failing with ENOSPC, and the environment-preparation step for every NEW topic
# died at startup — so the platform read as "cannot open a new topic" while the
# disk it was being judged on looked healthy. The nightly timer did not even
# start: its condition asked `df /`. So the condition moved in here (`--needed`),
# where it can ask about every filesystem this script reclaims on.
#
# Reports by default. Pass --apply to actually delete.

set -euo pipefail

APPLY=0
SELF_TEST=0
NEEDED_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --self-test) SELF_TEST=1 ;;
    --needed) NEEDED_ONLY=1 ;;
    -h|--help) sed -n '2,38p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

#: Run above this. It lives here rather than in the unit file so that the mark
#: the timer tests and the mark this script reports against cannot drift apart —
#: and so a box can be asked about its own numbers with one command.
#: 75 sits under `cheesex-disk-pressure-guard`'s 85% emergency brake, so the
#: caches go before the guard has to start removing sandbox containers.
#: Read through a function, and on every call, so `--needed` and the reclaim in
#: one run cannot disagree about the mark.
threshold_pct() { printf '%s' "${CHEESE_DISK_CLEANUP_THRESHOLD:-75}"; }

human() { numfmt --to=iec --suffix=B "${1:-0}" 2>/dev/null || printf '%sB' "${1:-0}"; }

# Size of a path in bytes, 0 when absent or unreadable. Every caller feeds this
# into $(( )), so it MUST emit exactly one integer and nothing else — a root-owned
# directory makes `du` emit a partial line plus nothing on stdout, and the two
# lines that came back from /var/cache/apt/archives turned $(( )) into a syntax
# error. Hence: one line, validated numeric, 0 otherwise.
size_of() {
  test -e "$1" || { printf '0'; return 0; }
  local n
  n="$(du -sxb "$1" 2>/dev/null | head -1 | cut -f1)"
  case "$n" in
    ''|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$n" ;;
  esac
}

# Where a run's throwaway files land. Both are swept because either can be its
# own filesystem: `/tmp` is a tmpfs on these boxes and `/var/tmp` is not always
# on the root filesystem either.
#
# `CHEESE_TEMP_ROOTS` replaces the pair outright. The self-test needs it — a
# self-test that reclaims out of the box's real `/var/tmp` is not one anybody
# runs twice — and a box that puts temp somewhere else entirely needs it too.
temp_roots() {
  if [ -n "${CHEESE_TEMP_ROOTS:-}" ]; then
    printf '%s\n' $CHEESE_TEMP_ROOTS
    return 0
  fi
  printf '%s\n' "${TMPDIR:-/tmp}" /var/tmp
}

# Every mount point this script answers for, deduped: the root, plus any temp
# root that is a filesystem of its own. On a box where `/tmp` is a directory on
# `/`, this is one line and nothing about the old behaviour changes.
watched_mounts() {
  { printf '/\n'; temp_roots; } | while IFS= read -r path; do
    test -d "$path" || continue
    df --output=target "$path" 2>/dev/null | tail -1
  done | sort -u
}

# Percent used on the filesystem holding a path, or empty when there is no
# reading. Empty is not 0: a `df` that failed must not be read as "plenty of
# room", which is the mistake that let the tmpfs fill up unnoticed.
pct_of() {
  local n
  n="$(df --output=pcent "$1" 2>/dev/null | tail -1 | tr -dc '0-9')"
  case "$n" in
    ''|*[!0-9]*) printf '' ;;
    *) printf '%s' "$n" ;;
  esac
}

# True when some filesystem is at or above the mark. This is what the systemd
# unit asks before running at all, so it has to be able to say yes about `/tmp`
# and not just about `/`.
needed() {
  local mount pct mark
  mark="$(threshold_pct)"
  for mount in $(watched_mounts); do
    pct="$(pct_of "$mount")"
    test -n "$pct" || continue
    [ "$pct" -ge "$mark" ] && return 0
  done
  return 1
}

TOTAL=0
plan() { # name, bytes, command...
  local name="$1" bytes="$2"; shift 2
  TOTAL=$((TOTAL + bytes))
  printf '  %-34s %8s\n' "$name" "$(human "$bytes")"
  if [ "$APPLY" -eq 1 ] && [ "$bytes" -gt 0 ]; then
    "$@" >/dev/null 2>&1 || printf '    (failed, skipped)\n'
  fi
}

# Old vscode-server payloads: keep the newest server build, drop superseded ones.
# Editors redownload on next connect; keeping the newest avoids doing that today.
prune_vscode_servers() {
  local dir="$HOME/.vscode-server/cli/servers"
  test -d "$dir" || return 0
  local keep
  keep="$(ls -1t "$dir" 2>/dev/null | head -1 || true)"
  test -n "$keep" || return 0
  find "$dir" -mindepth 1 -maxdepth 1 ! -name "$keep" -exec rm -rf -- {} + 2>/dev/null || true
}

vscode_servers_reclaimable() {
  local dir="$HOME/.vscode-server/cli/servers" total keep
  test -d "$dir" || { printf '0'; return 0; }
  total="$(size_of "$dir")"
  keep="$(ls -1t "$dir" 2>/dev/null | head -1 || true)"
  test -n "$keep" || { printf '%s' "$total"; return 0; }
  printf '%s' "$((total - $(size_of "$dir/$keep")))"
}

# pytest's per-run temp trees. Regenerable by definition — pytest makes one per
# run and keeps only the newest few itself (`keep_temp_dirs`) — but nothing
# prunes them ACROSS runs, so they are the thing that fills `/tmp` on a box where
# people run suites all day. On the dev box on 2026-09-22 they were 13G of a 32G
# RAM disk, and the largest single tree was 5.3G.
#
# Two guards, because one is not enough for a directory whose contents are
# somebody's running test:
#   * the newest N per user are left alone — pytest's own reasoning, and the run
#     happening right now is almost certainly among them;
#   * the rest must also be older than the window, for a suite that has been
#     running longer than anyone expected.
# A tree another user owns is not reachable from here anyway: /tmp is sticky, so
# `rm` refuses, and the failure is reported rather than silent.
#: How many of a user's most recent trees to leave alone.
#: Both knobs are read through functions, on every call — a variable read once at
#: startup cannot be overridden by the caller before the call that matters, and
#: the self-test below is exactly such a caller.
pytest_temp_keep() { printf '%s' "${CHEESE_PYTEST_TEMP_KEEP:-2}"; }
#: And how old the rest must be, in minutes.
pytest_temp_age_minutes() { printf '%s' "${CHEESE_PYTEST_TEMP_AGE_MINUTES:-360}"; }

pytest_temp_candidates() {
  local root user_dir
  for root in $(temp_roots); do
    for user_dir in "$root"/pytest-of-*; do
      test -d "$user_dir" || continue
      # `-type d` skips `pytest-current`, which is a symlink to the newest — the
      # one link we most want to keep pointing at something.
      find "$user_dir" -mindepth 1 -maxdepth 1 -type d -name 'pytest-*' \
        -mmin "+$(pytest_temp_age_minutes)" -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | tail -n +"$(( $(pytest_temp_keep) + 1 ))" | cut -d' ' -f2-
    done
  done
}

pytest_temp_reclaimable() {
  local total=0 tree
  while IFS= read -r tree; do
    test -n "$tree" || continue
    total=$((total + $(size_of "$tree")))
  done <<EOF
$(pytest_temp_candidates)
EOF
  printf '%s' "$total"
}

prune_pytest_temps() {
  local tree
  while IFS= read -r tree; do
    test -n "$tree" || continue
    rm -rf -- "$tree"
  done <<EOF
$(pytest_temp_candidates)
EOF
}

# Docker prints sizes as `5.228GB` / `344.1kB` — an SI number with a trailing
# `B`. `numfmt --from=iec` rejects that ("invalid suffix ... 'B'"), and the
# guard below turns the failure into 0, so every size read this way came back
# as nothing and the prune beside it was skipped on every box, silently.
docker_bytes() {
  local s="${1:-}" n
  s="${s%% *}"          # "5.228GB (0%)" -> "5.228GB"
  s="${s%B}"            # -> "5.228G", which --from=auto reads (and "Gi" too)
  n="$(printf '%s' "$s" | numfmt --from=auto 2>/dev/null | head -1)"
  case "$n" in
    ''|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$n" ;;
  esac
}

# `docker system df` is not cheap — 33s on the box that needed this script, and
# it is asked for two different rows. Read it ONCE, in the parent shell: each
# `plan` argument runs in a subshell, so a cache filled lazily inside one would
# be thrown away and the call repeated.
DOCKER_DF=""
docker_df_load() {
  command -v docker >/dev/null 2>&1 || return 0
  DOCKER_DF="$(docker system df --format '{{.Type}}\t{{.Reclaimable}}' 2>/dev/null)"
}

docker_df_reclaimable() {
  [ -n "$DOCKER_DF" ] || { printf '0'; return 0; }
  docker_bytes "$(printf '%s\n' "$DOCKER_DF" | awk -F'\t' -v want="$1" '$1==want{print $2; exit}')"
}

docker_build_cache() { docker_df_reclaimable "Build Cache"; }

# Anonymous volumes with no container referencing them — what a finished test
# run's postgres service container leaves behind, one per run. `docker volume
# prune` without `--all` takes exactly these and leaves named volumes alone, so
# a co-tenant's stopped work is not at risk: a stopped container still counts as
# referencing its volume.
# Unused local volumes. The number is `docker system df`'s aggregate, which is
# an UPPER BOUND on what gets freed: `docker volume prune` without `--all` takes
# only the anonymous ones, and a named volume nothing currently mounts stays.
# That is the intended split — an anonymous volume is what a finished test run's
# postgres service container leaves behind, one per run, and it is regenerable
# by definition; a named one is somebody's data. A stopped container still
# counts as referencing its volume, so a co-tenant's paused work is not at risk.
#
# Deliberately NOT sized with `docker system df -v`, which walks every volume:
# on the box that needed this (718 volumes) that call alone ran over five
# minutes, and a cleanup that costs more than it reclaims will not be run.
#
# One named volume dominates this row on dev, and it is not reclaimable:
# `buildx_buildkit_cheese-box-builder0_state`, the layer cache of the builder
# `.github/workflows/build.yml` creates with `keep-state: true`. The builder
# only exists while a build job runs; between jobs the volume stands alone, so
# `docker system df` counts it as unused, `docker volume prune` skips it for
# being named, and there is no builder for `docker buildx prune` to address.
# That is by design — build.yml prunes it itself after every job
# (`--max-used-space 20GB --min-free-space 10GB`), and emptying it here on
# 2026-09-18 only made the next box-image build refill all 12GB from scratch.
# So the row reports the aggregate MINUS those volumes: what the prune below
# can actually free. Sized with `du` on the volume directories rather than
# `docker system df -v`, which walks every volume and took over five minutes.
# The names come from docker (the docker group can list them) and the bytes
# from sudo (`/var/lib/docker/volumes` is root-only, so a glob there is empty).
docker_unused_volumes() {
  local total kept=0 name n
  total="$(docker_df_reclaimable "Local Volumes")"
  for name in $(docker volume ls -q --filter name=buildx_buildkit_ 2>/dev/null); do
    n="$(sudo du -sxb "/var/lib/docker/volumes/$name/_data" 2>/dev/null | head -1 | cut -f1)"
    case "$n" in ''|*[!0-9]*) ;; *) kept=$((kept + n)) ;; esac
  done
  [ "$total" -gt "$kept" ] && printf '%s' "$((total - kept))" || printf '0'
}

self_test() {
  # The one thing worth asserting: size_of must not abort the script on a
  # missing path. That was the whole failure mode of an earlier `set -e` bug in
  # check.sh — a guarded command in a branch, silently taking the script down.
  local out
  out="$(size_of /definitely/not/here)"
  test "$out" = "0" || { printf 'self-test FAIL: size_of on missing path -> %s\n' "$out"; exit 1; }
  # Every size feeds $(( )), so one integer and nothing else. A root-owned tree
  # is the case that actually broke this: it returned two lines and $(( )) died.
  # `/etc` stands in for that case rather than `/`: both are root-owned with
  # unreadable subdirectories (`/etc/ssl/private` is 0700), and `du -sxb /`
  # walks the whole root filesystem — on the box that needed this script, 431GB
  # of it, which took the self-test past ten minutes. A check that costs that
  # much is one nobody runs, and a self-test nobody runs is how the size-parsing
  # bug above survived in every release.
  for probe in /var/cache/apt/archives /var/log /etc "$HOME/.cache"; do
    out="$(size_of "$probe")"
    case "$out" in
      ''|*[!0-9]*) printf 'self-test FAIL: size_of %s -> %q\n' "$probe" "$out"; exit 1 ;;
    esac
    test "$(printf '%s' "$out" | wc -l)" -eq 0 \
      || { printf 'self-test FAIL: size_of %s emitted multiple lines\n' "$probe"; exit 1; }
  done
  # Assert the PARSE, not just that a number came out: the bug this replaces
  # returned a perfectly numeric 0 from a failed conversion, so a self-test that
  # only checked for digits passed while the reclaim never ran.
  for probe in '5.228GB=5228000000' '344.1kB=344100' '15.98GB=15980000000' \
               '2.5GiB=2684354560' '0B=0'; do
    got="$(docker_bytes "${probe%%=*}")"
    [ "$got" = "${probe#*=}" ] \
      || { printf 'self-test FAIL: docker_bytes %s -> %s, want %s\n' \
             "${probe%%=*}" "$got" "${probe#*=}"; exit 1; }
  done
  # Not calling docker here: the parse is what broke, and it is covered above.
  DOCKER_DF="$(printf 'Images\t0B (0%%)\nBuild Cache\t5.228GB\nLocal Volumes\t15.98GB (62%%)\n')"
  out="$(docker_build_cache)"
  [ "$out" = 5228000000 ] \
    || { printf 'self-test FAIL: build cache row -> %s, want 5228000000\n' "$out"; exit 1; }
  # The volumes row is the aggregate minus the buildx state volumes, and on a
  # box that has one it must come out smaller, never negative: `du` on a
  # directory that is not there contributes nothing, so here it is the whole row.
  out="$(docker_unused_volumes)"
  case "$out" in ''|*[!0-9]*) printf 'self-test FAIL: volumes row -> %s\n' "$out"; exit 1 ;; esac
  [ "$out" -le 15980000000 ] \
    || { printf 'self-test FAIL: volumes row -> %s, above the aggregate\n' "$out"; exit 1; }
  DOCKER_DF=""
  out="$(vscode_servers_reclaimable)"
  case "$out" in ''|*[!0-9]*) printf 'self-test FAIL: non-numeric reclaimable -> %s\n' "$out"; exit 1 ;; esac
  # The mounts this watches must include the temp filesystems, not just the
  # root: the whole bug was a condition that asked `df /` while `/tmp` was the
  # one that filled. Asserting on `watched_mounts` rather than on `/tmp` by name
  # keeps this honest on a box where the two are the same filesystem.
  out="$(watched_mounts)"
  grep -qx "/" <<<"$out" || { printf 'self-test FAIL: the root is not watched\n'; exit 1; }
  test -n "$out" || { printf 'self-test FAIL: no mounts watched\n'; exit 1; }
  # pct_of says nothing when it cannot read, and nothing is not zero. Both halves
  # matter: "0%" would read as room to spare on a filesystem nobody could measure.
  out="$(pct_of /definitely/not/here)"
  test -z "$out" || { printf 'self-test FAIL: pct_of on a missing path -> %q\n' "$out"; exit 1; }
  out="$(pct_of /)"
  case "$out" in ''|*[!0-9]*) printf 'self-test FAIL: pct_of / -> %q\n' "$out"; exit 1 ;; esac
  # The threshold decides whether the nightly run happens at all, so it has to be
  # the one this script reports against — and settable, so a test can drive it.
  CHEESE_DISK_CLEANUP_THRESHOLD=0 needed \
    || { printf 'self-test FAIL: needed is false at a threshold of 0\n'; exit 1; }
  CHEESE_DISK_CLEANUP_THRESHOLD=101 needed \
    && { printf 'self-test FAIL: needed is true at a threshold of 101\n'; exit 1; }
  # Both halves of the pytest selector, on a throwaway tree: a tree inside the
  # keep-count survives even though it is past the age window, because the newest
  # few are the run that is happening right now.
  local probe_root
  probe_root="$(mktemp -d)"
  trap 'rm -rf "$probe_root"' EXIT
  mkdir -p "$probe_root/pytest-of-selftest/pytest-1" \
           "$probe_root/pytest-of-selftest/pytest-2" \
           "$probe_root/pytest-of-selftest/pytest-3"
  touch -d '2 days ago' "$probe_root/pytest-of-selftest/pytest-1" \
                       "$probe_root/pytest-of-selftest/pytest-2" \
                       "$probe_root/pytest-of-selftest/pytest-3"
  # The selector must be pointed at the probe, or it sweeps the box's own
  # /var/tmp — which is the one thing a self-test may never do.
  export CHEESE_TEMP_ROOTS="$probe_root"
  out="$(pytest_temp_candidates)"
  test "$out" = "$probe_root/pytest-of-selftest/pytest-1" \
    || { printf 'self-test FAIL: pytest_temp_candidates -> %s\n' "$out"; exit 1; }
  out="$(pytest_temp_reclaimable)"
  case "$out" in ''|*[!0-9]*) printf 'self-test FAIL: pytest_temp_reclaimable -> %q\n' "$out"; exit 1 ;; esac
  prune_pytest_temps
  test ! -d "$probe_root/pytest-of-selftest/pytest-1" \
    || { printf 'self-test FAIL: the stale tree survived the prune\n'; exit 1; }
  test -d "$probe_root/pytest-of-selftest/pytest-2" \
    || { printf 'self-test FAIL: the prune took a tree inside the keep-count\n'; exit 1; }
  # And the age window is the other half, asserted on its own: with the
  # keep-count set to zero — so it can protect nothing — only the old trees come
  # back, and the one being written now does not.
  mkdir -p "$probe_root/pytest-of-selftest/pytest-4"
  out="$(CHEESE_PYTEST_TEMP_KEEP=0 pytest_temp_candidates | sort)"
  want="$(printf '%s\n%s\n' "$probe_root/pytest-of-selftest/pytest-2" \
                            "$probe_root/pytest-of-selftest/pytest-3")"
  test "$out" = "$want" \
    || { printf 'self-test FAIL: age window -> %s\nwant %s\n' "$out" "$want"; exit 1; }
  unset CHEESE_TEMP_ROOTS
  printf 'self-test OK\n'
  exit 0
}

test "$SELF_TEST" -eq 1 && self_test

docker_df_load

# The thing the nightly timer asks, and the reason it lives in this script: a
# condition down in the unit file can only ask about one filesystem, and the one
# that fills first here is not the one it was asking about.
if [ "$NEEDED_ONLY" -eq 1 ]; then
  needed
  exit $?
fi

BEFORE=""
printf 'filesystems watched:\n'
for mount in $(watched_mounts); do
  printf '  %-34s %s%% used\n' "$mount" "$(pct_of "$mount")"
  BEFORE="$BEFORE $mount=$(pct_of "$mount")"
done
if [ "$APPLY" -eq 1 ]; then printf 'reclaiming:\n'; else printf 'reclaimable (dry run — pass --apply to delete):\n'; fi

plan "docker build cache" "$(docker_build_cache)" docker builder prune -f
plan "docker unused volumes" "$(docker_unused_volumes)" docker volume prune -f
plan "apt archives" "$(size_of /var/cache/apt/archives)" sudo apt-get clean
plan "go build cache" "$(size_of "$HOME/.cache/go-build")" go clean -cache
plan "rust toolchain downloads" "$(size_of "$HOME/.cache/puccinialin")" rm -rf "$HOME/.cache/puccinialin"
plan "pip cache" "$(size_of "$HOME/.cache/pip")" rm -rf "$HOME/.cache/pip"
plan "vscode VSIX cache" "$(size_of "$HOME/.vscode-server/data/CachedExtensionVSIXs")" \
  rm -rf "$HOME/.vscode-server/data/CachedExtensionVSIXs"
plan "vscode old servers" "$(vscode_servers_reclaimable)" prune_vscode_servers
plan "stale pytest temp trees" "$(pytest_temp_reclaimable)" prune_pytest_temps

printf '  %-34s %8s\n' "TOTAL" "$(human "$TOTAL")"

if [ "$APPLY" -eq 1 ]; then
  # Journals and the uv cache are pruned rather than deleted: both keep entries
  # that are still referenced, and both have a first-class subcommand for it.
  journalctl --vacuum-size=32M >/dev/null 2>&1 || sudo journalctl --vacuum-size=32M >/dev/null 2>&1 || true
  command -v uv >/dev/null 2>&1 && uv cache prune >/dev/null 2>&1 || true
  # Every filesystem that was reported at the top, so the log says which one the
  # run bought room on — the whole bug was a report that only ever named one.
  for mount in $(watched_mounts); do
    was="$(printf '%s' "$BEFORE" | tr ' ' '\n' | sed -n "s|^$mount=||p")"
    printf '%s: %s%% used -> %s%% used\n' "$mount" "${was:--}" "$(pct_of "$mount")"
  done
fi
