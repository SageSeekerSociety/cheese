# Remote execution experiment

Claude Code runs on the central host. Its native file tools and shell commands execute through a persistent service on the assigned machine. Custom stdio MCP servers also run there, with their configured environment. Cheese RC remains attached to the central terminal; its file previews, diffs and shell task controls go to the executor.

The experiment requires Claude Code 2.1.265 on the central host. It uses the function hook interface enabled by `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS`, so upgrading Claude Code requires rerunning acceptance. The executor uses `claude mcp serve` for native file operations and owns shell processes itself. It does not run a model loop.

## Acceptance

From the repository root, install the locked dependencies:

```sh
uv sync --project backend --locked
npm ci --prefix scripts/remote_execution --no-audit --no-fund
```

Install `tmux`, `redis-server`, `openssl`, `ripgrep` and Git through the host package manager. Run:

```sh
backend/.venv/bin/python scripts/remote_execution/suite.py \
  --claude "$PWD/scripts/remote_execution/node_modules/.bin/claude" \
  --output tmp/remote-execution/acceptance-1
```

The suite runs the native terminal with deterministic model responses and a loopback RC service. It checks file operations, commands, custom MCP, project instructions, dynamic skill commands, remote background output and cancellation, request replay, platform hook events and RC file controls. Failure cases disable the plugin, throw from it, stall it and disconnect the executor. All must leave the central sentinel files unchanged.

Each case records its inputs, logs and result. Rerunning with the same output directory resumes passed cases only when source hashes and versions match. Changed inputs require a new directory. GitHub Actions runs the same suite on the experimental branch and uploads the receipts.

For two physical machines, configure an SSH alias that supports unattended authentication, then run:

```sh
backend/.venv/bin/python scripts/remote_execution/acceptance.py \
  --output tmp/remote-execution/cross-host-1 \
  --launcher device --rc \
  --ssh execution-host \
  --remote-root /srv/cheese-execution-tests \
  --remote-claude /opt/claude/bin/claude
```

`remote-root` is a writable scratch directory on the executor. Each test creates a new workspace beneath it. Python 3.9 or later, `ripgrep` and Claude Code must already be installed there. The central process stays local.

`scripts/remote_execution/real_model.py` accepts the same SSH and output arguments. It uses an existing local Claude subscription from the normal credentials file or macOS Keychain and spends model quota. It runs the interactive terminal without registering a Remote Control session with Claude's hosted service. Credentials are passed in process environment and excluded from test receipts.

## Experimental configuration

Copy `backend/app/domain/agent/harness/claude_code/remote_execution/runtime.py` to the execution host. Start it with a JSON configuration file on standard input:

```json
{
  "workspace": "/srv/project",
  "claude": "/opt/claude/bin/claude",
  "env": {"PROJECT_ENV": "test"},
  "mcp_servers": {
    "project": {
      "command": "python3",
      "args": ["/srv/tools/project_mcp.py"],
      "env": {}
    }
  }
}
```

```sh
python3 /srv/tools/runtime.py start --state /srv/cheese-session < executor.json
```

Use this target on both the backend and the central terminal host. Each host must resolve the SSH alias and have access to the execution host:

```json
{
  "ssh": "execution-host",
  "command": ["python3", "/srv/tools/runtime.py"],
  "state": "/srv/cheese-session",
  "mcp_servers": ["project"]
}
```

For the existing Cheese device launcher, put that target under the topic UUID in `settings.agent_execution_targets`, loaded from `AGENT_EXECUTION_TARGETS` in the backend configuration. The executor's workspace and environment must be prepared before launching the central session. The launcher skips project checkout and environment preparation on the central host for that topic. Removing the topic entry restores the existing placement on its next launch.

## Boundaries

The central session mirrors root project instructions and `.claude` assets. Project command hooks execute on the remote host; platform hooks execute centrally. Native execution is denied centrally when the function hook cannot return a remote result. Completed tool requests retain their receipts across executor restarts. A request interrupted before its receipt is stored has an unknown outcome and is not replayed automatically.

The current acceptance covers root instructions, imported instruction files and project skills. Nested instruction discovery, attachments, agent teams, non-command hooks and MCP sampling or elicitation are outside this acceptance. Custom MCP configuration currently accepts stdio servers. The experiment is not a security boundary for hostile plugins or an untrusted central process.

Each execution state directory belongs to one session. The remote service listens on a Unix socket restricted to its OS user. SSH carries requests across hosts. Stop the service with `python3 /srv/tools/runtime.py stop --state /srv/cheese-session`; it stops running shell process groups and MCP subprocesses.
