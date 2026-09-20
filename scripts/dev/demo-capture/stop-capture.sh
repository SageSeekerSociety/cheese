#!/usr/bin/env bash
# Stop a capture started by start-capture.sh, so the take gets its stop record.
# Usage: stop-capture.sh <take-name> [recordings-root]
set -euo pipefail
take="$1"
root="${2:-$HOME/cheese-recordings}"
[[ "$take" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo 'Take name must be lowercase letters, digits and dashes.' >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
capture_pid="$(cat "$root/logs/$take.pid")"
command="$(ps -p "$capture_pid" -o args= || true)"
case "$command" in
  # Only kill a process that is this capture for this take; a recycled PID is left alone.
  *"capture-stream.mjs"*"$root/$take"*) kill -TERM "$capture_pid" ;;
  '') : ;;
  *) echo 'Unexpected capture process; left running.' >&2; exit 1 ;;
esac
