#!/usr/bin/env bash
# Provisioning installs the files that sit beside it, from wherever it is invoked.
#
# It cds into the runner root early, so anything it reaches for by `$0` is looked
# up in the wrong place from then on: a real provision of a second slot got as far
# as "Settings Saved" and then died on `install: cannot stat
# './job-started-hook.sh'`, leaving a registered runner with no hook, no slot and
# no service.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheese-ci-provision-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

BUNDLE="$RUN_DIR/bundle"
HOME_DIR="$RUN_DIR/home"
FAKE_BIN="$RUN_DIR/bin"
mkdir -p "$BUNDLE" "$HOME_DIR" "$FAKE_BIN"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cp "$ROOT/deploy/ci-runner/provision.sh" "$BUNDLE/"
for name in job-started-hook.sh disk-guard.sh resident-services.sh; do
  printf '#!/bin/sh\nexit 0\n' > "$BUNDLE/$name"
  chmod +x "$BUNDLE/$name"
done

# The runner tarball, `sudo`, systemd and docker are not this test's subject.
for name in sudo systemctl docker curl tar nproc; do
  printf '#!/bin/sh\nexit 0\n' > "$FAKE_BIN/$name"
  chmod +x "$FAKE_BIN/$name"
done
printf '#!/bin/sh\nprintf "1\\n"\n' > "$FAKE_BIN/nproc"
chmod +x "$FAKE_BIN/nproc"

# `config.sh` and `svc.sh` belong to the runner tarball the fake curl did not fetch.
seed_runner_root() {
  mkdir -p "$1"
  printf '#!/bin/sh\necho "√ Settings Saved."\n' > "$1/config.sh"
  printf '#!/bin/sh\nexit 0\n' > "$1/svc.sh"
  mkdir -p "$1/bin"
  printf '#!/bin/sh\nexit 0\n' > "$1/bin/installdependencies.sh"
  chmod +x "$1/config.sh" "$1/svc.sh" "$1/bin/installdependencies.sh"
}
seed_runner_root "$HOME_DIR/actions-runner"
seed_runner_root "$HOME_DIR/actions-runner-1"

run_from_elsewhere() {
  ( cd "$RUN_DIR" && HOME="$HOME_DIR" PATH="$FAKE_BIN:$PATH" \
      bash "$BUNDLE/provision.sh" "$1" fake-token 2.337.0 "$2" >/dev/null 2>&1 )
}

# 1. The only slot: its own root, and the files beside the script land in it.
run_from_elsewhere cheese-ci-runner-9 0 || fail "provisioning slot 0 failed"
[ -x "$HOME_DIR/actions-runner/job-started-hook.sh" ] || fail "slot 0 has no job-started hook"
[ -x "$HOME_DIR/actions-runner/disk-guard.sh" ] || fail "slot 0 has no disk guard"
grep -q "CHEESE_CI_SLOT=0" "$HOME_DIR/actions-runner/.env" || fail "slot 0 did not declare its slot"

# 2. A second slot installs beside it, not over it, and declares its own number.
run_from_elsewhere cheese-ci-runner-9b 1 || fail "provisioning slot 1 failed"
[ -x "$HOME_DIR/actions-runner-1/job-started-hook.sh" ] || fail "slot 1 has no job-started hook"
grep -q "CHEESE_CI_SLOT=1" "$HOME_DIR/actions-runner-1/.env" || fail "slot 1 did not declare its slot"
grep -q "CHEESE_CI_SLOT=0" "$HOME_DIR/actions-runner/.env" || fail "slot 1 overwrote slot 0's"

# 3. A machine with two slots sizes pytest to half its cores instead of `auto`.
grep -q "CHEESE_CI_TEST_WORKERS=" "$HOME_DIR/actions-runner-1/.env" \
  || fail "a two-slot machine did not size its test workers"

# 4. Each slot's hook points at its OWN copy — slot 1 running slot 0's would
#    clean slot 0's workspace.
grep -q "ACTIONS_RUNNER_HOOK_JOB_STARTED=$HOME_DIR/actions-runner-1/job-started-hook.sh" \
  "$HOME_DIR/actions-runner-1/.env" || fail "slot 1 points at the wrong hook"

echo "ci-runner provisioning: all cases passed"
