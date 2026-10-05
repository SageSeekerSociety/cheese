#!/usr/bin/env bash
# Connects this machine to the team's Feishu through lark-cli: installs the CLI,
# registers the shared `cheese` app, and logs you in as yourself. Run it once per
# machine; it stops as soon as Feishu already answers through `--profile cheese`.
set -euo pipefail

# The app id is a public client id; its secret is not, and stays out of git.
APP_ID=cli_a97ca79454785bd5

if ! command -v lark-cli >/dev/null; then
  npm install -g @larksuite/cli
fi

if lark-cli contact +get-user --profile cheese >/dev/null 2>&1; then
  echo "Feishu answers through --profile cheese; nothing to do."
  exit 0
fi

if ! lark-cli config show --profile cheese >/dev/null 2>&1; then
  echo "Paste the cheese app secret (a teammate sends it privately), then press Enter:" >&2
  lark-cli config init --name cheese --app-id "$APP_ID" --app-secret-stdin
fi

# Opens a browser for your own Feishu account.
lark-cli auth login --profile cheese --recommend
lark-cli contact +get-user --profile cheese >/dev/null
echo "Feishu answers through --profile cheese."
