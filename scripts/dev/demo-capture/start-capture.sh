#!/usr/bin/env bash
# Start capturing the agent-browser frame stream into a take directory.
# Usage: start-capture.sh <stream-port> <take-name> [recordings-root]
# Prints the capture PID; stop it with stop-capture.sh using the same take name.
set -euo pipefail
port="$1"
take="$2"
root="${3:-$HOME/cheese-recordings}"
[[ "$port" =~ ^[0-9]+$ ]] || { echo 'Port must be numeric.' >&2; exit 1; }
[[ "$take" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo 'Take name must be lowercase letters, digits and dashes.' >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$root/logs"
# Refuse to write into an existing take: frames and frames.jsonl must stay one recording.
[[ ! -e "$root/$take" && ! -e "$root/logs/$take.pid" ]] || { echo "Take '$take' already exists under $root." >&2; exit 1; }
nohup node "$here/capture-stream.mjs" "$port" "$root/$take" > "$root/logs/$take.log" 2>&1 < /dev/null &
capture_pid=$!
echo "$capture_pid" > "$root/logs/$take.pid"
echo "$capture_pid"
