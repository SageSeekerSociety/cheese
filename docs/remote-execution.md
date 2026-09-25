# Central sessions and remote execution

Claude Code runs on the central host. Its native file tools and shell commands execute through a persistent service on the assigned machine. Custom stdio MCP servers also run there, with their configured environment. Claude Code itself runs headless (`claude -p` over stream-json), held by a runner on the central host that owns its stdin and stdout for the life of the session and records every stdout line in a journal the backend mirrors. The room's controls reach the session as lines on that stdin, including moving a running command to the background and stopping a task; file previews and diffs go to the executor.

The platform's own tools are a constant MCP table on the central host (`PLATFORM_TOOLS` in `backend/sandbox/cheese`): chat, the living document, task cards, acceptance, memory, notifications and the rest of what needs only the backend. The central host calls the backend for them directly, so they stay listed and callable while the machine is out of reach. `cheese_doc_set` reads its file from the machine and `cheese_accept_request` first runs `cheese sync` there; when the machine is out of reach they answer so at once. The `cheese` CLI on the machine keeps only what has to run there as a process: task directories, sync and recovery, fix pushes, preview, workspace file conversion, library downloads and git credentials.

Central sessions require Claude Code 2.1.282. They use the function hook interface enabled by `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS`, so upgrading Claude Code requires rerunning acceptance. The plugin (`proxy.js`) runs the file tools — Read, Edit, Write, NotebookEdit — through the pinned build's `claude mcp serve` on the executor. Bash is not the plugin's: the build runs it itself, as its own tool with its own background tasks, and only the process moves to the executor (below).

## Shell commands

Every command the build starts — a Bash call, a hook, a stdio MCP server — goes through `CLAUDE_CODE_SHELL_PREFIX`, the `shell-prefix` script `client.prepare` writes. A command that matches one of the platform's own commands exactly (its hooks, the plugin's transport, bridge and guard) runs on the central host. Anything else, including a trusted command with anything appended, runs on the executor (`client.shell`, `runtime.py` `shell`), and the prefix process stands in for it towards the build: it prints the command's output as it is produced, ends with its exit status or the signal that killed it, and passes on the signal the build uses to stop it. So the build's own machinery applies unchanged: `run_in_background`, `task_started` and `task_notification`, the follow-up turn when a task finishes, `background_tasks` and `stop_task` on stdin, the model's TaskStop, interrupt, the Bash timeout, closing stdin, and output large enough to be persisted.

The session sees the project at the executor's own path. `client.prepare` launches the build through `client.py enter`, which gives the session a user and mount namespace of its own: its root is the central host's, every top-level entry bound into place, and the forwarded project view is mounted at exactly the path the executor holds the project at. The directories leading down to it are the session's own: what is already in them is the host's, and what the session creates directly in one, such as a socket in `/tmp` when the project is under it, stays in the session, as in a private `/tmp` (64 MiB). So every path the build prints — in what it tells the model about the session, when it refuses a command such as `rmdir` of its own directory, when it resets the shell's directory — is the one it prints running on the executor, and nothing respells anything. The namespace maps the connector user to itself, and its capabilities end when the build is started. The central host must therefore be Linux and allow unprivileged user namespaces (Debian does; Ubuntu 24.04 needs `kernel.apparmor_restrict_unprivileged_userns=0`). `enter` refuses a path it cannot place, loudly: one that is not absolute, one under `/proc`, `/sys` or `/dev`, and one that would cover a path the session itself needs (its session directory, config, temp, HOME, the helpers, Python or the build). A Windows executor's path cannot be a Linux path at all; the session sees it as Git Bash, the shell Claude Code runs commands in there, spells it (`C:\Users\x` is `/c/Users/x`), and the executor turns that back into its own spelling (`runtime.native_path`). A session started before its machine was rented (`kind: deferred`) sees the placeholder `/unavailable-project`: the machine, and the path it holds the project at, do not exist yet. Its first project tool rents the machine, and the lease points its calls there (`RemoteClient.call`): paths at the placeholder are sent as the machine's, and the directory a command ends in is reported at the placeholder. Once the lease is ready and the machine answers, the session's execution target names the machine's path, so its launch identity changes and the first turn that finds it idle relaunches it (see Central session upgrades). While the machine does not answer, the target stays the placeholder: the session keeps running as it is, its conversation and mapped commands working, and the relaunch waits for an idle turn that finds the machine, as it waits for a running task. A session that has to start again in that time starts at the placeholder too. A session already on the machine stays there when the machine goes out of reach. The relaunched session takes the machine through the lease before it starts (`client._take_leased_machine`) and starts as one on that machine would: at the machine's path, with the project's instructions, hooks and MCP servers. The conversation's transcript moves first from the directory the build keeps for the placeholder to the one it keeps for the machine's path (`client.project_dir`, held to the build by `headless_contract.py` `projectdirs`), since a resumed session goes on writing where it found it. A session is never started at the machine's path without the machine: if the lease cannot be taken at that moment after all, the start fails with the platform's reason.

A Bash command arrives wrapped by the build: `source <snapshot> && … eval '<command>' < /dev/null && pwd -P >| <cwd file>`. The snapshot is of the central host's shell, so the executor runs the body in the shell the same build would choose on the executor (`runtime.claude_shell`: `CLAUDE_CODE_SHELL`, then the machine's `SHELL` when it names bash or zsh, then zsh before bash), after that shell's snapshot, which the build writes the first time its `mcp serve` Bash runs with `CLAUDE_CODE_SHELL` set to it (`runtime.shell_snapshot`): aliases, functions, options and PATH are the executor's, as in a session running there, and without a snapshot the shell is a login shell, as the build starts it then. The directory the shell ended in comes back and is written to the build's cwd file, where the build finds it in the view, whose directories are looked up on the executor as they are asked for (`forwarded_fs.py`, `context_fs` `directory` and `list`); a directory outside the workspace is reported so that the build resets to the workspace root and says so, as it does natively. The one path the build reads from the central host and the executor has elsewhere is a skill's: skills are linked into the session's config directory, so a skill file named there is named in the project's `.claude/skills` when it reaches the executor.

The command belongs to the executor, not to the connection. Its output goes to files in the executor's state directory, and the prefix reads them from its own offsets, so when the link to the executor drops — a network fault, or a release of the device connection owner — the command keeps running and the prefix picks up at the byte it had reached, with nothing lost or repeated. A command nobody has read for ten minutes lost its session for good and is stopped (`COMMAND_ABANDONED_S`). A stop does not depend on the prefix living to send it: the build kills the prefix about a second after its TERM, so a watcher the prefix detaches before starting the command delivers the stop (TERM, then KILL after five seconds) when the prefix asks or dies without having seen the command end, and the executor keeps a stop that arrives before its command's start and refuses that start. The operations travel as the executor control `shell`, a method every owner image already admits.

The prefix's stdout and stderr are the command's, so nothing of the platform's is written to them while the command runs: what the transport logs on its way through a retry goes to `shell-prefix.log` in the session directory, and only a command that could not run at all ends with the platform's own error. The platform's own `cheese-sync` at a device session's Stop runs without the project's tool hooks, which never see it natively.

The prefix forwards only what the build sets for its shell children (`FORWARDED_ENV`) and `CLAUDE_PROJECT_DIR`; the rest of the central environment, credentials included, never leaves the central host. It writes onto the central host only the command's output, up to 256 MiB (`SHELL_OUTPUT_BYTES`), after which the command is stopped, and the directory, only into the build's own cwd file. A hook's input travels as its stdin, up to 16 MiB. Reading a background task's output file or a persisted result is a local Read on the central host, as in a native session; the PreToolUse guard admits no other native file operation there.

The project's own PreToolUse and PostToolUse command hooks are registered in the central session (`context_fs` `tree` carries them), so the build fires them for the calls it runs itself, with its own input, and their commands run on the executor through the prefix. The file tools the plugin runs keep their hooks on the executor (`runtime.hooks`).

`scripts/remote_execution/equivalence.py` checks that none of this is visible: it runs one scenario matrix against Claude Code running directly in the machine's project and against the room's launch with the executor over the device route, one after the other at the same paths, while the central view of the project lives elsewhere. The two records must be equal with no path rewritten; the only normalizations are values drawn at random (session and task ids, persisted-result names, timestamps), the session context the build attaches to a message, and the session host's own process variables, which are never forwarded. The matrix covers output, encodings, large output, directories and the build's own refusal, environment, the machine's shell setup, background work, a device that drops and comes back, stopping, interrupts and timeouts. CI runs it for a machine whose shell is bash and one whose shell is zsh, with the build contracts (`mcp-contract.yml`): against the pinned build on changes here and against the newest published build daily.

## Room placement

`AGENT_SESSION_DEVICE_ID` names the enrolled device that hosts Claude Code and its runner. Both private chats and ordinary rooms require it. The backend records the session host and execution target with the room's resource generation. Changing the setting affects newly placed rooms; existing rooms retain their recorded host.

An ordinary room keeps its selected Cloud or enrolled execution device. The backend prepares its room directory, environment scripts, Cheese CLI, preview helper and executor through the existing connector. `cheese worktree` creates a separate checkout for each task on that device. Tool calls travel from the central session through a scoped backend endpoint to that connector. The endpoint resolves the destination from stored placement. The central host and ordinary execution device currently require distinct device identities and storage; private execution containers can share the central host.

An existing session moves on its next opening. The backend requests exit from the old Claude Code process, copies and verifies its original conversation files, then resumes on the central host. Source files remain available. Missing history, an offline host or a changed resource generation prevents the opening. Recovery can finish consuming a turn that began before migration.

Before deployment, connect the configured session host and install the private executor image described in `where-a-turn-runs.md`. Ordinary executors obtain the pinned binary from the backend's Claude Code download endpoint. Pool scheduling remains a design plan in RFC #927.

## Executor upgrades

Ordinary room executors use versioned directories for the runtime and platform helpers. New environments start with the current release. When an existing environment is prepared, a changed release is staged separately. If a tool call or background command is still running, preparation returns the existing executor with `upgrade_pending: true`; the next preparation retries the upgrade. The executor keeps accepting work while the upgrade is deferred.

Once idle, the executor stops accepting new work before it is replaced. Task inspection and cancellation remain available during that interval. Completed request receipts and task output stay in the same state directory. If installation fails after admission closes, repeating preparation completes the switch or restarts the previously selected release. Running shells and MCP processes are not transferred between releases. Dependency configuration changes require resetting or recreating the execution environment.

This upgrade path applies to ordinary room executors. Private execution containers retain their existing lifecycle.

## Central session upgrades

At the start of every turn the backend compares a room's running session with the one it would start now (`_launch_identity` in `device_provider.py`). The comparison covers the agent configuration, the execution target, the platform's install root, the platform half of the launcher, and the harness's launch contract (`MachineLaunch.contract`). For Claude Code the contract is the entire launch script apart from the room's system prompt and the resume offer: the pinned build, the argv, the environment, the settings, the skills, the runner archive and the helpers that `client.prepare` builds the session from. The shell prefix, the launch environment, the argv and the MCP config are written only once, so if they change the session has to be replaced. For pi the contract is its launch without the opening.

If the two differ, the backend closes the session and opens a new one on the same room. The runner resumes the room's conversation from its transcript. Closing a session ends everything it is running, so the backend asks the runner first. While the runner reports a turn in progress or any running task (a background command, a subagent or a workflow), the current turn runs on the existing session and the relaunch waits for the first turn that finds nothing running. A runner that does not answer has nothing left to lose and is replaced.

The resident release (`release.py` `stage`) updates helpers in a session that stays open. It covers only the helpers listed in `RESIDENT`: `proxy.js`, which `/reload-plugins` loads again, and `context_service.py` and `cheese.py`, which only the prompt hook and the reconnected native MCP transport read. Every other helper, including any helper added later, is launch-only and part of the contract.

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

The device-launcher and ordinary-room cases require Linux amd64 and Docker. A runner without the pinned owner image also needs authenticated GHCR access. They use the released owner image and packaged Go connector pinned in `scripts/remote_execution/owner_fixture.py`, with an isolated database upgraded to the current schema. CI supplies the same image and revision through `ACCEPTANCE_OWNER_IMAGE` and `ACCEPTANCE_OWNER_REVISION`, and assigns `ACCEPTANCE_OWNER_PORT` per runner slot. Suite receipts include the selected image, revision, and shared transport source hashes.

The suite runs Claude Code under the runner, as a room does, with deterministic model responses. It checks file operations, commands, custom MCP, project instructions, dynamic skill commands, remote background output and cancellation, request replay, the runner's journal and the room's controls. Failure cases disable the plugin, throw from it, stall it and disconnect the executor. All must leave the central sentinel files unchanged.

Each case records its inputs, logs and result. Rerunning with the same output directory resumes passed cases only when source hashes and versions match. Changed inputs require a new directory. GitHub Actions runs the same suite on the experimental branch and uploads the receipts.

For two physical machines, configure an SSH alias that supports unattended authentication, then run:

```sh
backend/.venv/bin/python scripts/remote_execution/acceptance.py \
  --output tmp/remote-execution/cross-host-1 \
  --launcher device \
  --ssh execution-host \
  --remote-root /srv/cheese-execution-tests \
  --remote-claude /opt/claude/bin/claude
```

`remote-root` is a writable scratch directory on the executor. Each test creates a new workspace beneath it. Python 3.9 or later, `ripgrep` and Claude Code must already be installed there. The central process stays local.

With `--ssh`, executor calls retain the SSH transport; launcher uploads still cross the released owner and Go connector on the central host. The local CI cases exercise the owner-to-connector execution path.

`scripts/remote_execution/real_model.py` accepts the same SSH and output arguments. It uses an existing local Claude subscription from the normal credentials file or macOS Keychain and spends model quota. It runs the session under the runner, as a room does. Credentials are passed in process environment and excluded from test receipts.

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

Use this target on both the backend and the central session host. Each host must resolve the SSH alias and have access to the execution host:

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

A room's Claude Code runs as `claude -p --input-format stream-json --output-format stream-json --verbose --replay-user-messages --permission-mode bypassPermissions --disallowedTools AskUserQuestion` (`LAUNCH_ARGS` in `claude_code/cli.py`), and the runner keeps its stdin open for the whole session. The protocol on those two pipes is unpublished, so `scripts/remote_execution/headless_contract.py` checks the parts the runner depends on. It runs the given build against `model_fixture.py`, with an isolated `HOME` and `CLAUDE_CONFIG_DIR`, the workspace under that `HOME`, and `--setting-sources user`. `.github/workflows/mcp-contract.yml` runs it beside `mcp_contract.py`: against the pinned build when the contract or its fixtures change, and against the newest published build every day.

Three scenarios launch the session the way a room's central session is launched: `client.prepare` builds the plugin (`proxy.js`), the shell prefix and the PreToolUse guard, and the executor is the acceptance fixture's (`acceptance.setup`, `runtime.py` over a project from `seed.py`). Two things differ from a room. The forwarded project view is a FUSE mount the fixture does not have, so the session sees an empty directory at the executor's path, and the version pin is relaxed so the daily job can run the newest build. No check reads either; `equivalence.py` is the one that mounts the view. The resume checks drive the interactive CLI in a tmux pane on its own server (`tmux -L`).

| Pinned behaviour | Why the runner needs it |
| --- | --- |
| `control_request` on stdin answers `initialize`, `interrupt`, `background_tasks`, `stop_task`, `set_model`, `set_permission_mode`, `set_max_thinking_tokens`, `apply_flag_settings`, `rename_session`, `file_suggestions`, `read_file`, `get_workspace_diff`, `get_context_usage`, `get_usage`, `mcp_status` and `mcp_reconnect`; `set_model` changes the model the next turn requests | The room's controls are written to stdin |
| `background_tasks` with a running tool's `tool_use_id`, or with no id, moves a foreground Bash or Agent to the background: `task_updated` reports `is_backgrounded: true`, `background_tasks_changed` is emitted, and the turn's `result` arrives while the task is still running. On completion, a `task_notification` with the task's `tool_use_id` starts a follow-up turn, which ends in its own `result` | Move-to-background, and tracking background work until it finishes |
| `stop_task` fails a foreground tool (`is_error` tool result) and the turn continues; on a background task it produces a `task_notification` with status `stopped`. `interrupt` ends the turn with an `error_during_execution` result | Stop and interrupt |
| A user message written to stdin while a tool runs reaches the model inside the next `tool_result`, as "The user sent a new message while you were working". `--replay-user-messages` echoes it (`isReplay`) only after that tool result | Mid-turn messages, and the echo as their delivery receipt |
| An Agent call starts as a background task unless `run_in_background: false`. Its tool result carries `agentId`, and its messages also appear on stdout with `parent_tool_use_id` set. Its transcript is `subagents/agent-<agentId>.jsonl` beside a `.meta.json`; a workflow's agents are under `subagents/workflows/wf_*/` | Attaching subagent actions to their card |
| Hooks in `$CLAUDE_CONFIG_DIR/settings.json` fire in `-p` mode: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop and SessionEnd | The hooks the platform keeps (agent sync, the PreToolUse guard) still run |
| A turn ends in one `result`, and `is_error` marks failure. A 401 from the API is first reported as `system/api_retry` with `error_status: 401`, and is retried (10 times by default). The turn then ends with `is_error: true`, `terminal_reason: api_error` and `subtype: success`, after an assistant message whose `error` is `authentication_failed`. The process survives | Turn end and failure come from `is_error`, never from `subtype` |
| After a `result`, the process keeps reading stdin and background tasks keep running. Closing stdin exits with status 0 and kills its background tasks | Session lifetime is the runner holding stdin |
| `-p --resume <id>` continues a transcript the interactive CLI wrote in the same cwd and `CLAUDE_CONFIG_DIR`: the model sees the earlier turns, `init` reports the same session id, and the new turns are appended to the same file. Interactive `--resume` continues a transcript `-p` wrote in the same way | A room's existing transcript continues under the runner, and a session can still be opened by hand |
| Under `-p` with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`, the plugin and the shell prefix, Bash runs on the executor in its workspace and environment, and Write lands in the executor workspace. `CLAUDE_CODE_SHELL_PREFIX` is applied to hook commands: a platform hook runs centrally, a command the prefix does not know runs on the executor. With function hooks off, the PreToolUse guard denies the native call (`the remote execution plugin did not handle this call`) and the turn continues | Remote execution works unchanged under `-p` |
| A Bash that runs on the executor through the shell prefix is a harness task: `task_started` reports it, `background_tasks` on stdin with its `tool_use_id` moves it while it keeps running there, and its completion produces a `task_notification` and a follow-up turn | Move-to-background, stop and completion of a room's commands are the build's own, with nothing for the runner to watch |
| `/reload-skills` and `/reload-plugins` sent as user messages run as commands: the model is not asked, the turn ends in a `result` whose text reports the reload (`Reloaded skills: …`, `Reloaded: 1 plugin · …`), `commands_changed` and a new `init` precede it, and the next turn offers the skill or plugin skill added after launch. Both are listed in `init.slash_commands` | Resident release reloads plugins by writing `/reload-plugins` to stdin |
| A subagent's own Agent call starts a nested agent, reported as `task_started` and `task_notification` for that call. The nested agent's messages are not on stdout, only its parent subagent's; its transcript is `subagents/agent-<agentId>.jsonl` beside its parent's. A workflow agent's messages are not on stdout either. The workflow's `task_progress` names each agent's `agentId` in `workflow_progress`, and its transcript is `subagents/workflows/wf_*/agent-<agentId>.jsonl` | Nested and workflow agents reach their card only by the runner tailing their files into the journal |
| With `--disallowedTools AskUserQuestion` the tool is not offered. A call to it anyway is a `No such tool available` tool error, and the turn continues and ends with `is_error: false` (that half is `backend/tests/unit/test_claude_runner.py`'s, against the pinned build) | Questions go through `cheese ask`, and disallowing the tool is safe |
| With `--permission-mode bypassPermissions`, no `--permission-prompt-tool` and AskUserQuestion disallowed, Bash, Write under `.claude/` and outside the workspace, WebFetch and an MCP tool all run, and no `control_request` arrives | The runner never has to answer a permission request |
| A Bash printing 1 MB puts at most 30,000 characters of it on stdout, in `tool_use_result.stdout`, beside `persistedOutputPath` and `persistedOutputSize`; the model sees a 2 KB preview. Measured on 2.1.277: 38 KB of stdout for that turn (33 KB in the tool-result event), 40 KB when the plugin runs it on the executor, against 5 KB for an `echo` | The journal grows by tens of KB per large tool output, not by the output |

When a check fails against a new release, the runner needs to change before that release is pinned.

## Boundaries

The central session mirrors root project instructions and `.claude` assets. Project command hooks execute on the remote host; platform hooks execute centrally. For the calls the build runs itself, Bash among them, the build fires the project's PreToolUse and PostToolUse hooks with its own input and the shell prefix runs them on the executor. For the file tools the plugin runs, the executor runs a project's hooks as plain Claude Code does: in the shell's working directory with `CLAUDE_PROJECT_DIR` set to the project root, blocking the call on exit 2 or a `deny` decision, whose reason reaches the model as the tool's error, and letting it run on any other failing exit (`backend/tests/unit/test_a_projects_own_hooks_hold_in_a_room.py`). Native execution is denied centrally when the function hook cannot return a remote result. Completed tool requests retain their receipts across executor restarts. A request interrupted before its receipt is stored has an unknown outcome and is not replayed automatically.

The current acceptance covers root instructions, imported instruction files and project skills. Nested instruction discovery, attachments, agent teams, non-command hooks and MCP sampling or elicitation are outside this acceptance. Custom MCP configuration currently accepts stdio servers. Subagents run their tools on the executor like the main thread; a subagent requested with `isolation` is refused centrally, because both modes build on the session host; the refusal tells the agent to omit isolation, and to create a tracked, reviewed task with `cheese task` only when the work is itself a deliverable. The experiment is not a security boundary for hostile plugins or an untrusted central process.

Each execution state directory belongs to one room resource generation. The remote service listens on a Unix socket restricted to its OS user. On Windows it listens on a loopback TCP port instead, and writes the port and a token to `executor.endpoint` in its state directory; a connection must send that token as its first line, and then speaks the same protocol. Application rooms use the device connector; standalone acceptance can use SSH. Stop a standalone service with `python3 /srv/tools/runtime.py stop --state /srv/cheese-session`; it stops running shell process groups (on Windows, process trees) and MCP subprocesses.
