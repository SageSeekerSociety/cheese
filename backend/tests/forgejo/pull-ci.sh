#!/usr/bin/env bash
# Internal Linux CI helper: retry only the complete HTTP 503 diagnostic observed
# from Compose. A new/unknown diagnostic fails closed rather than broadening retry.
set -euo pipefail
umask 077

if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_OS:-}" != Linux || $# == 0 ]]; then
  echo "Forge fixture pull retry requires GitHub Actions on Linux and a Compose command" >&2
  exit 2
fi
for tool in timeout awk mktemp cat rm sleep "$1"; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Forge fixture CI pull requires $tool" >&2
    exit 2
  fi
done
if [[ "$(timeout --version 2>/dev/null)" != *"GNU coreutils"* ]]; then
  echo "Forge fixture CI pull requires GNU timeout" >&2
  exit 2
fi
pull_log="$(mktemp)" || exit 2
trap 'rm -f "$pull_log"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

only_http_503() {
  # Ignore known plain progress, but require every other nonempty line to be
  # a complete, recognized 503 terminal error. Do not grep for a 503 substring:
  # progress byte counts, mixed failures and truncated messages are not proof.
  LC_ALL=C awk '
    {
      gsub(/\033\[[0-?]*[ -/]*[@-~]/, "")
      gsub(/\r/, "")
      sub(/^[ \t]+/, "")
      sub(/[ \t]+$/, "")
    }
    /^$/ { next }
    /^(forgejo|snapshots) Error received unexpected HTTP status: 503 Service Unavailable$/ {
      errors++; next
    }
    /^Error response from daemon: received unexpected HTTP status: 503 Service Unavailable$/ {
      errors++; next
    }
    /^(forgejo|snapshots) (Pulling|Pulled)$/ { next }
    /^[0-9a-f]+ (Pulling fs layer|Waiting|Already exists|Download complete|Verifying Checksum|Pull complete)$/ {
      next
    }
    /^[0-9a-f]+ (Downloading|Extracting) \[[= >]*\][ \t]+[0-9.]+(B|kB|MB|GB)\/[0-9.]+(B|kB|MB|GB)$/ {
      next
    }
    { unknown = 1 }
    END { exit (errors > 0 && !unknown ? 0 : 1) }
  ' "$pull_log"
}

for attempt in 1 2 3; do
  # 3 * (120s + 5s kill grace) + 10s + 20s = at most 405s of client pull
  # and backoff. Timing out a client does not prove the daemon stopped work;
  # any timeout/signal terminates this path without starting another pull.
  if timeout --kill-after=5s 120s "$@" --ansi never --progress plain pull --policy missing \
    > "$pull_log" 2>&1; then
    status=0
  else
    status=$?
  fi
  cat "$pull_log" || exit 2
  if [[ "$status" == 0 ]]; then exit 0; fi
  if [[ "$status" != 1 ]] || ! only_http_503 || [[ "$attempt" == 3 ]]; then
    exit "$status"
  fi
  delay=$((attempt * 10))
  echo "Forge fixture pull attempt $attempt failed with only HTTP 503; retrying in ${delay}s" >&2
  sleep "$delay"
done
