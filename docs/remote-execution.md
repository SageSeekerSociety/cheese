# Central sessions and remote execution

Claude Code runs on the central host. Its native file tools and shell commands execute through a persistent service on the assigned machine. Custom stdio MCP servers also run there, with their configured environment. Cheese RC remains attached to the central terminal; its file previews, diffs and shell task controls go to the executor.

Central sessions require Claude Code 2.1.277. They use the function hook interface enabled by `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS`, so upgrading Claude Code requires rerunning acceptance. The executor runs both native file operations and shell commands through the same pinned build's `claude mcp serve`, so a command's working directory, its return to the workspace root after leaving it, and the text a failure comes back as are that build's own. Four of the behaviours this relies on are in no published contract — a `cd` outliving its call, the workspace-root return, the disk path a backgrounded command's output lands on, and the build offering no way back to a backgrounded task from serve mode (`TaskStop` answers `No task found`, and 2.1.277 stopped serving `TaskOutput` there) — so `scripts/remote_execution/mcp_contract.py` checks them: CI runs it against the pinned build on changes here and against the newest published build daily. The executor reads a backgrounded command's output from disk and stops a command by the marker it puts in the shell's argv. The wait is the executor's own: the serve call carries the largest timeout there is, because at the build's own foreground timeout a command is killed or backgrounded depending on whether it printed anything right at the start, a rule written down nowhere; the agent's timeout, the room's move-to-background and interrupt are all decided by the executor no longer waiting for an answer the command goes on producing.

## Room placement

`AGENT_SESSION_DEVICE_ID` names the enrolled device that hosts Claude Code and RC. Both private chats and ordinary rooms require it. The backend records the session host and execution target with the room's resource generation. Changing the setting affects newly placed rooms; existing rooms retain their recorded host.

An ordinary room keeps its selected Cloud or enrolled execution device. The backend prepares its room directory, environment scripts, Cheese CLI, preview helper and executor through the existing connector. `cheese worktree` creates a separate checkout for each task on that device. Tool calls travel from the central session through a scoped backend endpoint to that connector. The endpoint resolves the destination from stored placement. The central host and ordinary execution device currently require distinct device identities and storage; private execution containers can share the central host.

An existing session moves on its next opening. The backend requests exit from the old Claude Code process, copies and verifies its original conversation files, then resumes on the central host. Source files remain available. Missing history, an offline host or a changed resource generation prevents the opening. Recovery can finish consuming a turn that began before migration.

Before deployment, connect the configured session host and install the private executor image described in `where-a-turn-runs.md`. Ordinary executors obtain the pinned binary from the backend's Claude Code download endpoint. Pool scheduling remains a design plan in RFC #927.

## Executor upgrades

Ordinary room executors use versioned directories for the runtime and platform helpers. New environments start with the current release. When an existing environment is prepared, a changed release is staged separately. If a tool call or background command is still running, preparation returns the existing executor with `upgrade_pending: true`; the next preparation retries the upgrade. The executor keeps accepting work while the upgrade is deferred.

Once idle, the executor stops accepting new work before it is replaced. Task inspection and cancellation remain available during that interval. Completed request receipts and task output stay in the same state directory. If installation fails after admission closes, repeating preparation completes the switch or restarts the previously selected release. Running shells and MCP processes are not transferred between releases. Dependency configuration changes require resetting or recreating the execution environment.

This upgrade path applies to ordinary room executors. Private execution containers retain their existing lifecycle.

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

The device-launcher and ordinary-room cases require Linux amd64, Docker, and access to GHCR. They use the released owner image and packaged Go connector pinned in `scripts/remote_execution/owner_fixture.py`, with an isolated database upgraded to the current schema. CI supplies the same image and revision through `ACCEPTANCE_OWNER_IMAGE` and `ACCEPTANCE_OWNER_REVISION`, and assigns `ACCEPTANCE_OWNER_PORT` per runner slot. Suite receipts include the selected image, revision, and shared transport source hashes.

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

With `--ssh`, executor calls retain the SSH transport; launcher uploads still cross the released owner and Go connector on the central host. The local CI cases exercise the owner-to-connector execution path.

`scripts/remote_execution/real_model.py` accepts the same SSH and output arguments. It uses an existing local Claude subscription from the normal credentials file or macOS Keychain and spends model quota. It runs the interactive terminal without registering a Remote Control session with Claude's hosted service. Credentials are passed in process environment and excluded from test receipts.

## Standalone SSH acceptance

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

This target is for the standalone acceptance launcher. Application rooms use their stored placement and connector transport automatically.

## Headless stream-json contract

The Claude Code driver planned in #1606 runs `claude -p --input-format stream-json --output-format stream-json --verbose --permission-mode bypassPermissions --permission-prompt-tool stdio` and keeps its stdin open for the whole session. The protocol on those two pipes is unpublished, so `scripts/remote_execution/headless_contract.py` checks the parts the driver depends on. It runs the given build against `model_fixture.py`, with an isolated `HOME` and `CLAUDE_CONFIG_DIR`, the workspace under that `HOME`, and `--setting-sources user`. `.github/workflows/mcp-contract.yml` runs it beside `mcp_contract.py`: against the pinned build when the contract or its fixtures change, and against the newest published build every day.

| Pinned behaviour | Why the driver needs it |
| --- | --- |
| `control_request` on stdin answers `initialize`, `interrupt`, `background_tasks`, `stop_task`, `set_model`, `set_permission_mode`, `set_max_thinking_tokens`, `apply_flag_settings`, `rename_session`, `file_suggestions`, `read_file`, `get_workspace_diff`, `get_context_usage`, `get_usage`, `mcp_status` and `mcp_reconnect`; `set_model` changes the model the next turn requests; `set_color` returns `Unsupported control request subtype` | The room's controls are written to stdin, replacing RC |
| `background_tasks` with a running tool's `tool_use_id`, or with no id, moves a foreground Bash or Agent to the background: `task_updated` reports `is_backgrounded: true`, `background_tasks_changed` is emitted, and the turn's `result` arrives while the task is still running. On completion, a `task_notification` with the task's `tool_use_id` starts a follow-up turn, which ends in its own `result` | Move-to-background, and tracking background work until it finishes |
| `stop_task` fails a foreground tool (`is_error` tool result) and the turn continues; on a background task it produces a `task_notification` with status `stopped`. `interrupt` ends the turn with an `error_during_execution` result | Stop and interrupt |
| A user message written to stdin while a tool runs reaches the model inside the next `tool_result`, as "The user sent a new message while you were working". `--replay-user-messages` echoes it (`isReplay`) only after that tool result | Mid-turn messages and their delivery receipts |
| Under the driver's flags, Bash and writes under `.claude/` run with no permission request, while AskUserQuestion arrives as `can_use_tool` with `requires_user_interaction`. Answering it with `behavior: allow` and `updatedInput.answers` passes the answer to the model. With `--dangerously-skip-permissions` alone, AskUserQuestion is not offered at all | Ordinary tools never wait for a person; questions still reach the room |
| An Agent call starts as a background task unless `run_in_background: false`. Its tool result carries `agentId`, and its messages also appear on stdout with `parent_tool_use_id` set. Its transcript is `subagents/agent-<agentId>.jsonl` beside a `.meta.json`; a workflow's agents are under `subagents/workflows/wf_*/` | Attaching subagent actions to their card |
| Hooks in `$CLAUDE_CONFIG_DIR/settings.json` fire in `-p` mode: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop and SessionEnd | Hooks kept for context injection |
| A turn ends in one `result`, and `is_error` marks failure. A 401 from the API is first reported as `system/api_retry` with `error_status: 401`, and is retried (10 times by default). The turn then ends with `is_error: true`, `terminal_reason: api_error` and `subtype: success`, after an assistant message whose `error` is `authentication_failed`. The process survives | Turn end and failure come from `is_error`, never from `subtype` |
| After a `result`, the process keeps reading stdin and background tasks keep running. Closing stdin exits with status 0 and kills its background tasks | Session lifetime is the driver holding stdin |

When a check fails against a new release, the driver needs to change before that release is pinned.

## Boundaries

The central session mirrors root project instructions and `.claude` assets. Project command hooks execute on the remote host; platform hooks execute centrally. Native execution is denied centrally when the function hook cannot return a remote result. Completed tool requests retain their receipts across executor restarts. A request interrupted before its receipt is stored has an unknown outcome and is not replayed automatically.

The current acceptance covers root instructions, imported instruction files and project skills. Nested instruction discovery, attachments, agent teams, non-command hooks and MCP sampling or elicitation are outside this acceptance. Custom MCP configuration currently accepts stdio servers. Subagents run their tools on the executor like the main thread; a subagent requested with `isolation` is refused centrally, because both modes build on the session host, and a separate checkout comes from `cheese split`. The experiment is not a security boundary for hostile plugins or an untrusted central process.

Each execution state directory belongs to one room resource generation. The remote service listens on a Unix socket restricted to its OS user. Application rooms use the device connector; standalone acceptance can use SSH. Stop a standalone service with `python3 /srv/tools/runtime.py stop --state /srv/cheese-session`; it stops running shell process groups and MCP subprocesses.
