#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# The installer replaces skill directories; keep local edits in git first.
if [ -n "$(git status --porcelain -- '.agents/skills/lark-*' '.claude/skills/lark-*' skills-lock.json)" ]; then
  echo 'Commit local Lark skill changes before updating.' >&2
  exit 1
fi

# Only the skills this repository uses. The feed serves the whole official
# collection; installing a skill nobody calls still puts its name and description
# in front of every agent on every turn. This project reads the Feishu wiki, docs
# and Drive through the CLI, so those five are enough. Add a name here to adopt
# another one. The installer only adds and updates: a skill dropped from this
# list stays installed until its directories and lock entry are removed too.
RETAINED_SKILLS=(
  lark-shared
  lark-wiki
  lark-doc
  lark-drive
  lark-openapi-explorer
)

skill_args=()
for skill in "${RETAINED_SKILLS[@]}"; do
  skill_args+=(--skill "$skill")
done

# Project scope is deliberate: global installation exposes these skills in every repository.
export DISABLE_TELEMETRY=1
npx --yes skills@1.5.25 add https://open.feishu.cn/lark-cli/skills/regular \
  "${skill_args[@]}" --agent claude-code codex --yes
