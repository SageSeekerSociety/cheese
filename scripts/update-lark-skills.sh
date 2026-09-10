#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# The installer replaces skill directories; keep local edits in git first.
if [ -n "$(git status --porcelain -- '.agents/skills/lark-*' '.claude/skills/lark-*' skills-lock.json)" ]; then
  echo 'Commit local Lark skill changes before updating.' >&2
  exit 1
fi

# Project scope is deliberate: global installation exposes these skills in every repository.
export DISABLE_TELEMETRY=1
npx --yes skills@1.5.25 add https://open.feishu.cn/lark-cli/skills/regular \
  --skill '*' --agent claude-code codex --yes
