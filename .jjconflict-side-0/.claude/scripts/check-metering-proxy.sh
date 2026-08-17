#!/usr/bin/env bash
# check-metering-proxy.sh — the subscription metering proxy must stay
# credential-hardened. The durable Claude-subscription credential is a one-year,
# NON-refreshing `claude setup-token` (CODEX findings / issue #388). This guard
# fails the build if the deployment config under deploy/ reintroduces any of the
# retired mechanisms:
#
#   1. an OAuth refresh command (print-mode `claude -p`, a
#      CLAUDE_CODE_OAUTH_REFRESH_TOKEN exchange, or a raw /v1/oauth/token call),
#   2. a Claude Code login credential JSON (`.credentials.json`) as the source,
#   3. an enabled credential-refresh daemon unit (`creds-daemon`).
#
# WHY, not taste: the outage came from treating a human `/login` access token as
# a service credential and standing up a local "refresh near expiry" daemon that
# raced its own OAuth refresh chain and poisoned it. A setup-token does not
# refresh, so none of the above may exist; a rule that only an agent's diligence
# enforces decays, so this moves it to "the build is red".
#
# Scope is deploy/ — the deployment surface. This SCRIPT necessarily names the
# forbidden strings (its patterns, its self-test), and the corrected docs under
# deploy/ describe the RIGHT mechanism without naming the retired ones, so a
# clean tree has zero hits. A `systemctl mask` line is the one allowed mention of
# the daemon: masking is how a box keeps a leftover unit un-startable.
#
# Usage: check-metering-proxy.sh [root]        check a tree (default: repo root)
#        check-metering-proxy.sh --self-test   prove each rule fires and is scoped
set -euo pipefail

SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }
ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
FAILED=0

report() {
  local rule="$1" fix="$2" hits="$3"
  printf '%s\n' "$hits" | sed 's/^/  /'
  echo "  -> $fix"
  echo "::error::$rule"
  FAILED=1
}

# Rule 1 — no OAuth refresh command. A setup-token is durable and does not
# refresh; any print-mode `claude -p` (the call the old creds daemon used to
# trigger a refresh), a CLAUDE_CODE_OAUTH_REFRESH_TOKEN exchange, or a raw
# /v1/oauth/token call reintroduces the rotating-refresh credential class that
# caused the outage.
check_no_refresh_command() {
  local dir="$ROOT/deploy" hits
  [ -d "$dir" ] || return 0
  hits="$(grep -rnE 'claude[[:space:]]+-p([[:space:]]|$)|CLAUDE_CODE_OAUTH_REFRESH_TOKEN|/v1/oauth/token' \
    "$dir" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: an OAuth refresh command in the metering-proxy deployment"
  report "metering proxy must not refresh — the setup-token is durable, non-refreshing" \
    "delete the refresh call; rotation is a planned manual setup-token swap, no daemon" \
    "$hits"
}

# Rule 2 — no login credential JSON as the source. `.credentials.json` is Claude
# Code's HUMAN /login credential store; the durable service credential is a
# setup-token held in the injector directory, never the login JSON.
check_no_credentials_json() {
  local dir="$ROOT/deploy" hits
  [ -d "$dir" ] || return 0
  hits="$(grep -rn '\.credentials\.json' "$dir" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: .credentials.json referenced as a credential source"
  report "the durable credential is a setup-token, not the /login credential JSON" \
    "point the injector at the durable setup-token; do not source the login JSON" \
    "$hits"
}

# Rule 3 — no enabled credential-refresh daemon. The retired daemon must not
# reappear as a unit FILE under deploy/, nor be NAMED anywhere in the deploy
# tree — except on a `systemctl mask` (or "masked") line, which is how a box
# neutralizes a leftover unit rather than enabling it.
check_no_creds_daemon() {
  local dir="$ROOT/deploy" name_hits content_hits hits
  [ -d "$dir" ] || return 0
  name_hits="$(find "$dir" -type f -iname '*creds*daemon*' 2>/dev/null || true)"
  content_hits="$(grep -rnE 'creds[-_]daemon' "$dir" 2>/dev/null \
    | grep -viE 'systemctl[[:space:]]+mask|masked' || true)"
  hits="$(printf '%s\n%s\n' "$name_hits" "$content_hits" | grep -v '^[[:space:]]*$' || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: a credential-refresh daemon in the deployment config"
  report "the credential-refresh daemon is retired — no enabled unit may return" \
    "remove the unit; if a box still carries it, mask it (systemctl mask) so it can't start" \
    "$hits"
}

run_all() {
  check_no_refresh_command
  check_no_credentials_json
  check_no_creds_daemon
}

# --- self-test -------------------------------------------------------------
# A guard nobody has seen fail is a guard nobody knows works. Each case builds a
# tiny tree, runs this same script against it, and asserts the verdict.
if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  mkdir -p "$tmp/deploy/metering-proxy" "$tmp/deploy/systemd"
  me="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

  # A clean deploy tree (the hardened config) passes.
  printf 'CHEESE_INJECT_TOKEN: /etc/cheese/secrets/inject.token\n' \
    > "$tmp/deploy/metering-proxy/compose.yml"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a clean deploy tree must pass"

  # Rule 1 — an OAuth refresh command must fail, in each of its forms.
  printf 'ExecStart=/usr/bin/claude -p "keep it warm"\n' > "$tmp/deploy/refresh.sh"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "print-mode claude -p must fail"
  printf 'Environment=CLAUDE_CODE_OAUTH_REFRESH_TOKEN=x\n' > "$tmp/deploy/refresh.sh"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a refresh-token exchange must fail"
  printf 'curl https://api.anthropic.com/v1/oauth/token\n' > "$tmp/deploy/refresh.sh"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a raw /v1/oauth/token call must fail"
  rm "$tmp/deploy/refresh.sh"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "removing the refresh call must restore green"

  # Rule 2 — the login credential JSON as a source must fail.
  printf 'INJECT_SOURCE=/home/box/.claude/.credentials.json\n' > "$tmp/deploy/creds.env"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "sourcing the login credential JSON must fail"
  rm "$tmp/deploy/creds.env"

  # Rule 3 — an enabled creds-daemon unit must fail, whether caught by its file
  # name or by being named in an enabling context.
  printf '[Service]\nExecStart=/usr/local/bin/cheese-creds-daemon\n' \
    > "$tmp/deploy/systemd/cheese-creds-daemon.service"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a creds-daemon unit must fail"
  rm "$tmp/deploy/systemd/cheese-creds-daemon.service"
  # ...but a `systemctl mask` line in a deploy script (how a box neutralizes a
  # leftover unit) must pass — masking keeps it un-startable, not enabled.
  printf '#!/bin/bash\nsystemctl mask cheese-creds-daemon.service\n' \
    > "$tmp/deploy/neutralize.sh"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a systemctl mask line must pass"
  rm "$tmp/deploy/neutralize.sh"

  # Scoping — the same forbidden strings OUTSIDE deploy/ (this guard's own
  # patterns, a findings doc, the addon's neighbouring code) must not fire.
  mkdir -p "$tmp/.claude/scripts" "$tmp/scratchpad"
  printf 'grep claude -p .credentials.json creds-daemon CLAUDE_CODE_OAUTH_REFRESH_TOKEN\n' \
    > "$tmp/.claude/scripts/check-metering-proxy.sh"
  printf 'the old creds-daemon ran claude -p against .credentials.json\n' \
    > "$tmp/scratchpad/findings.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "forbidden strings outside deploy/ must not fire"

  echo "PASS: check-metering-proxy self-test (refresh cmd, login JSON, creds-daemon; mask + scope verified)"
  exit 0
fi

run_all
if [ "$FAILED" = 1 ]; then
  echo ""
  echo "The durable subscription credential is a one-year, non-refreshing setup-token"
  echo "(CODEX findings / #388): no refresh loop, no login-JSON source, no creds daemon."
  exit 1
fi
echo "PASS: metering proxy stays credential-hardened (no refresh command, no login JSON, no creds daemon)"
