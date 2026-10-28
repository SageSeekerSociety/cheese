#!/usr/bin/env bash
# Install scripts/remote_execution's pinned Claude Code and prove it runs.
#
# `npm ci` can succeed and still leave a `claude` the machine cannot execute:
# on 2026-09-26 a remote acceptance on cheese-ci-runner-3b died with
# "Exec format error: .../node_modules/.bin/claude". The package ships
# `bin/claude.exe` as a shebang-less placeholder script, and its postinstall
# replaces it with the binary from the platform's optional dependency
# (`@anthropic-ai/claude-code-linux-x64`). When that download fails, npm skips
# the optional dependency without an error — that run logged "added 1 package
# in 3m" where a good install adds 2 in seconds — and the placeholder stays.
# `npm ci` alone does not notice, and the job then dies before a single case
# runs. So: install, run `claude --version`,
# and if it does not run, install once more from an empty cache. A second
# failure is a real problem and fails the step with the output that says why.
set -euo pipefail

prefix="${1:-scripts/remote_execution}"
bin="$prefix/node_modules/.bin/claude"

runs() { "$bin" --version >/dev/null 2>&1; }

npm ci --prefix "$prefix" --no-audit --no-fund
if runs; then exit 0; fi

echo "::warning::$bin does not run on this machine; reinstalling from an empty npm cache"
rm -rf "$prefix/node_modules"
fresh_cache="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/npm-cache.XXXXXX")"
trap 'rm -rf "$fresh_cache"' EXIT
npm ci --prefix "$prefix" --no-audit --no-fund --prefer-online --cache "$fresh_cache"
"$bin" --version
