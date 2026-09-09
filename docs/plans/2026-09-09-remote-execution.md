# Remote execution experiment

Goal: Run the interactive Claude Code process on a central host while project file operations, shell commands and custom MCP servers execute on a separate host. Keep this work on an experimental branch; do not deploy or merge it.

Architecture: A persistent executor owns the remote workspace and command processes. A Claude Code function hook forwards native tool calls through MCP. The same executor answers file and task controls from Cheese RC. The central session receives the project context needed by Claude Code without installing integration files in the hosted repository.

Tech stack: Python standard library, SSH, Claude Code function hooks and MCP, pytest, GitHub Actions.

## Implementation

1. Add `backend/app/domain/agent/harness/claude_code/remote_execution/` with an executor, an MCP transport, a function hook plugin and a central session launcher. Reuse the existing launch and RC boundaries. Keep session state outside the hosted repository.
2. Implement persistent shell working directories, environment propagation, background output and process-group cancellation. Record request identities so reconnection cannot execute an accepted write twice. Proxy custom MCP servers using their configured remote environment.
3. Route RC file previews, diffs and task controls to the executor. Load project instructions and skills from the remote workspace. Block native execution on the central host if the plugin fails.
4. Add functional tests and an end-to-end runner that uses the real Claude Code terminal, deterministic model responses and a separate execution process. Repeat the complete path across two physical hosts and with a real model.
5. Add `.github/workflows/remote-execution.yml` to run the acceptance suite against the pinned Claude Code build. Save timestamped test logs, inputs, terminal output and results as artifacts. Push the experimental branch and verify its CI result.

## Acceptance

The suite must prove remote Read/Edit/Write/Bash and custom MCP execution; unchanged central sentinel files; shell cwd and environment continuity; background output; cancellation of remote descendants; reconnection without duplicate writes; explicit errors when execution is unavailable; remote project instructions and skills in model context; RC file previews and diffs matching remote edits; and continued use of the native terminal. A passing unit suite alone does not establish completion.

The final report links the branch, exact tested commit, CI run and cross-host evidence, and identifies any unverified case. Completion requires every acceptance case to pass.
